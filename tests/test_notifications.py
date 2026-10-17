"""Notifications par e-mail, préférences, désinscription et réinitialisation du mot de passe.

Les e-mails sont gardés en mémoire (fixture boite_mail de conftest.py) : aucun envoi réel.
"""

import html
import logging
import re
import smtplib
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import taches
from app.auth.securite import empreinte
from app.emails import envoi
from app.emails.envoi import renvoyer_echoues
from app.emails.expediteurs import ExpediteurConsole, ExpediteurSmtp, Message, construire, journal
from app.emails.notifications import (
    envoyer_resumes_quotidiens,
    expirer_et_notifier_releves,
    verifier_inactivite,
)
from app.emails.preferences import enregistrer_mode, jeton_desinscription, mode_de
from app.models import (
    Chantier,
    EmailEnvoye,
    Etape,
    JetonReinitialisation,
    MembreChantier,
    ModeNotification,
    Photo,
    Rapport,
    RoleMembre,
    SessionUtilisateur,
    StatutChantier,
    StatutEmail,
    StatutRapport,
    StatutReleve,
    StatutVerification,
    TypeEmail,
    TypeEtape,
)
from tests.outils import (
    MOTIF_LIEN_INVITATION,
    creer_utilisateur,
    position_estimee,
    poster,
    se_connecter,
)
from tests.test_releve_position import envoyer_mesures, mesures_simulees, nouveau_lien

RACINE = Path(__file__).resolve().parent.parent
MOTIF_LIEN_REINITIALISATION = re.compile(r"/reinitialiser/([A-Za-z0-9_\-]{43})")
FORMULAIRE_CHANTIER = {
    "nom": "Villa Calavi",
    "pays": "BJ",
    "devise": "XOF",
    "statut": "en_cours",
    "rayon_verification_m": "100",
}


def maintenant() -> datetime:
    return datetime.now(UTC)


# Les routes valident (commit) avant d'envoyer : les données préparées sont donc validées aussi,
# la transaction englobante est annulée en fin de test (voir conftest.py).
@pytest.fixture
def chantier(session):
    proprio = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    associe = creer_utilisateur(session, "associe@exemple.test", "Koffi Adjovi")
    chef = creer_utilisateur(session, "chef@exemple.test", "Rodrigue Hounkpatin")
    chef.telephone = "+229 01 90 00 00 02"
    chantier = Chantier(
        nom="Villa Calavi", **position_estimee(2.35, 6.45), statut=StatutChantier.EN_COURS
    )
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=associe.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    fondations = Etape(chantier=chantier, type=TypeEtape.FONDATIONS, ordre=1, avancement_pct=30)
    session.add_all([chantier, fondations])
    session.commit()
    return SimpleNamespace(
        chantier=chantier, proprio=proprio, associe=associe, chef=chef, fondations=fondations
    )


def brouillon(session, chantier, auteur=None, photos=()) -> Rapport:
    """Brouillon avec des photos déjà vérifiées : (statut, distance en m, alertes)."""
    rapport = Rapport(chantier_id=chantier.chantier.id, auteur_id=(auteur or chantier.chef).id)
    session.add(rapport)
    for statut, distance, alertes in photos:
        session.add(
            Photo(
                rapport=rapport,
                chemin_fichier="test.jpg",
                statut_verification=statut,
                distance_chantier_m=distance,
                alertes=list(alertes),
            )
        )
    session.commit()
    return rapport


def publier(client, rapport, chantier, **champs):
    donnees = {
        "texte": "Semelles coulées.",
        "etape_id": str(chantier.fondations.id),
        "avancement_pct": "40",
        **champs,
    }
    return poster(client, f"/rapports/{rapport.id}/publier", donnees, page="/chantiers")


def rapport_publie(session, chantier, il_y_a: timedelta, texte="Rapport") -> Rapport:
    rapport = Rapport(
        chantier_id=chantier.chantier.id,
        auteur_id=chantier.chef.id,
        texte=texte,
        statut=StatutRapport.PUBLIE,
        publie_le=maintenant() - il_y_a,
        cree_le=maintenant() - il_y_a,
    )
    session.add(rapport)
    session.commit()
    return rapport


