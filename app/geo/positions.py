"""Passage entre (latitude, longitude) et les colonnes geography de PostGIS."""

import uuid

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session

from app.models import Chantier


def point(latitude: float, longitude: float) -> WKTElement:
    # WKT et GeoJSON : longitude d'abord.
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def lat_lon_chantier(db: Session, chantier_id: uuid.UUID) -> tuple[float, float] | None:
    """(latitude, longitude) du chantier, ou None tant que sa position n'est pas définie."""
    position = cast(Chantier.position, Geometry)
    latitude, longitude = db.execute(
        select(func.ST_Y(position), func.ST_X(position)).where(Chantier.id == chantier_id)
    ).one()
    return None if latitude is None else (latitude, longitude)
