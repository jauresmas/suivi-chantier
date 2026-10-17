import math

import pytest
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import cast, delete, func, select
from sqlalchemy.exc import IntegrityError

from app.models import Chantier, Etape, Photo, Rapport, TypeEtape
from tests.outils import position_confirmee

# Chantier de référence, près d'Abomey-Calavi (Bénin)
LON, LAT = 2.35, 6.45


def point(lon: float, lat: float) -> WKTElement:
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


def creer_chantier(session) -> Chantier:
    chantier = Chantier(nom="Chantier de test", pays="BJ", **position_confirmee(LON, LAT))
    session.add(chantier)
    session.flush()
    return chantier


def distance_haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distance sur la sphère de rayon moyen terrestre : référence indépendante de PostGIS."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(a))


def test_chantier_garde_sa_position(session):
    chantier = creer_chantier(session)
    session.expire_all()

    lon, lat = session.execute(
        select(
            func.ST_X(cast(Chantier.position, Geometry)),
            func.ST_Y(cast(Chantier.position, Geometry)),
        ).where(Chantier.id == chantier.id)
    ).one()

    assert lon == pytest.approx(LON)
    assert lat == pytest.approx(LAT)


def test_st_distance_renvoie_des_metres(session):
    # Photo prise environ 1 km à l'est du chantier.
    lon_photo = LON + 0.00904
    chantier = creer_chantier(session)
    rapport = Rapport(chantier=chantier)
    photo = Photo(rapport=rapport, chemin_fichier="test.jpg", position=point(lon_photo, LAT))
    session.add_all([rapport, photo])
    session.flush()

    distance = session.scalar(
        select(func.ST_Distance(Chantier.position, Photo.position))
        .join(Rapport, Rapport.chantier_id == Chantier.id)
        .join(Photo, Photo.rapport_id == Rapport.id)
        .where(Photo.id == photo.id)
    )

    attendu = distance_haversine_m(LON, LAT, lon_photo, LAT)
    assert attendu == pytest.approx(1000, rel=0.01)
    assert distance == pytest.approx(attendu, rel=0.01)


def test_avancement_superieur_a_100_refuse(session):
    chantier = creer_chantier(session)
    session.add(Etape(chantier=chantier, type=TypeEtape.DALLE, ordre=1, avancement_pct=150))

    with pytest.raises(IntegrityError, match="ck_etapes_avancement_pct"):
        session.flush()


def test_suppression_chantier_supprime_etapes_rapports_photos(session):
    chantier = creer_chantier(session)
    etape = Etape(chantier=chantier, type=TypeEtape.FONDATIONS, ordre=1)
    rapport = Rapport(chantier=chantier, etape=etape, texte="Coulage des semelles")
    photo = Photo(rapport=rapport, chemin_fichier="semelles.jpg", position=point(LON, LAT))
    session.add_all([etape, rapport, photo])
    session.flush()
    ids = {Etape: etape.id, Rapport: rapport.id, Photo: photo.id}

    # Suppression en SQL, sans passer par l'ORM : c'est bien ON DELETE CASCADE qui est testé.
    session.expunge_all()
    session.execute(delete(Chantier).where(Chantier.id == chantier.id))

    for modele, ident in ids.items():
        assert session.get(modele, ident) is None, f"{modele.__name__} non supprimé"
