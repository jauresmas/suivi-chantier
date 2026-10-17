from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy import select, text

from app.auth.securite import empreinte
from app.models import Chantier, Invitation, MembreChantier, RoleMembre, User
from tests.outils import (
    MOT_DE_PASSE,
    MOTIF_LIEN_INVITATION,
    creer_utilisateur,
    position_confirmee,
    poster,
    se_connecter,
)


@pytest.fixture
def donnees(session):
    """Chantier A (propriétaire + chef) et chantier B (un autre propriétaire)."""
    proprio = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    chef = creer_utilisateur(session, "chef@exemple.test", "Rodrigue Hounkpatin")
    autre = creer_utilisateur(session, "autre@exemple.test", "Serge Dossou")
    chantier = Chantier(nom="Villa Calavi", **position_confirmee(2.35, 6.45))
    chantier_b = Chantier(nom="Immeuble Cotonou", **position_confirmee(2.377, 6.354))
    session.add_all([chantier, chantier_b])
    session.flush()
    session.add_all(
        [
            MembreChantier(
                chantier_id=chantier.id, user_id=proprio.id, role=RoleMembre.PROPRIETAIRE
            ),
            MembreChantier(chantier_id=chantier.id, user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
            MembreChantier(
                chantier_id=chantier_b.id, user_id=autre.id, role=RoleMembre.PROPRIETAIRE
            ),
        ]
    )
    session.flush()
    return SimpleNamespace(
        proprio=proprio, chef=chef, autre=autre, chantier=chantier, chantier_b=chantier_b
    )


def creer_invitation(navigateur, chantier_id, role="chef_chantier"):
    """Le navigateur (connecté) crée une invitation ; renvoie (réponse, jeton ou None)."""
    reponse = poster(
        navigateur,
        f"/chantiers/{chantier_id}/invitations",
        {"role": role},
        page=f"/chantiers/{chantier_id}",
    )
    trouve = MOTIF_LIEN_INVITATION.search(reponse.text)
    return reponse, trouve.group(1) if trouve else None


# --- Matrice d'accès sur /chantiers/{id} --------------------------------------------------------


def test_non_connecte_redirige_vers_connexion(navigateur, donnees):
    url = f"/chantiers/{donnees.chantier.id}"

    reponse = navigateur.get(url, follow_redirects=False)

    assert reponse.status_code == 303
    assert reponse.headers["location"] == f"/connexion?suivant={quote(url, safe='')}"


@pytest.mark.parametrize("qui, code_attendu", [("proprio", 200), ("chef", 200), ("autre", 404)])
def test_matrice_acces_chantier(navigateur, donnees, qui, code_attendu):
    se_connecter(navigateur, getattr(donnees, qui).email)

    reponse = navigateur.get(f"/chantiers/{donnees.chantier.id}")

    assert reponse.status_code == code_attendu
    if code_attendu == 200:
        assert "Villa Calavi" in reponse.text
        assert "Rodrigue Hounkpatin" in reponse.text  # liste des membres


def test_identifiant_invalide_ou_inexistant_donne_404(navigateur, donnees):
    se_connecter(navigateur, donnees.proprio.email)

    assert navigateur.get("/chantiers/pas-un-uuid").status_code == 404
    assert navigateur.get("/chantiers/00000000-0000-0000-0000-000000000000").status_code == 404


def test_liste_ne_montre_que_mes_chantiers(navigateur, donnees):
    se_connecter(navigateur, donnees.autre.email)

    page = navigateur.get("/chantiers").text

    assert "Immeuble Cotonou" in page
    assert "Villa Calavi" not in page


# --- Création d'invitations ---------------------------------------------------------------------


def test_seul_le_proprietaire_cree_une_invitation(navigateurs, donnees):
    codes = {}
    for qui in ("proprio", "chef", "autre"):
        navigateur = navigateurs()
        se_connecter(navigateur, getattr(donnees, qui).email)
        reponse = poster(
            navigateur,
            f"/chantiers/{donnees.chantier.id}/invitations",
            {"role": "chef_chantier"},
            page="/chantiers",
        )
        codes[qui] = reponse.status_code

    # Le chef connaît le chantier : 403. L'autre propriétaire ne doit même pas savoir qu'il existe :
    # 404.
    assert codes == {"proprio": 200, "chef": 403, "autre": 404}


def test_jeton_invitation_jamais_stocke_en_clair(navigateur, donnees, session):
    se_connecter(navigateur, donnees.proprio.email)

    reponse, jeton = creer_invitation(navigateur, donnees.chantier.id)

    assert reponse.status_code == 200 and jeton
    assert "https://wa.me/?text=" in reponse.text
    invitation = session.scalar(
        select(Invitation).where(Invitation.chantier_id == donnees.chantier.id)
    )
    assert invitation.empreinte_jeton == empreinte(jeton)
    ligne_complete = session.scalar(
        text("SELECT i::text FROM invitations i WHERE id = :id"), {"id": invitation.id}
    )
    assert jeton not in ligne_complete


# --- Acceptation d'une invitation ---------------------------------------------------------------


def test_parcours_complet_invitation_puis_inscription_du_chef(navigateurs, donnees, session):
    proprio = navigateurs()
    se_connecter(proprio, donnees.proprio.email)
    _, jeton = creer_invitation(proprio, donnees.chantier.id)

    # Fenêtre de navigation privée : la personne invitée n'a pas de compte.
    invite = navigateurs()
    page = invite.get(f"/invitations/{jeton}")
    assert page.status_code == 200 and "Villa Calavi" in page.text
    assert "Créer un compte" in page.text

    inscription = poster(
        invite,
        "/inscription",
        {
            "nom_complet": "Nouveau Chef",
            "email": "nouveau@exemple.test",
            "mot_de_passe": MOT_DE_PASSE,
            "suivant": f"/invitations/{jeton}",
        },
        page=f"/inscription?suivant=/invitations/{jeton}",
    )
    assert inscription.headers["location"] == f"/invitations/{jeton}"
    assert "Rejoindre le chantier" in invite.get(f"/invitations/{jeton}").text

    acceptation = poster(invite, f"/invitations/{jeton}/accepter", page=f"/invitations/{jeton}")
    assert acceptation.status_code == 303
    assert acceptation.headers["location"] == f"/chantiers/{donnees.chantier.id}"

    nouveau = session.scalar(select(User).where(User.email == "nouveau@exemple.test"))
    membre = session.get(MembreChantier, (donnees.chantier.id, nouveau.id))
    assert membre.role == RoleMembre.CHEF_CHANTIER
    invitation = session.scalar(
        select(Invitation).where(Invitation.empreinte_jeton == empreinte(jeton))
    )
    assert invitation.utilisee_le is not None and invitation.utilisee_par == nouveau.id
    assert invite.get(f"/chantiers/{donnees.chantier.id}").status_code == 200


def test_invitation_deja_utilisee_refusee(navigateurs, donnees, session):
    proprio = navigateurs()
    se_connecter(proprio, donnees.proprio.email)
    _, jeton = creer_invitation(proprio, donnees.chantier.id)
    premier = navigateurs()
    creer_utilisateur(session, "premier@exemple.test")
    se_connecter(premier, "premier@exemple.test")
    poster(premier, f"/invitations/{jeton}/accepter", page=f"/invitations/{jeton}")

    # Le même lien, transféré à quelqu'un d'autre :
    second = navigateurs()
    se_connecter(second, donnees.autre.email)
    page = second.get(f"/invitations/{jeton}")
    acceptation = poster(second, f"/invitations/{jeton}/accepter", page="/chantiers")

    assert page.status_code == 410 and "déjà été utilisé" in page.text
    assert acceptation.status_code == 410
    assert session.get(MembreChantier, (donnees.chantier.id, donnees.autre.id)) is None


def test_invitation_expiree_refusee(navigateurs, donnees, session):
    proprio = navigateurs()
    se_connecter(proprio, donnees.proprio.email)
    _, jeton = creer_invitation(proprio, donnees.chantier.id)
    invitation = session.scalar(
        select(Invitation).where(Invitation.empreinte_jeton == empreinte(jeton))
    )
    invitation.expire_le = datetime.now(UTC) - timedelta(minutes=1)
    session.flush()

    invite = navigateurs()
    se_connecter(invite, donnees.autre.email)
    page = invite.get(f"/invitations/{jeton}")
    acceptation = poster(invite, f"/invitations/{jeton}/accepter", page="/chantiers")

    assert page.status_code == 410 and "expiré" in page.text
    assert acceptation.status_code == 410
    assert session.get(MembreChantier, (donnees.chantier.id, donnees.autre.id)) is None


def test_jeton_inconnu_refuse(navigateur, donnees):
    se_connecter(navigateur, donnees.autre.email)
    jeton_invente = "x" * 43

    page = navigateur.get(f"/invitations/{jeton_invente}")
    acceptation = poster(navigateur, f"/invitations/{jeton_invente}/accepter", page="/chantiers")

    assert page.status_code == 404 and "pas valide" in page.text
    assert acceptation.status_code == 404