def connecte(navigateurs, user):
    client = navigateurs()
    se_connecter(client, user.email)
    return client


def emails_en_base(session, type_: TypeEmail) -> list[EmailEnvoye]:
    return list(
        session.scalars(
            select(EmailEnvoye).where(EmailEnvoye.type == type_).order_by(EmailEnvoye.destinataire)
        )
    )


# --- Rapport publié
# --------------------------------------------------------------------------------


def test_publication_un_email_par_proprietaire_en_mode_immediat_aucun_en_mode_aucun(
    navigateurs, session, chantier, boite_mail
):
    enregistrer_mode(session, chantier.associe.id, chantier.chantier.id, ModeNotification.AUCUN)
    session.commit()
    rapport = brouillon(session, chantier, photos=[(StatutVerification.VERIFIEE, 30, [])])

    reponse = publier(connecte(navigateurs, chantier.chef), rapport, chantier)

    assert reponse.status_code == 303
    assert [m.destinataire for m in boite_mail.envoyes] == ["proprio@exemple.test"]
    email = boite_mail.envoyes[0]
    assert email.sujet == "Villa Calavi — nouveau rapport : Fondations 40 %"
    assert "Semelles coulées." in email.texte and "1 photo : 1 sur le chantier" in email.texte
    assert f"/rapports/{rapport.id}" in email.html and "Voir le rapport" in email.html
    assert "<img" not in email.html  # aucune photo intégrée
    assert email.url_desinscription and email.url_desinscription in email.texte
    assert "Gérer mes notifications" in email.html
    (journal_email,) = emails_en_base(session, TypeEmail.RAPPORT_PUBLIE)
    assert (journal_email.statut, journal_email.tentatives) == (StatutEmail.ENVOYE, 1)
    assert journal_email.envoye_le is not None


def test_le_chef_auteur_ne_recoit_rien_pour_son_rapport(navigateurs, session, chantier, boite_mail):
    publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)

    assert boite_mail.pour("chef@exemple.test") == []
    assert len(boite_mail.envoyes) == 2  # les deux propriétaires


def test_un_proprietaire_auteur_ne_recoit_rien_pour_son_rapport(
    navigateurs, session, chantier, boite_mail
):
    publier(
        connecte(navigateurs, chantier.proprio),
        brouillon(session, chantier, auteur=chantier.proprio),
        chantier,
    )

    assert [m.destinataire for m in boite_mail.envoyes] == ["associe@exemple.test"]


def test_photo_hors_zone_avertissement_en_tete(navigateurs, session, chantier, boite_mail):
    rapport = brouillon(
        session,
        chantier,
        photos=[
            (StatutVerification.VERIFIEE, 30, []),
            (StatutVerification.HORS_ZONE, 2000, []),
            (StatutVerification.VERIFIEE, 40, ["doublon"]),
            (
                StatutVerification.NON_VERIFIABLE,
                None,
                ["position_exif"],
            ),  # information, pas suspicion
        ],
    )

    publier(connecte(navigateurs, chantier.chef), rapport, chantier)

    email = boite_mail.pour("proprio@exemple.test")[0]
    for corps in (email.texte, email.html):
        avertissement = corps.index("1 photo prise hors zone, à 2,0 km du chantier")
        assert avertissement < corps.index("Nouveau rapport sur")
        assert "1 photo à vérifier : Cette photo a déjà été envoyée" in corps
    assert "4 photos : 2 sur le chantier, 1 hors zone, 1 non vérifiable" in email.texte
    assert (
        "la vérification des photos reste indicative" in email.texte
    )  # position seulement estimée


