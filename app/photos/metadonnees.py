"""Validation des métadonnées envoyées avec une photo (position, précision, dates, empreinte)."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.geo.coordonnees import CoordonneesInvalides, valider_position
from app.models import SourcePosition

TOLERANCE_FUTUR = timedelta(minutes=5)  # horloges de téléphone légèrement en avance
PRECISION_MAX_M = 10_000
SOURCES_AVEC_POSITION = {SourcePosition.NAVIGATEUR, SourcePosition.EXIF}


class MetadonneesInvalides(ValueError):
    """Le message est destiné à l'utilisateur."""


@dataclass
class MetadonneesPhoto:
    latitude: float | None
    longitude: float | None
    source_position: SourcePosition
    precision_m: float | None
    position_obtenue_le: datetime | None
    prise_le: datetime | None
    empreinte_originale: str | None


def lire_metadonnees(
    *,
    latitude: str,
    longitude: str,
    precision_m: str,
    position_obtenue_le: str,
    source_position: str,
    prise_le: str,
    empreinte_originale: str,
    maintenant: datetime | None = None,
) -> MetadonneesPhoto:
    maintenant = maintenant or datetime.now(UTC)
    date_position = _lire_date(position_obtenue_le, "de la position GPS", maintenant)
    date_prise = _lire_date(prise_le, "de prise de vue", maintenant)

    # Une position invalide ou incohérente n'empêche pas l'envoi : la photo est enregistrée sans
    # position.
    lat = lon = None
    try:
        source = SourcePosition(source_position)
        if source in SOURCES_AVEC_POSITION:
            lat, lon = valider_position(float(latitude), float(longitude))
    except (ValueError, CoordonneesInvalides):
        pass
    if lat is None:
        return MetadonneesPhoto(
            None,
            None,
            SourcePosition.AUCUNE,
            None,
            None,
            date_prise,
            _lire_empreinte(empreinte_originale),
        )

    return MetadonneesPhoto(
        latitude=lat,
        longitude=lon,
        source_position=source,
        precision_m=_lire_precision(precision_m),
        position_obtenue_le=date_position,
        prise_le=date_prise,
        empreinte_originale=_lire_empreinte(empreinte_originale),
    )


def _lire_date(texte: str, libelle: str, maintenant: datetime) -> datetime | None:
    if not texte.strip():
        return None
    try:
        date = datetime.fromisoformat(texte.strip())
    except ValueError:
        raise MetadonneesInvalides(f"Date {libelle} illisible.") from None
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    if date > maintenant + TOLERANCE_FUTUR:
        raise MetadonneesInvalides(f"Date {libelle} dans le futur : vérifiez l'heure du téléphone.")
    return date


def _lire_precision(texte: str) -> float | None:
    try:
        precision = float(texte)
    except ValueError:
        return None
    return precision if 0 <= precision <= PRECISION_MAX_M else None


def _lire_empreinte(texte: str) -> str | None:
    texte = texte.strip().lower()
    return texte if re.fullmatch(r"[0-9a-f]{64}", texte) else None
