import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage

if TYPE_CHECKING:
    from app.models.etape import Etape


class HistoriqueAvancement(Base):
    """Chaque changement d'avancement d'une étape, avec le rapport qui l'a publié."""

    __tablename__ = "historique_avancement"
    __table_args__ = (
        CheckConstraint("ancien_pct BETWEEN 0 AND 100", name="ancien_pct"),
        CheckConstraint("nouveau_pct BETWEEN 0 AND 100", name="nouveau_pct"),
    )

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    etape_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("etapes.id", ondelete="CASCADE"), index=True
    )
    rapport_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rapports.id", ondelete="SET NULL")
    )
    auteur_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ancien_pct: Mapped[int]
    nouveau_pct: Mapped[int]
    commentaire: Mapped[str | None] = mapped_column(Text)
    cree_le: Mapped[datetime] = horodatage()

    etape: Mapped["Etape"] = relationship()