def test_resume_quotidien_regroupe_trois_rapports_en_un_email(
    navigateurs, session, chantier, boite_mail
):
    for user in (chantier.proprio, chantier.associe):
        enregistrer_mode(session, user.id, chantier.chantier.id, ModeNotification.RESUME_QUOTIDIEN)
    session.commit()
    publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)
    assert boite_mail.envoyes == []  # rien sur le moment en mode résumé
    rapport_publie(session, chantier, timedelta(hours=5), "Ferraillage des longrines.")
    rapport_publie(session, chantier, timedelta(hours=20), "Livraison du ciment.")
    rapport_publie(session, chantier, timedelta(hours=30), "Trop ancien pour ce résumé.")

    assert envoyer_resumes_quotidiens(session) == 2

    email = boite_mail.pour("proprio@exemple.test")[0]
    assert email.sujet == "Villa Calavi — résumé du jour : 3 rapports"
    for texte in ("Semelles coulées.", "Ferraillage des longrines.", "Livraison du ciment."):
        assert texte in email.texte
    assert "Trop ancien" not in email.texte
    # Relancée le même jour, la commande n'envoie rien de plus.
    assert envoyer_resumes_quotidiens(session) == 0
    assert len(boite_mail.envoyes) == 2


# --- Inactivité
# ------------------------------------------------------------------------------------


def test_inactivite_un_seul_email_meme_si_la_commande_tourne_plusieurs_jours(
    session, chantier, boite_mail
):
    chantier.chantier.cree_le = maintenant() - timedelta(days=10)
    session.commit()

    for jour in range(4):
        verifier_inactivite(session, maintenant() + timedelta(days=jour))

    assert sorted(m.destinataire for m in boite_mail.envoyes) == [
        "associe@exemple.test",
        "proprio@exemple.test",
    ]
    email = boite_mail.pour("proprio@exemple.test")[0]
    assert email.sujet == "Villa Calavi — aucune nouvelle depuis 10 jours"
    assert "Rodrigue Hounkpatin : +229 01 90 00 00 02" in email.texte
    assert 'href="tel:+2290190000002"' in email.html


def test_inactivite_nouvelle_periode_apres_un_rapport(session, chantier, boite_mail):
    chantier.chantier.cree_le = maintenant() - timedelta(days=30)
    session.commit()
    verifier_inactivite(session)
    rapport_publie(session, chantier, timedelta(0))  # publié après l'alerte
    assert verifier_inactivite(session) == 0  # chantier actif

    assert verifier_inactivite(session, maintenant() + timedelta(days=8)) == 2  # nouvelle période
    assert len(boite_mail.envoyes) == 4


def test_pas_d_alerte_d_inactivite_pour_un_chantier_termine_ou_un_proprietaire_desabonne(
    session, chantier, boite_mail
):
    chantier.chantier.cree_le = maintenant() - timedelta(days=10)
    enregistrer_mode(session, chantier.associe.id, chantier.chantier.id, ModeNotification.AUCUN)
    session.commit()
    verifier_inactivite(session)
    assert [m.destinataire for m in boite_mail.envoyes] == ["proprio@exemple.test"]

    autre = Chantier(
        nom="Maison livrée",
        statut=StatutChantier.TERMINE,
        cree_le=maintenant() - timedelta(days=60),
    )
    autre.membres.append(MembreChantier(user_id=chantier.proprio.id, role=RoleMembre.PROPRIETAIRE))
    session.add(autre)
    session.commit()
    assert verifier_inactivite(session) == 0


# --- Relevés de position
# ---------------------------------------------------------------------------


def test_releve_recu_email_immediat_meme_en_mode_resume_quotidien(
    navigateur, session, chantier, boite_mail
):
    enregistrer_mode(
        session, chantier.proprio.id, chantier.chantier.id, ModeNotification.RESUME_QUOTIDIEN
    )
    enregistrer_mode(session, chantier.associe.id, chantier.chantier.id, ModeNotification.AUCUN)
    session.commit()
    releve, jeton = nouveau_lien(session, chantier)

    assert envoyer_mesures(navigateur, jeton, mesures_simulees()).status_code == 201

    (email,) = boite_mail.envoyes
    assert email.destinataire == "proprio@exemple.test"
    assert email.sujet == "Villa Calavi — Tante Afi a relevé la position de votre terrain"
    assert f"/position/releves/{releve.id}" in email.html
    assert re.search(
        r"Le relevé est à \d+ m de la position que vous aviez indiquée", email.texte
    )  # ~1 km > 500 m
    assert email.texte.index("RELEVÉ ÉLOIGNÉ") < email.texte.index("a relevé la position")


