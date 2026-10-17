import logging

import pytest
from sqlalchemy import func, select

from app.auth.securite import chemin_interne
from app.journalisation import MasquerJetons
from app.models import SessionUtilisateur, User
from tests.outils import MOT_DE_PASSE, creer_utilisateur, poster, se_connecter

FORMULAIRE_INSCRIPTION = {
    "nom_complet": "Rodrigue Hounkpatin",
    "email": "Rodrigue@Exemple.test",
    "telephone": "+229 01 90 00 00 02",
    "mot_de_passe": "un-mot-de-passe-solide",
}


def nombre_de_sessions(session, user: User) -> int:
    return session.scalar(
        select(func.count())
        .select_from(SessionUtilisateur)
        .where(SessionUtilisateur.user_id == user.id)
    )


# --- Inscription -----------------------------------------------------------------------------


def test_inscription_cree_le_compte_et_connecte(navigateur, session):
    reponse = poster(navigateur, "/inscription", FORMULAIRE_INSCRIPTION, page="/inscription")

    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/chantiers"
    user = session.scalar(select(User).where(User.email == "rodrigue@exemple.test"))
    assert user is not None, "e-mail enregistré en minuscules"
    assert nombre_de_sessions(session, user) == 1
    page = navigateur.get("/chantiers", follow_redirects=False)
    assert page.status_code == 200
    assert "Rodrigue Hounkpatin" in page.text  # nom affiché dans l'en-tête


def test_mot_de_passe_stocke_hache_en_argon2(navigateur, session):
    poster(navigateur, "/inscription", FORMULAIRE_INSCRIPTION, page="/inscription")

    user = session.scalar(select(User).where(User.email == "rodrigue@exemple.test"))
    assert user.hash_mot_de_passe.startswith("$argon2")
    assert FORMULAIRE_INSCRIPTION["mot_de_passe"] not in user.hash_mot_de_passe


def test_inscription_refuse_un_mot_de_passe_trop_court(navigateur, session):
    reponse = poster(
        navigateur,
        "/inscription",
        {**FORMULAIRE_INSCRIPTION, "mot_de_passe": "court"},
        page="/inscription",
    )

    assert reponse.status_code == 400
    assert "Au moins 8 caractères" in reponse.text
    assert session.scalar(select(User).where(User.email == "rodrigue@exemple.test")) is None


def test_inscription_refuse_un_email_deja_utilise(navigateur, session):
    creer_utilisateur(session, "rodrigue@exemple.test")

    reponse = poster(navigateur, "/inscription", FORMULAIRE_INSCRIPTION, page="/inscription")

    assert reponse.status_code == 400
    assert "Un compte existe déjà" in reponse.text


# --- Connexion et déconnexion ----------------------------------------------------------------


def test_connexion_puis_deconnexion_supprime_la_session_en_base(navigateur, session):
    user = creer_utilisateur(session, "chef@exemple.test")

    reponse = se_connecter(navigateur, "chef@exemple.test")
    assert reponse.status_code == 303
    assert nombre_de_sessions(session, user) == 1
    assert navigateur.get("/chantiers", follow_redirects=False).status_code == 200

    reponse = poster(navigateur, "/deconnexion", page="/chantiers")
    assert reponse.status_code == 303
    assert nombre_de_sessions(session, user) == 0
    assert navigateur.get("/chantiers", follow_redirects=False).status_code == 303


def test_session_volee_inutilisable_apres_deconnexion(navigateurs, session):
    creer_utilisateur(session, "chef@exemple.test")
    navigateur = navigateurs()
    se_connecter(navigateur, "chef@exemple.test")
    jeton_vole = navigateur.cookies.get("session")

    poster(navigateur, "/deconnexion", page="/chantiers")

    pirate = navigateurs()
    pirate.cookies.set("session", jeton_vole)
    assert pirate.get("/chantiers", follow_redirects=False).status_code == 303


def test_mauvais_mot_de_passe_refuse_avec_le_meme_message_que_email_inconnu(navigateur, session):
    creer_utilisateur(session, "chef@exemple.test")

    mauvais_mdp = se_connecter(navigateur, "chef@exemple.test", "pas-le-bon")
    email_inconnu = se_connecter(navigateur, "inconnu@exemple.test", "pas-le-bon")

    assert mauvais_mdp.status_code == email_inconnu.status_code == 400
    assert "Adresse e-mail ou mot de passe incorrect." in mauvais_mdp.text
    assert "Adresse e-mail ou mot de passe incorrect." in email_inconnu.text
    assert "session" not in navigateur.cookies


def test_blocage_apres_5_echecs(navigateur, session):
    user = creer_utilisateur(session, "chef@exemple.test")
    for _ in range(5):
        assert se_connecter(navigateur, "chef@exemple.test", "pas-le-bon").status_code == 400

    # Même avec le bon mot de passe, la connexion reste bloquée.
    reponse = se_connecter(navigateur, "chef@exemple.test", MOT_DE_PASSE)

    assert reponse.status_code == 429
    assert "Trop de tentatives" in reponse.text
    assert nombre_de_sessions(session, user) == 0


# --- CSRF et redirections ---------------------------------------------------------------------


def test_post_sans_jeton_csrf_refuse(navigateur, session):
    creer_utilisateur(session, "chef@exemple.test")
    navigateur.get("/connexion")  # le cookie CSRF existe, mais le formulaire n'envoie pas le jeton

    sans_jeton = navigateur.post(
        "/connexion", data={"email": "chef@exemple.test", "mot_de_passe": MOT_DE_PASSE}
    )
    jeton_faux = navigateur.post(
        "/connexion",
        data={"email": "chef@exemple.test", "mot_de_passe": MOT_DE_PASSE, "csrf_token": "0" * 64},
    )

    assert sans_jeton.status_code == 403
    assert jeton_faux.status_code == 403
    assert "session" not in navigateur.cookies


@pytest.mark.parametrize(
    "suivant",
    [
        "//evil.example",
        "https://evil.example/",
        "/\\evil.example",
        "/\t/evil.example",
        "evil.example",
    ],
)
def test_suivant_externe_ignore(navigateur, session, suivant):
    creer_utilisateur(session, "chef@exemple.test")

    reponse = se_connecter(navigateur, "chef@exemple.test", suivant=suivant)

    assert reponse.headers["location"] == "/chantiers"


def test_suivant_interne_respecte(navigateur, session):
    creer_utilisateur(session, "chef@exemple.test")

    reponse = se_connecter(navigateur, "chef@exemple.test", suivant="/invitations/abc?x=1")

    assert reponse.headers["location"] == "/invitations/abc?x=1"
    assert chemin_interne("/chantiers/1") == "/chantiers/1"


# --- Journaux ---------------------------------------------------------------------------------


def test_jeton_invitation_masque_dans_le_journal_des_acces():
    jeton = "Abc123_-" * 5 + "xyz"
    enregistrement = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/connexion?suivant=%2Finvitations%2F{jeton}", "1.1", 200),
        None,
    )
    MasquerJetons().filter(enregistrement)
    assert jeton not in enregistrement.getMessage()

    enregistrement.args = ("127.0.0.1:5000", "GET", f"/invitations/{jeton}", "1.1", 200)
    MasquerJetons().filter(enregistrement)
    assert jeton not in enregistrement.getMessage()
    assert "/invitations/***" in enregistrement.getMessage()
