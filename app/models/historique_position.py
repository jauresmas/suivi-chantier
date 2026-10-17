import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage
from app.models.chantier import (
    TYPE_SOURCE_POSITION_CHANTIER,
    TYPE_STATUT_POSITION,
    SourcePositionChantier,
    StatutPosition,
)

if TYPE_CHECKING:
    from app.models.releve_position import RelevePosition
    from app.models.user import User


class HistoriquePositionChantier(Base):
    """Chaque changement de la position de référence d'un chantier : qui, quand, d'où, et vers
    où."""

    __tablename__ = "historique_position_chantier"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    chantier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"), index=True
    )
    ancienne_position: Mapped[WKBElement | None] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False)
    )
    nouvelle_position: Mapped[WKBElement | None] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False)
    )
    ancien_statut: Mapped[StatutPosition] = mapped_column(TYPE_STATUT_POSITION)
    nouveau_statut: Mapped[StatutPosition] = mapped_column(TYPE_STATUT_POSITION)
    source: Mapped[SourcePositionChantier] = mapped_column(TYPE_SOURCE_POSITION_CHANTIER)
    releve_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releves_position.id", ondelete="SET NULL")
    )
    modifie_par: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    cree_le: Mapped[datetime] = horodatage()

    auteur: Mapped["User | None"] = relationship()
    releve: Mapped["RelevePosition | None"] = relationship()