def test_releve_expire_statut_et_email_a_l_auteur_de_la_demande(session, chantier, boite_mail):
    releve, _ = nouveau_lien(session, chantier)
    releve.expire_le = maintenant() - timedelta(minutes=1)
    session.commit()

    assert expirer_et_notifier_releves(session) == 1

    session.refresh(releve)
    assert releve.statut == StatutReleve.EXPIRE
    (email,) = boite_mail.envoyes
    assert (
        email.destinataire == "proprio@exemple.test"
    )  # auteur de la demande, pas l'autre propriétaire
    assert email.sujet == "Villa Calavi — le lien de relevé envoyé à Tante Afi a expiré"
    assert f"/chantiers/{chantier.chantier.id}/position/demander" in email.html
    assert expirer_et_notifier_releves(session) == 0  # déjà traité


# --- Position modifiée (sécurité)
# ------------------------------------------------------------------


def test_position_modifiee_email_a_tous_les_proprietaires_meme_en_mode_aucun(
    navigateurs, session, chantier, boite_mail
):
    for user in (chantier.proprio, chantier.associe):
        enregistrer_mode(session, user.id, chantier.chantier.id, ModeNotification.AUCUN)
    session.commit()

    reponse = poster(
        connecte(navigateurs, chantier.proprio),
        f"/chantiers/{chantier.chantier.id}/modifier",
        {**FORMULAIRE_CHANTIER, "coordonnees": "6.4509, 2.35"},
        page="/chantiers",
    )

    assert reponse.status_code == 303
    assert sorted(m.destinataire for m in boite_mail.envoyes) == [
        "associe@exemple.test",
        "proprio@exemple.test",
    ]
    email = boite_mail.pour("associe@exemple.test")[0]
    assert email.sujet == "Villa Calavi — la position du chantier a été modifiée"
    for attendu in (
        "Modifiée par : Mireille Adjovi",
        "Ancienne position : 6.450000, 2.350000",
        "Nouvelle position : 6.450900, 2.350000",
        "Déplacement : 100 m",
        "Position estimée sur la carte",
    ):
        assert attendu in email.texte, attendu
    assert (
        f"/chantiers/{chantier.chantier.id}/position" in email.html
        and "Voir l'historique" in html.unescape(email.html)
    )
    # E-mail de sécurité : ni désinscription ni réglage.
    assert email.url_desinscription is None
    assert "desinscription" not in email.html and "Gérer mes notifications" not in email.html


def test_validation_d_un_releve_previent_aussi_tous_les_proprietaires(
    navigateurs, session, chantier, boite_mail
):
    proche, proprio = navigateurs(), connecte(navigateurs, chantier.proprio)
    releve, jeton = nouveau_lien(session, chantier)
    envoyer_mesures(proche, jeton, mesures_simulees())
    boite_mail.envoyes.clear()

    poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/valider",
        page="/chantiers",
    )

    assert len(boite_mail.envoyes) == 2
    assert "Source : Relevé sur place par un proche (Tante Afi)" in boite_mail.envoyes[0].texte


def test_pas_d_email_de_position_a_la_creation_du_chantier(navigateur, session, boite_mail):
    user = creer_utilisateur(session, "nouveau@exemple.test")
    se_connecter(navigateur, user.email)

    poster(
        navigateur,
        "/chantiers/nouveau",
        {**FORMULAIRE_CHANTIER, "coordonnees": "6.45, 2.35"},
        page="/chantiers/nouveau",
    )

    assert boite_mail.envoyes == []


# --- Invitation acceptée
# ---------------------------------------------------------------------------


def test_invitation_acceptee_email_a_son_auteur(navigateurs, session, chantier, boite_mail):
    proprio = connecte(navigateurs, chantier.proprio)
    reponse = poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/invitations",
        {"role": "chef_chantier"},
        page="/chantiers",
    )
    jeton = MOTIF_LIEN_INVITATION.search(reponse.text).group(1)
    nouveau = creer_utilisateur(session, "macon@exemple.test", "Paul Dossou")
    session.commit()

    poster(connecte(navigateurs, nouveau), f"/invitations/{jeton}/accepter", page="/chantiers")

    (email,) = boite_mail.envoyes
    assert email.destinataire == "proprio@exemple.test"
    assert email.sujet == "Villa Calavi — Paul Dossou a rejoint le chantier"
    assert "comme chef de chantier" in email.texte


