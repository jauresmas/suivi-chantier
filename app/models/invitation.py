import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage
from app.models.membre_chantier import TYPE_ROLE_MEMBRE, RoleMembre

if TYPE_CHECKING:
    from app.models.chantier import Chantier


class Invitation(Base):
    """Lien d'invitation à un chantier, à usage unique. Seule l'empreinte SHA-256 du jeton est
    stockée."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    chantier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"), index=True
    )
    invite_par: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    role: Mapped[RoleMembre] = mapped_column(TYPE_ROLE_MEMBRE)
    empreinte_jeton: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expire_le: Mapped[datetime]
    utilisee_le: Mapped[datetime | None]
    utilisee_par: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    cree_le: Mapped[datetime] = horodatage()

    chantier: Mapped["Chantier"] = relationship()
