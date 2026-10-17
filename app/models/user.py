import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage

if TYPE_CHECKING:
    from app.models.membre_chantier import MembreChantier
    from app.models.rapport import Rapport


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    nom_complet: Mapped[str] = mapped_column(String(150))
    telephone: Mapped[str | None] = mapped_column(String(30))
    hash_mot_de_passe: Mapped[str | None] = mapped_column(String(255))
    cree_le: Mapped[datetime] = horodatage()

    adhesions: Mapped[list["MembreChantier"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    rapports: Mapped[list["Rapport"]] = relationship(back_populates="auteur", passive_deletes=True)