# --- Échecs d'envoi et nouvelles tentatives
# --------------------------------------------------------


def test_echec_smtp_la_publication_reussit_quand_meme(navigateurs, session, chantier, boite_mail):
    boite_mail.panne = smtplib.SMTPServerDisconnected("Connexion fermée par le serveur")
    rapport = brouillon(session, chantier)

    reponse = publier(connecte(navigateurs, chantier.chef), rapport, chantier)

    assert reponse.status_code == 303
    session.refresh(rapport)
    assert rapport.statut == StatutRapport.PUBLIE
    emails = emails_en_base(session, TypeEmail.RAPPORT_PUBLIE)
    assert [(e.statut, e.tentatives) for e in emails] == [(StatutEmail.ECHEC, 1)] * 2
    assert "SMTPServerDisconnected" in emails[0].derniere_erreur


def test_renvoyer_les_emails_echoues_trois_tentatives_au_plus(
    navigateurs, session, chantier, boite_mail
):
    boite_mail.panne = smtplib.SMTPServerDisconnected("indisponible")
    publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)

    assert renvoyer_echoues(session) == (0, 2)  # 2e tentative, toujours en panne
    assert renvoyer_echoues(session) == (0, 2)  # 3e tentative
    assert renvoyer_echoues(session) == (0, 0)  # plus rien à tenter
    boite_mail.panne = None
    assert renvoyer_echoues(session) == (0, 0)
    assert {e.tentatives for e in emails_en_base(session, TypeEmail.RAPPORT_PUBLIE)} == {3}


def test_renvoi_reussi_apres_une_panne(
    navigateurs, session, chantier, boite_mail, monkeypatch, capsys
):
    boite_mail.panne = smtplib.SMTPServerDisconnected("indisponible")
    publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)
    boite_mail.panne = None
    monkeypatch.setattr(taches, "SessionLocal", lambda: nullcontext(session))

    taches.main(["renvoyer_emails_echoues"])

    assert "2 e-mails renvoyé(s), 0 toujours en échec." in capsys.readouterr().out
    emails = emails_en_base(session, TypeEmail.RAPPORT_PUBLIE)
    assert [(e.statut, e.tentatives, e.derniere_erreur) for e in emails] == [
        (StatutEmail.ENVOYE, 2, None)
    ] * 2
    assert "Villa Calavi — nouveau rapport" in boite_mail.envoyes[0].sujet


def test_commandes_planifiees(session, chantier, monkeypatch, capsys):
    chantier.chantier.cree_le = maintenant() - timedelta(days=10)
    session.commit()
    monkeypatch.setattr(taches, "SessionLocal", lambda: nullcontext(session))

    for commande in ("envoyer_resumes_quotidiens", "verifier_inactivite", "expirer_releves"):
        taches.main([commande])

    sortie = capsys.readouterr().out
    assert "0 résumé envoyé(s)." in sortie
    assert "2 alertes d'inactivité envoyée(s)." in sortie
    assert "0 lien de relevé expiré(s)." in sortie


# --- Préférences et désinscription
# -----------------------------------------------------------------


def test_page_des_preferences(navigateurs, session, chantier):
    proprio = connecte(navigateurs, chantier.proprio)
    page = proprio.get("/compte/notifications")
    assert page.status_code == 200
    assert "Villa Calavi" in page.text and "Un résumé par jour" in page.text

    reponse = poster(
        proprio,
        "/compte/notifications",
        {f"mode_{chantier.chantier.id}": "resume_quotidien"},
        page="/chantiers",
    )

    assert reponse.status_code == 303
    assert (
        mode_de(session, chantier.proprio.id, chantier.chantier.id)
        == ModeNotification.RESUME_QUOTIDIEN
    )
    # Un chef de chantier ne règle rien pour un chantier dont il n'est pas propriétaire.
    chef = connecte(navigateurs, chantier.chef)
    poster(
        chef, "/compte/notifications", {f"mode_{chantier.chantier.id}": "aucun"}, page="/chantiers"
    )
    assert session.scalar(select(func.count()).select_from(EmailEnvoye)) == 0
    assert mode_de(session, chantier.chef.id, chantier.chantier.id) == ModeNotification.IMMEDIAT


