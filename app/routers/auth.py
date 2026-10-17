"""Inscription, connexion, déconnexion et réinitialisation du mot de passe."""

import math
import re

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import reinitialisation
from app.auth.dependencies import get_current_user_optional
from app.auth.limitation import limiteur_connexions
from app.auth.securite import chemin_interne, hacher_mot_de_passe, verifier_mot_de_passe
from app.auth.sessions import fermer_session, ouvrir_session
from app.database import get_db
from app.emails.envoi import programmer
from app.emails.notifications import message_reinitialisation
from app.emails.rendu import url
from app.models import User
from app.templating import templates

router = APIRouter(tags=["comptes"], include_in_schema=False)

MOT_DE_PASSE_MIN = 8
MOT_DE_PASSE_MAX = 128
FORMAT_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FORMAT_TELEPHONE = re.compile(r"^\+?[0-9 .\-]{6,30}$")
MESSAGE_IDENTIFIANTS = "Adresse e-mail ou mot de passe incorrect."


def normaliser_email(email: str) -> str:
    return email.strip().lower()


@router.get("/inscription")
def formulaire_inscription(
    request: Request,
    suivant: str = "",
    utilisateur: User | None = Depends(get_current_user_optional),
):
    if utilisateur:
        return RedirectResponse(chemin_interne(suivant), status_code=303)
    return templates.TemplateResponse(
        request,
        "auth/inscription.html",
        {"suivant": chemin_interne(suivant, defaut=""), "valeurs": {}, "erreurs": {}},
    )


