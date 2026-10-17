import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CHAR, REAL, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, cle_primaire_uuid, horodatage, type_enum

if TYPE_CHECKING:
    from app.models.rapport import Rapport


class SourcePosition(StrEnum):
    NAVIGATEUR = "navigateur"
    EXIF = "exif"
    AUCUNE = "aucune"


class StatutVerification(StrEnum):
    VERIFIEE = "verifiee"
    HORS_ZONE = "hors_zone"
    NON_VERIFIABLE = "non_verifiable"


class Photo(Base):
    """Photo d'un rapport. Sa position GPS permet de vérifier qu'elle a été prise sur le
    chantier."""

    __tablename__ = "photos"

    id: Mapped[uuid.UUID] = cle_primaire_uuid()
    rapport_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rapports.id", ondelete="CASCADE"), index=True
    )
    # Chemins relatifs au dossier STOCKAGE_PHOTOS_DIR ; jamais le nom de fichier du client.
    chemin_fichier: Mapped[str] = mapped_column(String(500))
    chemin_miniature: Mapped[str | None] = mapped_column(String(500))
    # SHA-256 du fichier reçu par le serveur. Indexée pour repérer une même photo envoyée plusieurs
    # fois.
    empreinte_sha256: Mapped[str | None] = mapped_column(CHAR(64), index=True)
    # SHA-256 de l'original, calculée par le navigateur avant compression.
    empreinte_originale: Mapped[str | None] = mapped_column(CHAR(64), index=True)
    position: Mapped[WKBElement | None] = mapped_column(Geography("POINT", srid=4326))
    source_position: Mapped[SourcePosition] = mapped_column(
        type_enum(SourcePosition, "source_position"), server_default=SourcePosition.AUCUNE.value
    )
    precision_m: Mapped[float | None] = mapped_column(REAL)  # précision GPS annoncée par l'appareil
    position_obtenue_le: Mapped[datetime | None]  # horodatage du relevé GPS
    prise_le: Mapped[datetime | None]
    largeur_px: Mapped[int | None]
    hauteur_px: Mapped[int | None]
    taille_octets: Mapped[int | None]
    envoyee_le: Mapped[datetime] = horodatage()
    distance_chantier_m: Mapped[float | None] = mapped_column(REAL)
    statut_verification: Mapped[StatutVerification] = mapped_column(
        type_enum(StatutVerification, "statut_verification"),
        server_default=StatutVerification.NON_VERIFIABLE.value,
    )
    # Signaux de confiance détectés (codes : voir app/geo/verification.py)
    alertes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    verifiee_le: Mapped[datetime | None]  # date du dernier calcul

    rapport: Mapped["Rapport"] = relationship(back_populates="photos")
