import uuid
from datetime import datetime

from sqlalchemy import CHAR, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, cle_primaire_uuid, horodatage


class JetonReinitialisation(Base):
    """Lien de réinitialisation du mot de passe : valable 1 heure, à usage unique, stocké haché."""

    __tablename__ = "jetons_reinitialisation"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    empreinte_jeton: Mapped[str] = mapped_column(CHAR(64), unique=True)
    cree_le: Mapped[datetime] = horodatage()
    expire_le: Mapped[datetime]
    utilise_le: Mapped[datetime | None]