def test_lien_de_desinscription_passe_en_mode_aucun_pour_ce_chantier(
    navigateurs, session, chantier, boite_mail
):
    publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)
    lien = boite_mail.pour("proprio@exemple.test")[0].url_desinscription
    chemin = lien.removeprefix("http://localhost:8000")
    anonyme = navigateurs()

    page = anonyme.get(chemin)
    assert page.status_code == 200 and "Me désinscrire de ce chantier" in page.text
    assert (
        mode_de(session, chantier.proprio.id, chantier.chantier.id) == ModeNotification.IMMEDIAT
    )  # GET ne change rien

    # Désinscription en un clic (RFC 8058) : POST de la messagerie, sans session ni jeton CSRF.
    reponse = anonyme.post(chemin, data={"List-Unsubscribe": "One-Click"})

    assert reponse.status_code == 200 and "C'est fait" in reponse.text
    assert mode_de(session, chantier.proprio.id, chantier.chantier.id) == ModeNotification.AUCUN
    assert mode_de(session, chantier.associe.id, chantier.chantier.id) == ModeNotification.IMMEDIAT


def test_lien_de_desinscription_falsifie_refuse(navigateur, session, chantier):
    jeton = jeton_desinscription(chantier.proprio.id, chantier.chantier.id)
    falsifie = (
        jeton_desinscription(chantier.associe.id, chantier.chantier.id).split(".")[0]
        + "."
        + jeton.split(".")[1]
    )

    assert navigateur.get(f"/notifications/desinscription/{falsifie}").status_code == 404
    navigateur.post(f"/notifications/desinscription/{falsifie}")
    assert mode_de(session, chantier.associe.id, chantier.chantier.id) == ModeNotification.IMMEDIAT


def test_en_tetes_de_desinscription_en_un_clic(navigateurs, session, chantier, boite_mail):
    publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)
    email = boite_mail.pour("proprio@exemple.test")[0]

    mime = construire(email, "Suivi Chantier <notifications@exemple.test>")

    assert mime["List-Unsubscribe"] == f"<{email.url_desinscription}>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert [p.get_content_type() for p in mime.iter_parts()] == ["text/plain", "text/html"]
    assert not list(mime.iter_attachments())


# --- Mot de passe oublié
# ---------------------------------------------------------------------------


def demander_lien(client, email: str):
    return poster(client, "/mot-de-passe-oublie", {"email": email}, page="/mot-de-passe-oublie")


def test_mot_de_passe_oublie_meme_reponse_que_le_compte_existe_ou_non(
    navigateur, session, chantier, boite_mail
):
    existe = demander_lien(navigateur, "Proprio@Exemple.test ")
    inconnu = demander_lien(navigateur, "personne@exemple.test")

    assert existe.status_code == inconnu.status_code == 200
    assert existe.text.replace("proprio@exemple.test", "X") == inconnu.text.replace(
        "personne@exemple.test", "X"
    )
    (email,) = boite_mail.envoyes
    assert email.destinataire == "proprio@exemple.test"
    assert email.sujet == "Suivi Chantier — réinitialisation de votre mot de passe"
    assert email.url_desinscription is None


def test_jeton_hache_en_base_et_contenu_non_conserve(navigateur, session, chantier, boite_mail):
    demander_lien(navigateur, "proprio@exemple.test")
    jeton = MOTIF_LIEN_REINITIALISATION.search(boite_mail.envoyes[0].texte).group(1)

    enregistre = session.scalar(select(JetonReinitialisation))
    assert enregistre.empreinte_jeton == empreinte(jeton)
    assert enregistre.expire_le - enregistre.cree_le == timedelta(hours=1)
    (journal_email,) = emails_en_base(session, TypeEmail.MOT_DE_PASSE)
    assert (
        journal_email.corps_texte is None and journal_email.corps_html is None
    )  # le jeton n'est stocké nulle part en clair
    assert journal_email.statut == StatutEmail.ENVOYE


