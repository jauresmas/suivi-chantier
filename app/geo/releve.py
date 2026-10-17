"""Calcul de la position d'un chantier à partir des mesures d'un relevé fait sur le terrain.

Le navigateur envoie toutes ses mesures brutes (latitude, longitude, précision annoncée,
horodatage) : le calcul est fait ici, jamais dans le navigateur.

1. les mesures annoncées à plus de 50 m sont ignorées ;
2. les valeurs aberrantes sont écartées : plus de 3 fois l'écart médian loin de la médiane ;
3. la position est la moyenne pondérée par 1 / précision² des mesures restantes ;
4. la précision estimée est l'écart-type des positions autour de ce point : racine de la
   moyenne des carrés des distances, calculées en mètres par PostGIS (geography).
"""

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

PRECISION_MAX_M = 50
FACTEUR_ABERRANT = 3
# Mesures très groupées : l'écart médian peut tomber à quelques centimètres ; sous ce rayon,
# une mesure n'est jamais jugée aberrante.
SEUIL_ABERRANT_MIN_M = 2
MESURES_MIN = 10
PRECISION_FINALE_MAX_M = 25
SEUIL_SIGNAL_BON_M = 10

MESURES_MAX = 1000
DUREE_MAX = timedelta(minutes=15)
MESURES_PAR_SECONDE_MAX = (
    5  # watchPosition en livre environ une par seconde ; marge pour les appareils plus rapides
)
TOLERANCE_HORLOGE = timedelta(seconds=5)
RAYON_TERRE_M = 6_371_008.8


class ReleveInvalide(ValueError):
    """Mesures incohérentes (hors bornes, dans le futur, trop nombreuses pour la durée…)."""


@dataclass(frozen=True)
class Mesure:
    latitude: float
    longitude: float
    precision_m: float
    horodatage: datetime


@dataclass
class Resultat:
    latitude: float
    longitude: float
    precision_estimee_m: float
    retenues: list[Mesure]
    nb_recues: int
    nb_imprecises: int  # annoncées à plus de 50 m
    nb_aberrantes: int
    duree_s: int

    @property
    def signal_faible(self) -> bool:
        return len(self.retenues) < MESURES_MIN or self.precision_estimee_m > PRECISION_FINALE_MAX_M

    def point(self) -> WKTElement:
        return WKTElement(f"POINT({self.longitude} {self.latitude})", srid=4326)

    def multipoint(self) -> WKTElement:
        """Mesures retenues, conservées pour l'audit (visibles dans QGIS)."""
        points = ", ".join(f"({m.longitude} {m.latitude})" for m in self.retenues)
        return WKTElement(f"MULTIPOINT({points})", srid=4326)


# --- Validation des mesures reçues
# -----------------------------------------------------------------


def lire_mesures(
    brutes: list[tuple[float, float, float, float]],
    horloge_client_ms: float,
    maintenant: datetime,
    lien_cree_le: datetime,
) -> list[Mesure]:
    """Contrôle les mesures (latitude, longitude, précision en m, horodatage en ms) et les date.

    L'horloge d'un téléphone peut être déréglée : le navigateur envoie aussi son heure au moment
    de l'envoi, et les horodatages sont recalés sur l'horloge du serveur.
    """
    if not brutes:
        raise ReleveInvalide("Aucune mesure reçue.")
    if len(brutes) > MESURES_MAX:
        raise ReleveInvalide(f"Trop de mesures : {MESURES_MAX} au maximum.")
    if not math.isfinite(horloge_client_ms):
        raise ReleveInvalide("Heure du téléphone illisible.")
    decalage = maintenant - _depuis_ms(horloge_client_ms)

    mesures = []
    for latitude, longitude, precision_m, horodatage_ms in dict.fromkeys(
        brutes
    ):  # doublons exacts ignorés
        if not all(math.isfinite(x) for x in (latitude, longitude, precision_m, horodatage_ms)):
            raise ReleveInvalide("Mesure illisible.")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ReleveInvalide("Mesure hors des limites du globe.")
        if precision_m <= 0:
            raise ReleveInvalide("Précision de mesure impossible.")
        horodatage = _depuis_ms(horodatage_ms) + decalage
        if horodatage > maintenant + TOLERANCE_HORLOGE:
            raise ReleveInvalide("Mesure datée après l'envoi.")
        if horodatage < lien_cree_le - TOLERANCE_HORLOGE:
            raise ReleveInvalide("Mesure datée d'avant la création du lien.")
        mesures.append(Mesure(latitude, longitude, precision_m, horodatage))

    mesures.sort(key=lambda m: m.horodatage)
    duree = mesures[-1].horodatage - mesures[0].horodatage
    if duree > DUREE_MAX:
        raise ReleveInvalide("Relevé trop long.")
    if len(mesures) > MESURES_PAR_SECONDE_MAX * duree.total_seconds() + 1:
        raise ReleveInvalide("Trop de mesures pour la durée du relevé.")
    return mesures


