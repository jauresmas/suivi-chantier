import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage

if TYPE_CHECKING:
    from app.models.user import User


class SessionUtilisateur(Base):
    """Session de connexion. Le cookie contient le jeton ; la base n'en garde que l'empreinte
    SHA-256."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    empreinte_jeton: Mapped[str] = mapped_column(CHAR(64), unique=True)
    cree_le: Mapped[datetime] = horodatage()
    expire_le: Mapped[datetime]
    derniere_activite: Mapped[datetime | None]

    user: Mapped["User"] = relationship()
