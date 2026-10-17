import uuid
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, type_enum

if TYPE_CHECKING:
    from app.models.chantier import Chantier
    from app.models.rapport import Rapport


class TypeEtape(StrEnum):
    TERRASSEMENT = "terrassement"
    FONDATIONS = "fondations"
    ELEVATION = "elevation"
    DALLE = "dalle"
    TOITURE = "toiture"
    MENUISERIES = "menuiseries"
    ELECTRICITE_PLOMBERIE = "electricite_plomberie"
    FINITIONS = "finitions"
    AUTRE = "autre"


class Etape(Base):
    __tablename__ = "etapes"
    __table_args__ = (
        UniqueConstraint("chantier_id", "ordre"),
        CheckConstraint("avancement_pct BETWEEN 0 AND 100", name="avancement_pct"),
        CheckConstraint("poids BETWEEN 1 AND 100", name="poids"),
    )

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    chantier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"))
    type: Mapped[TypeEtape] = mapped_column(type_enum(TypeEtape, "type_etape"))
    libelle: Mapped[str | None] = mapped_column(String(150))  # utile quand type = autre
    ordre: Mapped[int]
    avancement_pct: Mapped[int] = mapped_column(server_default="0")
    # Part de l'étape dans l'avancement global. L'application propose un poids selon le type
    # (app/modeles_etapes.py) ; 10 est la valeur de repli pour une insertion directe en SQL.
    poids: Mapped[int] = mapped_column(server_default="10")
    date_fin_prevue: Mapped[date | None]
    # Dates réelles, remplies automatiquement à la première progression et à 100 %.
    date_debut: Mapped[date | None]
    date_fin: Mapped[date | None]

    chantier: Mapped["Chantier"] = relationship(back_populates="etapes")
    rapports: Mapped[list["Rapport"]] = relationship(back_populates="etape", passive_deletes=True)
