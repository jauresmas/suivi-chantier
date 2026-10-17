"""Préférences de notification par chantier, et lien de désinscription signé (sans connexion)."""

import hashlib
import hmac
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.emails.rendu import url
from app.models import MembreChantier, ModeNotification, PreferenceNotification, RoleMembre, User

LIBELLES_MODE = {
    ModeNotification.IMMEDIAT: "À chaque rapport",
    ModeNotification.RESUME_QUOTIDIEN: "Un résumé par jour",
    ModeNotification.AUCUN: "Aucun e-mail",
}


@dataclass
class Destinataire:
    user: User
    mode: ModeNotification


def proprietaires(db: Session, chantier_id: uuid.UUID) -> list[Destinataire]:
    """Propriétaires du chantier et leur mode de notification (« immediat » sans préférence
    enregistrée)."""
    lignes = db.execute(
        select(User, PreferenceNotification.mode)
        .join(MembreChantier, MembreChantier.user_id == User.id)
        .outerjoin(
            PreferenceNotification,
            (PreferenceNotification.user_id == User.id)
            & (PreferenceNotification.chantier_id == chantier_id),
        )
        .where(
            MembreChantier.chantier_id == chantier_id,
            MembreChantier.role == RoleMembre.PROPRIETAIRE,
        )
        .order_by(User.nom_complet)
    ).all()
    return [Destinataire(user, mode or ModeNotification.IMMEDIAT) for user, mode in lignes]


def mode_de(db: Session, user_id: uuid.UUID, chantier_id: uuid.UUID) -> ModeNotification:
    mode = db.scalar(
        select(PreferenceNotification.mode).where(
            PreferenceNotification.user_id == user_id,
            PreferenceNotification.chantier_id == chantier_id,
        )
    )
    return mode or ModeNotification.IMMEDIAT


def enregistrer_mode(
    db: Session, user_id: uuid.UUID, chantier_id: uuid.UUID, mode: ModeNotification
) -> None:
    requete = insert(PreferenceNotification).values(
        user_id=user_id, chantier_id=chantier_id, mode=mode
    )
    db.execute(
        requete.on_conflict_do_update(
            index_elements=["user_id", "chantier_id"], set_={"mode": mode, "modifie_le": func.now()}
        )
    )


# --- Désinscription en un clic -------------------------------------------------------------------


def _signature(charge: str) -> str:
    cle = get_settings().secret_key.encode()
    return hmac.new(cle, f"desinscription:{charge}".encode(), hashlib.sha256).hexdigest()[:32]


def jeton_desinscription(user_id: uuid.UUID, chantier_id: uuid.UUID) -> str:
    """Utilisateur et chantier, signés avec SECRET_KEY : le lien n'a pas besoin d'être stocké."""
    charge = f"{user_id.hex}{chantier_id.hex}"
    return f"{charge}.{_signature(charge)}"


def lire_jeton_desinscription(jeton: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    charge, _, signature = jeton.partition(".")
    if len(charge) != 64 or not hmac.compare_digest(signature, _signature(charge)):
        return None
    try:
        return uuid.UUID(charge[:32]), uuid.UUID(charge[32:])
    except ValueError:
        return None


def url_desinscription(user_id: uuid.UUID, chantier_id: uuid.UUID) -> str:
    return url(f"/notifications/desinscription/{jeton_desinscription(user_id, chantier_id)}")
