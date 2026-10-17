import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CheckConstraint, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage, type_enum

if TYPE_CHECKING:
    from app.models.etape import Etape
    from app.models.membre_chantier import MembreChantier
    from app.models.rapport import Rapport


class StatutChantier(StrEnum):
    EN_PREPARATION = "en_preparation"
    EN_COURS = "en_cours"
    EN_PAUSE = "en_pause"
    TERMINE = "termine"


class StatutPosition(StrEnum):
    NON_DEFINIE = "non_definie"
    ESTIMEE = "estimee"  # placée sur la carte, sans relevé sur place
    A_VALIDER = "a_valider"  # relevé reçu, en attente de validation par le propriétaire
    CONFIRMEE = "confirmee"


class SourcePositionChantier(StrEnum):
    CARTE_PROPRIETAIRE = "carte_proprietaire"
    RELEVE_PROCHE = "releve_proche"
    RELEVE_CHEF = "releve_chef"
    BORNES_DOCUMENT = "bornes_document"  # issue future
    GEOMETRE = "geometre"  # issue future


# Types partagés avec la table historique_position_chantier.
TYPE_STATUT_POSITION = type_enum(StatutPosition, "statut_position")
TYPE_SOURCE_POSITION_CHANTIER = type_enum(SourcePositionChantier, "source_position_chantier")


class Chantier(Base):
    __tablename__ = "chantiers"
    __table_args__ = (
        CheckConstraint("rayon_verification_m BETWEEN 10 AND 1000", name="rayon_verification_m"),
        # Statut, source et position restent cohérents : pas de position « confirmée » sans point,
        # ni de point sans source. « À valider » peut ne pas avoir de point (premier relevé reçu).
        CheckConstraint(
            "(position IS NULL) = (source_position IS NULL)", name="position_et_source"
        ),
        CheckConstraint(
            "CASE statut_position WHEN 'non_definie' THEN position IS NULL"
            " WHEN 'a_valider' THEN TRUE ELSE position IS NOT NULL END",
            name="position_et_statut",
        ),
        CheckConstraint(
            "(statut_position = 'confirmee') = (position_confirmee_le IS NOT NULL)",
            name="position_confirmee_le",
        ),
    )

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    nom: Mapped[str] = mapped_column(String(150))
    adresse: Mapped[str | None] = mapped_column(Text)
    ville: Mapped[str | None] = mapped_column(String(100))
    pays: Mapped[str | None] = mapped_column(String(2))  # code ISO 3166-1, ex. BJ
    # Geography : ST_Distance et ST_DWithin travaillent directement en mètres.
    # Vide tant que le chantier n'est pas positionné (statut_position = non_definie).
    position: Mapped[WKBElement | None] = mapped_column(Geography("POINT", srid=4326))
    statut_position: Mapped[StatutPosition] = mapped_column(
        TYPE_STATUT_POSITION,
        default=StatutPosition.NON_DEFINIE,
        server_default=StatutPosition.NON_DEFINIE.value,
    )
    source_position: Mapped[SourcePositionChantier | None] = mapped_column(
        TYPE_SOURCE_POSITION_CHANTIER
    )
    position_confirmee_le: Mapped[datetime | None]
    emprise: Mapped[WKBElement | None] = mapped_column(Geography("POLYGON", srid=4326))
    rayon_verification_m: Mapped[int] = mapped_column(server_default="100")
    budget_prevu: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    devise: Mapped[str] = mapped_column(String(3), server_default="XOF")
    statut: Mapped[StatutChantier] = mapped_column(
        type_enum(StatutChantier, "statut_chantier"),
        server_default=StatutChantier.EN_PREPARATION.value,
    )
    cree_le: Mapped[datetime] = horodatage()

    membres: Mapped[list["MembreChantier"]] = relationship(
        back_populates="chantier", cascade="all, delete-orphan", passive_deletes=True
    )
    etapes: Mapped[list["Etape"]] = relationship(
        back_populates="chantier",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Etape.ordre",
    )
    rapports: Mapped[list["Rapport"]] = relationship(
        back_populates="chantier", cascade="all, delete-orphan", passive_deletes=True
    )