def test_reinitialisation_change_le_mot_de_passe_et_ferme_toutes_les_sessions(
    navigateurs, session, chantier, boite_mail
):
    autre_appareil = connecte(navigateurs, chantier.proprio)
    assert autre_appareil.get("/chantiers", follow_redirects=False).status_code == 200
    client = navigateurs()
    demander_lien(client, "proprio@exemple.test")
    jeton = MOTIF_LIEN_REINITIALISATION.search(boite_mail.envoyes[0].texte).group(1)
    assert client.get(f"/reinitialiser/{jeton}").status_code == 200

    reponse = poster(
        client,
        f"/reinitialiser/{jeton}",
        {"mot_de_passe": "nouveau-secret-2026", "confirmation": "nouveau-secret-2026"},
        page="/connexion",
    )

    assert (
        reponse.status_code == 303
        and reponse.headers["location"] == "/connexion?mot_de_passe=modifie"
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(SessionUtilisateur)
            .where(SessionUtilisateur.user_id == chantier.proprio.id)
        )
        == 0
    )
    assert autre_appareil.get("/chantiers", follow_redirects=False).status_code == 303  # déconnecté
    assert se_connecter(client, "proprio@exemple.test", "nouveau-secret-2026").status_code == 303
    assert (
        se_connecter(navigateurs(), "proprio@exemple.test").status_code == 400
    )  # ancien mot de passe refusé


def test_jeton_reutilise_refuse(navigateur, session, chantier, boite_mail):
    demander_lien(navigateur, "proprio@exemple.test")
    jeton = MOTIF_LIEN_REINITIALISATION.search(boite_mail.envoyes[0].texte).group(1)
    donnees = {"mot_de_passe": "nouveau-secret-2026", "confirmation": "nouveau-secret-2026"}
    poster(navigateur, f"/reinitialiser/{jeton}", donnees, page="/connexion")

    second = poster(
        navigateur,
        f"/reinitialiser/{jeton}",
        {**donnees, "mot_de_passe": "pirate-123456", "confirmation": "pirate-123456"},
        page="/connexion",
    )

    assert second.status_code == 410
    assert "Ce lien n'est plus valable" in html.unescape(second.text)
    assert navigateur.get(f"/reinitialiser/{jeton}").status_code == 410


def test_jeton_expire_refuse(navigateur, session, chantier, boite_mail):
    demander_lien(navigateur, "proprio@exemple.test")
    jeton = MOTIF_LIEN_REINITIALISATION.search(boite_mail.envoyes[0].texte).group(1)
    enregistre = session.scalar(select(JetonReinitialisation))
    enregistre.expire_le = maintenant() - timedelta(seconds=1)
    session.commit()

    assert navigateur.get(f"/reinitialiser/{jeton}").status_code == 410
    reponse = poster(
        navigateur,
        f"/reinitialiser/{jeton}",
        {"mot_de_passe": "nouveau-secret-2026", "confirmation": "nouveau-secret-2026"},
        page="/connexion",
    )
    assert reponse.status_code == 410


def test_mots_de_passe_differents_ou_trop_courts(navigateur, session, chantier, boite_mail):
    demander_lien(navigateur, "proprio@exemple.test")
    jeton = MOTIF_LIEN_REINITIALISATION.search(boite_mail.envoyes[0].texte).group(1)

    court = poster(
        navigateur,
        f"/reinitialiser/{jeton}",
        {"mot_de_passe": "court", "confirmation": "court"},
        page="/connexion",
    )
    different = poster(
        navigateur,
        f"/reinitialiser/{jeton}",
        {"mot_de_passe": "nouveau-secret-2026", "confirmation": "autre-chose-2026"},
        page="/connexion",
    )

    assert court.status_code == different.status_code == 400
    assert "Au moins 8 caractères." in court.text
    assert "ne sont pas identiques" in different.text
    assert navigateur.get(f"/reinitialiser/{jeton}").status_code == 200  # le jeton reste utilisable


def test_trois_demandes_par_email_et_par_heure(navigateur, session, chantier, boite_mail):
    for _ in range(5):
        assert demander_lien(navigateur, "proprio@exemple.test").status_code == 200

    assert len(boite_mail.envoyes) == 3
    assert session.scalar(select(func.count()).select_from(JetonReinitialisation)) == 3


