"""Base déclarative commune et colonnes réutilisées par tous les modèles."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

# Noms de contraintes et d'index stables : Alembic peut ainsi les retrouver et les supprimer.
CONVENTION_NOMMAGE = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENTION_NOMMAGE)
    # Toutes les dates sont des TIMESTAMP WITH TIME ZONE.
    type_annotation_map = {datetime: DateTime(timezone=True)}


def cle_primaire_uuid() -> MappedColumn[uuid.UUID]:
    # Générée côté Python pour connaître l'identifiant avant l'insertion,
    # et côté base pour les insertions faites depuis pgAdmin ou QGIS.
    return mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )


def horodatage(**options) -> MappedColumn[datetime]:
    return mapped_column(server_default=func.now(), **options)


def type_enum(classe: type[StrEnum], nom: str) -> Enum:
    """Type ENUM PostgreSQL qui stocke les valeurs (« en_cours ») et non les noms Python (« EN_COURS
    »)."""
    return Enum(classe, name=nom, values_callable=lambda membres: [m.value for m in membres])
