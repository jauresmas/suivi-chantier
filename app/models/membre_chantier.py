import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, horodatage, type_enum

if TYPE_CHECKING:
    from app.models.chantier import Chantier
    from app.models.user import User


class RoleMembre(StrEnum):
    PROPRIETAIRE = "proprietaire"
    CHEF_CHANTIER = "chef_chantier"


# Type partagé avec la table invitations.
TYPE_ROLE_MEMBRE = type_enum(RoleMembre, "role_membre")


class MembreChantier(Base):
    """Rôle d'un utilisateur sur un chantier : on peut être propriétaire ici et chef de chantier
    ailleurs."""

    __tablename__ = "membres_chantier"

    chantier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexé pour retrouver vite les chantiers d'un utilisateur (la clé primaire commence par
    # chantier_id).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[RoleMembre] = mapped_column(TYPE_ROLE_MEMBRE)
    ajoute_le: Mapped[datetime] = horodatage()

    chantier: Mapped["Chantier"] = relationship(back_populates="membres")
    user: Mapped["User"] = relationship(back_populates="adhesions")