@router.post("/inscription")
def inscription(
    request: Request,
    nom_complet: str = Form(""),
    email: str = Form(""),
    telephone: str = Form(""),
    mot_de_passe: str = Form(""),
    suivant: str = Form(""),
    db: Session = Depends(get_db),
):
    nom_complet, email, telephone = nom_complet.strip(), normaliser_email(email), telephone.strip()
    erreurs = {}
    if not nom_complet:
        erreurs["nom_complet"] = "Indiquez votre nom complet."
    elif len(nom_complet) > 150:
        erreurs["nom_complet"] = "150 caractères au maximum."
    if not FORMAT_EMAIL.match(email) or len(email) > 255:
        erreurs["email"] = "Adresse e-mail invalide."
    elif db.scalar(select(User.id).where(User.email == email)):
        erreurs["email"] = "Un compte existe déjà avec cette adresse. Connectez-vous."
    if telephone and not FORMAT_TELEPHONE.match(telephone):
        erreurs["telephone"] = "Numéro invalide (chiffres, espaces et + uniquement)."
    if len(mot_de_passe) < MOT_DE_PASSE_MIN:
        erreurs["mot_de_passe"] = f"Au moins {MOT_DE_PASSE_MIN} caractères."
    elif len(mot_de_passe) > MOT_DE_PASSE_MAX:
        erreurs["mot_de_passe"] = f"{MOT_DE_PASSE_MAX} caractères au maximum."

    if not erreurs:
        user = User(
            nom_complet=nom_complet,
            email=email,
            telephone=telephone or None,
            hash_mot_de_passe=hacher_mot_de_passe(mot_de_passe),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:  # même e-mail inscrit entre la vérification et l'insertion
            db.rollback()
            erreurs["email"] = "Un compte existe déjà avec cette adresse. Connectez-vous."

    if erreurs:
        valeurs = {"nom_complet": nom_complet, "email": email, "telephone": telephone}
        return templates.TemplateResponse(
            request,
            "auth/inscription.html",
            {"suivant": chemin_interne(suivant, defaut=""), "valeurs": valeurs, "erreurs": erreurs},
            status_code=400,
        )

    response = RedirectResponse(chemin_interne(suivant), status_code=303)
    ouvrir_session(db, user, response)
    return response


@router.get("/connexion")
def formulaire_connexion(
    request: Request,
    suivant: str = "",
    mot_de_passe: str = "",
    utilisateur: User | None = Depends(get_current_user_optional),
):
    if utilisateur:
        return RedirectResponse(chemin_interne(suivant), status_code=303)
    return templates.TemplateResponse(
        request,
        "auth/connexion.html",
        {
            "suivant": chemin_interne(suivant, defaut=""),
            "email": "",
            "mot_de_passe_modifie": mot_de_passe == "modifie",
        },
    )


@router.post("/connexion")
def connexion(
    request: Request,
    email: str = Form(""),
    mot_de_passe: str = Form(""),
    suivant: str = Form(""),
    db: Session = Depends(get_db),
):
    email = normaliser_email(email)

    def refuser(message: str, status_code: int):
        return templates.TemplateResponse(
            request,
            "auth/connexion.html",
            {"suivant": chemin_interne(suivant, defaut=""), "email": email, "erreur": message},
            status_code=status_code,
        )

    attente_s = limiteur_connexions.secondes_de_blocage(email)
    if attente_s:
        minutes = math.ceil(attente_s / 60)
        return refuser(
            "Trop de tentatives de connexion. "
            f"Réessayez dans {minutes} minute{'s' if minutes > 1 else ''}.",
            429,
        )

    user = db.scalar(select(User).where(User.email == email))
    if not verifier_mot_de_passe(mot_de_passe, user.hash_mot_de_passe if user else None):
        limiteur_connexions.enregistrer_echec(email)
        return refuser(MESSAGE_IDENTIFIANTS, 400)

    limiteur_connexions.reinitialiser(email)
    response = RedirectResponse(chemin_interne(suivant), status_code=303)
    ouvrir_session(db, user, response)
    return response


@router.post("/deconnexion")
def deconnexion(request: Request, db: Session = Depends(get_db)):
    response = RedirectResponse("/", status_code=303)
    fermer_session(db, request, response)
    return response


# --- Mot de passe oublié --------------------------------------------------------------------------


@router.get("/mot-de-passe-oublie")
def formulaire_mot_de_passe_oublie(request: Request):
    return templates.TemplateResponse(
        request, "auth/mot_de_passe_oublie.html", {"email": "", "envoye": False}
    )


@router.post("/mot-de-passe-oublie")
def mot_de_passe_oublie(
    request: Request, taches: BackgroundTasks, email: str = Form(""), db: Session = Depends(get_db)
):
    """Même réponse que le compte existe ou non ; l'e-mail part en tâche de fond (temps de réponse
    identique)."""
    email = normaliser_email(email)
    if not FORMAT_EMAIL.match(email) or len(email) > 255:
        return templates.TemplateResponse(
            request,
            "auth/mot_de_passe_oublie.html",
            {"email": email, "envoye": False, "erreur": "Adresse e-mail invalide."},
            status_code=400,
        )
    if demande := reinitialisation.demander(db, email):
        user, jeton = demande
        programmer(db, taches, [message_reinitialisation(user, url(f"/reinitialiser/{jeton}"))])
    return templates.TemplateResponse(
        request, "auth/mot_de_passe_oublie.html", {"email": email, "envoye": True}
    )


def page_reinitialisation(
    request: Request, jeton: str, erreurs: dict | None = None, code: int = 200
):
    return templates.TemplateResponse(
        request,
        "auth/reinitialiser.html",
        {"jeton": jeton, "erreurs": erreurs or {}, "invalide": False, "min": MOT_DE_PASSE_MIN},
        status_code=code,
    )


def page_lien_perime(request: Request):
    return templates.TemplateResponse(
        request, "auth/reinitialiser.html", {"invalide": True}, status_code=410
    )


@router.get("/reinitialiser/{jeton}")
def formulaire_reinitialisation(request: Request, jeton: str, db: Session = Depends(get_db)):
    if reinitialisation.jeton_valide(db, jeton) is None:
        return page_lien_perime(request)
    return page_reinitialisation(request, jeton)


@router.post("/reinitialiser/{jeton}")
def reinitialiser_mot_de_passe(
    request: Request,
    jeton: str,
    mot_de_passe: str = Form(""),
    confirmation: str = Form(""),
    db: Session = Depends(get_db),
):
    trouve = reinitialisation.jeton_valide(db, jeton, verrouiller=True)
    if trouve is None:
        db.rollback()
        return page_lien_perime(request)
    erreurs = {}
    if len(mot_de_passe) < MOT_DE_PASSE_MIN:
        erreurs["mot_de_passe"] = f"Au moins {MOT_DE_PASSE_MIN} caractères."
    elif len(mot_de_passe) > MOT_DE_PASSE_MAX:
        erreurs["mot_de_passe"] = f"{MOT_DE_PASSE_MAX} caractères au maximum."
    elif confirmation != mot_de_passe:
        erreurs["confirmation"] = "Les deux mots de passe ne sont pas identiques."
    if erreurs:
        db.rollback()
        return page_reinitialisation(request, jeton, erreurs, 400)
    user = reinitialisation.reinitialiser(db, trouve, mot_de_passe)
    db.commit()
    limiteur_connexions.reinitialiser(
        user.email
    )  # un compte bloqué par des essais ratés est débloqué
    response = RedirectResponse("/connexion?mot_de_passe=modifie", status_code=303)
    fermer_session(
        db, request, response
    )  # cookie de ce navigateur retiré (la session est déjà supprimée)
    return response