def _depuis_ms(ms: float) -> datetime:
    try:
        return datetime.fromtimestamp(ms / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        raise ReleveInvalide("Horodatage impossible.") from None


# --- Calcul
# ----------------------------------------------------------------------------------------


def distance_locale_m(mesure: Mesure, latitude: float, longitude: float) -> float:
    """Distance approchée sur un plan tangent local : largement suffisante à l'échelle d'un
    terrain."""
    dy = math.radians(mesure.latitude - latitude) * RAYON_TERRE_M
    dx = (
        math.radians(mesure.longitude - longitude)
        * math.cos(math.radians(latitude))
        * RAYON_TERRE_M
    )
    return math.hypot(dx, dy)


def filtrer(mesures: list[Mesure]) -> tuple[list[Mesure], int, int]:
    """Renvoie (mesures retenues, nombre d'imprécises, nombre d'aberrantes)."""
    precises = [m for m in mesures if m.precision_m <= PRECISION_MAX_M]
    if not precises:
        return [], len(mesures), 0
    lat_mediane = statistics.median(m.latitude for m in precises)
    lon_mediane = statistics.median(m.longitude for m in precises)
    distances = [distance_locale_m(m, lat_mediane, lon_mediane) for m in precises]
    seuil = max(FACTEUR_ABERRANT * statistics.median(distances), SEUIL_ABERRANT_MIN_M)
    retenues = [m for m, d in zip(precises, distances, strict=True) if d <= seuil]
    return retenues, len(mesures) - len(precises), len(precises) - len(retenues)


def moyenne_ponderee(mesures: list[Mesure]) -> tuple[float, float]:
    """Moyenne pondérée par 1 / précision² : une mesure à ±5 m pèse 4 fois plus qu'une à ±10 m."""
    poids = [1 / m.precision_m**2 for m in mesures]
    total = sum(poids)
    latitude = sum(p * m.latitude for p, m in zip(poids, mesures, strict=True)) / total
    longitude = sum(p * m.longitude for p, m in zip(poids, mesures, strict=True)) / total
    return latitude, longitude


_ECART_TYPE = text("""
    SELECT sqrt(avg(power(ST_Distance(
        ST_SetSRID(ST_MakePoint(m.lon, m.lat), 4326)::geography,
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
    ), 2)))
    FROM unnest(CAST(:lons AS double precision[]), CAST(:lats AS double precision[])) AS m(lon, lat)
""")


def ecart_type_m(db: Session, mesures: list[Mesure], latitude: float, longitude: float) -> float:
    return db.scalar(
        _ECART_TYPE,
        {
            "lat": latitude,
            "lon": longitude,
            "lats": [m.latitude for m in mesures],
            "lons": [m.longitude for m in mesures],
        },
    )


def calculer(db: Session, mesures: list[Mesure]) -> Resultat | None:
    """Position et précision estimée ; None si aucune mesure n'est exploitable."""
    retenues, imprecises, aberrantes = filtrer(mesures)
    if not retenues:
        return None
    latitude, longitude = moyenne_ponderee(retenues)
    return Resultat(
        latitude=latitude,
        longitude=longitude,
        precision_estimee_m=ecart_type_m(db, retenues, latitude, longitude),
        retenues=retenues,
        nb_recues=len(mesures),
        nb_imprecises=imprecises,
        nb_aberrantes=aberrantes,
        duree_s=round((mesures[-1].horodatage - mesures[0].horodatage).total_seconds()),
    )


# --- Affichage
# -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QualiteSignal:
    libelle: str  # Bon, Moyen, Faible
    ton: str
    icone: str


def qualite_signal(precision_m: float | None, signal_faible: bool) -> QualiteSignal:
    """Mêmes seuils que l'indicateur affiché pendant le relevé (releve.js)."""
    if signal_faible or precision_m is None or precision_m > PRECISION_FINALE_MAX_M:
        return QualiteSignal("Faible", "danger", "signal-low")
    if precision_m > SEUIL_SIGNAL_BON_M:
        return QualiteSignal("Moyen", "alerte", "signal-medium")
    return QualiteSignal("Bon", "succes", "signal-high")
