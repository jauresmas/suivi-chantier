"""Fichiers des photos sur disque, sous STOCKAGE_PHOTOS_DIR (jamais exposé en statique)."""

import os
import uuid
from datetime import datetime
from pathlib import Path

from app.config import get_settings


def racine() -> Path:
    return Path(get_settings().stockage_photos_dir).resolve()


def chemins_photo(chantier_id: uuid.UUID, photo_id: uuid.UUID, date: datetime) -> tuple[str, str]:
    """Chemins relatifs de la photo et de sa miniature : {chantier}/{AAAA}/{MM}/{uuid}.jpg."""
    dossier = f"{chantier_id}/{date:%Y}/{date:%m}"
    return f"{dossier}/{photo_id}.jpg", f"{dossier}/{photo_id}_mini.jpg"


def chemins_photo_releve(
    chantier_id: uuid.UUID, releve_id: uuid.UUID, photo_id: uuid.UUID
) -> tuple[str, str]:
    """Photos du terrain prises pendant un relevé de position :
    {chantier}/releves/{relevé}/{uuid}.jpg."""
    dossier = f"{chantier_id}/releves/{releve_id}"
    return f"{dossier}/{photo_id}.jpg", f"{dossier}/{photo_id}_mini.jpg"


def ecrire(relatif: str, contenu: bytes) -> None:
    chemin = racine() / relatif
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_bytes(contenu)
    os.replace(temporaire, chemin)  # le fichier final n'existe jamais à moitié écrit


def supprimer(relatif: str) -> None:
    (racine() / relatif).unlink(missing_ok=True)


def chemin_existant(relatif: str | None) -> Path | None:
    """Chemin absolu du fichier s'il existe et se trouve bien sous la racine du stockage."""
    if not relatif:
        return None
    base = racine()
    chemin = (base / relatif).resolve()
    if not chemin.is_relative_to(base) or not chemin.is_file():
        return None
    return chemin
