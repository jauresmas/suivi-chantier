"""Sessions côté serveur : un jeton aléatoire dans le cookie, son empreinte en base."""

from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.auth.securite import cookies_securises, empreinte, nouveau_jeton
from app.models import SessionUtilisateur, User

COOKIE_SESSION = "session"
DUREE_SESSION = timedelta(days=30)
# Évite une écriture en base à chaque requête.
INTERVALLE_MAJ_ACTIVITE = timedelta(minutes=5)


def ouvrir_session(db: Session, user: User, response: Response) -> None:
    jeton = nouveau_jeton()
    maintenant = datetime.now(UTC)
    db.execute(
        delete(SessionUtilisateur).where(
            SessionUtilisateur.user_id == user.id, SessionUtilisateur.expire_le <= maintenant
        )
    )
    db.add(
        SessionUtilisateur(
            user_id=user.id,
            empreinte_jeton=empreinte(jeton),
            expire_le=maintenant + DUREE_SESSION,
            derniere_activite=maintenant,
        )
    )
    db.commit()
    response.set_cookie(
        COOKIE_SESSION,
        jeton,
        max_age=int(DUREE_SESSION.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=cookies_securises(),
    )


def fermer_session(db: Session, request: Request, response: Response) -> None:
    """Supprime la session en base, pas seulement le cookie."""
    jeton = request.cookies.get(COOKIE_SESSION)
    if jeton:
        db.execute(
            delete(SessionUtilisateur).where(SessionUtilisateur.empreinte_jeton == empreinte(jeton))
        )
        db.commit()
    response.delete_cookie(
        COOKIE_SESSION, httponly=True, samesite="lax", secure=cookies_securises()
    )


def session_active(db: Session, jeton: str) -> SessionUtilisateur | None:
    maintenant = datetime.now(UTC)
    session = db.scalar(
        select(SessionUtilisateur)
        .options(joinedload(SessionUtilisateur.user))
        .where(
            SessionUtilisateur.empreinte_jeton == empreinte(jeton),
            SessionUtilisateur.expire_le > maintenant,
        )
    )
    if session is not None and (
        session.derniere_activite is None
        or maintenant - session.derniere_activite > INTERVALLE_MAJ_ACTIVITE
    ):
        session.derniere_activite = maintenant
        db.commit()
    return session
