import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, horodatage, type_enum


class ModeNotification(StrEnum):
    IMMEDIAT = "immediat"
    RESUME_QUOTIDIEN = "resume_quotidien"
    AUCUN = "aucun"


class PreferenceNotification(Base):
    """Mode de notification d'un utilisateur pour un chantier. Sans ligne : « immediat »."""

    __tablename__ = "preferences_notification"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chantier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chantiers.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    mode: Mapped[ModeNotification] = mapped_column(
        type_enum(ModeNotification, "mode_notification"),
        default=ModeNotification.IMMEDIAT,
        server_default=ModeNotification.IMMEDIAT.value,
    )
    modifie_le: Mapped[datetime] = horodatage()