def test_lien_mot_de_passe_oublie_sur_la_page_de_connexion(navigateur):
    assert 'href="/mot-de-passe-oublie"' in navigateur.get("/connexion").text


# --- Expéditeurs et gabarits
# ------------------------------------------------------------------------


def message_exemple(**champs) -> Message:
    return Message(
        type=TypeEmail.RAPPORT_PUBLIE,
        destinataire="proprio@exemple.test",
        sujet="Villa Calavi — nouveau rapport",
        texte="Texte du rapport",
        html="<p>Texte du rapport</p>",
        **champs,
    )


def test_expediteur_console_affiche_l_email_dans_les_journaux(caplog):
    expediteur = ExpediteurConsole()
    journal.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="app.emails"):
            expediteur.envoyer(message_exemple())
    finally:
        journal.removeHandler(caplog.handler)

    assert "Objet : Villa Calavi — nouveau rapport" in caplog.text
    assert "À : proprio@exemple.test" in caplog.text and "Texte du rapport" in caplog.text


class FauxSmtp:
    instances: list["FauxSmtp"] = []

    def __init__(self, hote, port, timeout):
        self.hote, self.port, self.timeout, self.actions = hote, port, timeout, []
        FauxSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.actions.append("ehlo")

    def has_extn(self, nom):
        return nom == "starttls"

    def starttls(self, context):
        self.actions.append("starttls")

    def login(self, utilisateur, mot_de_passe):
        self.actions.append(("login", utilisateur))

    def send_message(self, mime):
        self.actions.append(("envoi", mime["To"], mime["From"]))


def test_expediteur_smtp_chiffre_s_identifie_puis_envoie(monkeypatch):
    FauxSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FauxSmtp)
    expediteur = ExpediteurSmtp(
        "smtp.exemple.test", 587, "compte", "secret", "Suivi Chantier <notifications@exemple.test>"
    )

    expediteur.envoyer(message_exemple())

    (connexion,) = FauxSmtp.instances
    assert (connexion.hote, connexion.port) == ("smtp.exemple.test", 587)
    assert connexion.actions == [
        "ehlo",
        "starttls",
        ("login", "compte"),
        ("envoi", "proprio@exemple.test", "Suivi Chantier <notifications@exemple.test>"),
    ]


def test_un_serveur_qui_refuse_starttls_ne_recoit_pas_les_identifiants(monkeypatch):
    class SansTls(FauxSmtp):
        def has_extn(self, nom):
            return False

    FauxSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", SansTls)

    with pytest.raises(smtplib.SMTPNotSupportedError):
        ExpediteurSmtp("smtp.exemple.test", 587, "compte", "secret", "x@exemple.test").envoyer(
            message_exemple()
        )
    assert ("login", "compte") not in FauxSmtp.instances[0].actions


def test_gabarits_aux_couleurs_de_la_charte_sans_couleur_recopiee():
    """Les couleurs viennent de source.css (app/emails/rendu.py) : aucune n'est écrite dans les
    gabarits."""
    gabarits = list((RACINE / "app/emails/gabarits").glob("*.*"))
    assert len(gabarits) >= 17
    fautifs = [
        g.name
        for g in gabarits
        if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", g.read_text(encoding="utf-8"))
    ]
    assert not fautifs


def test_envoi_en_tache_de_fond_n_ecrit_le_resultat_qu_apres_la_reponse(
    navigateurs, session, chantier, monkeypatch, boite_mail
):
    """La publication ne dépend pas de l'envoi : les e-mails sont « en attente » tant que la tâche
    n'a pas tourné."""
    statuts_avant_envoi = []

    def envoyer_plus_tard(email_id, message):
        statuts_avant_envoi.append(session.get(EmailEnvoye, email_id).statut)

    monkeypatch.setattr(envoi, "envoyer", envoyer_plus_tard)
    reponse = publier(connecte(navigateurs, chantier.chef), brouillon(session, chantier), chantier)

    assert reponse.status_code == 303
    assert statuts_avant_envoi == [StatutEmail.EN_ATTENTE] * 2
    assert boite_mail.envoyes == []
