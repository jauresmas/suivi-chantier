"""Dépendances FastAPI réutilisables pour l'authentification et le contrôle d'accès."""

import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth.sessions import COOKIE_SESSION, session_active
from app.database import get_db
from app.models import MembreChantier, RoleMembre, User


class ConnexionRequise(Exception):
    """Levée quand une page demande un utilisateur connecté ; transformée en redirection par
    main.py."""

    def __init__(self, suivant: str | None = None):
        self.suivant = suivant


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    jeton = request.cookies.get(COOKIE_SESSION)
    session = session_active(db, jeton) if jeton else None
    user = session.user if session else None
    request.state.utilisateur = user  # pour l'en-tête des pages
    return user


def get_current_user(
    request: Request, user: User | None = Depends(get_current_user_optional)
) -> User:
    if user is None:
        # Après connexion, on revient sur la page demandée (seulement pour un GET, qu'on peut
        # rejouer).
        suivant = None
        if request.method == "GET":
            suivant = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise ConnexionRequise(suivant)
    return user


def utilisateur_api(user: User | None = Depends(get_current_user_optional)) -> User:
    """Comme get_current_user, mais répond 401 au lieu de rediriger (appels fetch / XHR, images)."""
    if user is None:
        raise HTTPException(401, "Connexion requise.")
    return user


def require_membre(roles: set[RoleMembre] | None = None, api: bool = False):
    """Vérifie que l'utilisateur est membre du chantier {id} de l'URL, avec l'un des rôles demandés.

    Un non-membre reçoit une 404, comme si le chantier n'existait pas. Un membre sans le bon
    rôle reçoit une 403 : il sait déjà que le chantier existe. Avec `api=True`, un visiteur
    non connecté reçoit une 401 au lieu d'être redirigé vers /connexion.
    """
    dependance_utilisateur = utilisateur_api if api else get_current_user

    def verifier(
        id: str, user: User = Depends(dependance_utilisateur), db: Session = Depends(get_db)
    ) -> MembreChantier:
        try:
            chantier_id = uuid.UUID(id)
        except ValueError:
            raise HTTPException(404) from None
        membre = db.scalar(
            select(MembreChantier)
            .options(joinedload(MembreChantier.chantier))
            .where(MembreChantier.chantier_id == chantier_id, MembreChantier.user_id == user.id)
        )
        if membre is None:
            raise HTTPException(404)
        if roles and membre.role not in roles:
            raise HTTPException(403, "Cette action est réservée au propriétaire du chantier.")
        return membre

    return verifier


# Dépendances prêtes à l'emploi, créées une fois : Depends(membre_du_chantier)…
membre_du_chantier = require_membre()
membre_du_chantier_api = require_membre(api=True)
proprietaire_du_chantier = require_membre({RoleMembre.PROPRIETAIRE})
