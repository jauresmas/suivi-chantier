import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage, type_enum

if TYPE_CHECKING:
    from app.models.chantier import Chantier
    from app.models.etape import Etape
    from app.models.photo import Photo
    from app.models.user import User


class StatutRapport(StrEnum):
    BROUILLON = "brouillon"  # visible de son seul auteur
    PUBLIE = "publie"  # définitif : une correction passe par un nouveau rapport


class Rapport(Base):
    """Rapport de visite : un passage sur le chantier, avec un texte et des photos."""

    __tablename__ = "rapports"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    chantier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"), index=True
    )
    auteur_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    etape_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("etapes.id", ondelete="SET NULL"))
    texte: Mapped[str | None] = mapped_column(Text)
    statut: Mapped[StatutRapport] = mapped_column(
        type_enum(StatutRapport, "statut_rapport"),
        default=StatutRapport.BROUILLON,
        server_default=StatutRapport.BROUILLON.value,
    )
    cree_le: Mapped[datetime] = horodatage(index=True)
    publie_le: Mapped[datetime | None] = mapped_column(index=True)

    chantier: Mapped["Chantier"] = relationship(back_populates="rapports")
    auteur: Mapped["User | None"] = relationship(back_populates="rapports")
    etape: Mapped["Etape | None"] = relationship(back_populates="rapports")
    photos: Mapped[list["Photo"]] = relationship(
        back_populates="rapport",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Photo.envoyee_le",
    )
