"""Lecture de coordonnées collées par l'utilisateur : texte « lat, lon » ou lien Google Maps /
WhatsApp.

Fonctions pures, utilisées par le serveur. Le JavaScript de la page n'en fait qu'un aperçu.
"""

import math
import re
from urllib.parse import parse_qs, unquote, urlparse

DECIMALES = 6  # ~10 cm
RAYON_TERRE_M = 6_371_008.8

NOMBRE = r"[-+]?\d{1,3}(?:\.\d+)?"
# « 6.4512, 2.3501 », « 6.4512,2.3501 » ou « 6.4512 2.3501 » : latitude puis longitude, point
# décimal.
MOTIF_TEXTE = re.compile(rf"^\s*({NOMBRE})\s*(?:,\s*|\s+)({NOMBRE})\s*$")
# Position exacte d'un lieu dans les liens Google Maps : « !3d6.4512!4d2.3501 ».
MOTIF_REPERE = re.compile(rf"!3d({NOMBRE})!4d({NOMBRE})")
# Centre de la vue : « /@6.4512,2.3501,17z ».
MOTIF_AROBASE = re.compile(rf"@({NOMBRE}),({NOMBRE})")
MOTIF_URL = re.compile(r"https?://\S+", re.IGNORECASE)
PARAMETRES_POSITION = ("q", "query", "ll")
DOMAINES_RACCOURCIS = ("goo.gl", "maps.app.goo.gl", "g.co")


class CoordonneesInvalides(ValueError):
    """Le message est destiné à l'utilisateur."""


def lire_coordonnees(texte: str | None) -> tuple[float, float]:
    """Renvoie (latitude, longitude) arrondies à 6 décimales, ou lève CoordonneesInvalides."""
    texte = (texte or "").strip()
    if not texte:
        raise CoordonneesInvalides(
            "Collez des coordonnées (ex. 6.4512, 2.3501) ou un lien de position."
        )

    if url := MOTIF_URL.search(texte):
        lat, lon = _depuis_lien(url.group(0))
    else:
        trouve = MOTIF_TEXTE.match(texte)
        if not trouve:
            raise CoordonneesInvalides(
                "Format non reconnu. Écrivez la latitude puis la longitude "
                "avec un point décimal : 6.4512, 2.3501"
            )
        if "." not in trouve.group(1) and "." not in trouve.group(2):
            # « 6,45 » : virgule décimale probable, pas des degrés entiers.
            raise CoordonneesInvalides("Précisez les décimales avec un point : 6.4512, 2.3501")
        lat, lon = float(trouve.group(1)), float(trouve.group(2))
    return valider_position(lat, lon)


def _depuis_lien(url: str) -> tuple[float, float]:
    hote = (urlparse(url).hostname or "").lower()
    if hote in DOMAINES_RACCOURCIS:
        raise CoordonneesInvalides(
            "Lien raccourci : ouvrez-le dans Google Maps, "
            "puis copiez le lien complet ou les coordonnées."
        )
    decode = unquote(url)
    if trouve := MOTIF_REPERE.search(decode):
        return float(trouve.group(1)), float(trouve.group(2))
    parametres = parse_qs(urlparse(url).query)
    for nom in PARAMETRES_POSITION:
        for valeur in parametres.get(nom, []):
            if trouve := MOTIF_TEXTE.match(valeur.removeprefix("loc:")):
                return float(trouve.group(1)), float(trouve.group(2))
    if trouve := MOTIF_AROBASE.search(decode):
        return float(trouve.group(1)), float(trouve.group(2))
    raise CoordonneesInvalides(
        "Ce lien ne contient pas de coordonnées. Copiez plutôt la latitude et la longitude."
    )


def valider_position(latitude: float, longitude: float) -> tuple[float, float]:
    """Vérifie les bornes et arrondit à 6 décimales."""
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        raise CoordonneesInvalides("Latitude et longitude doivent être des nombres.")
    if not -90 <= latitude <= 90:
        message = f"Latitude {latitude:g} impossible : elle doit être comprise entre -90 et 90."
        if -90 <= longitude <= 90 and -180 <= latitude <= 180:
            message += " Latitude et longitude sont peut-être inversées."
        raise CoordonneesInvalides(message)
    if not -180 <= longitude <= 180:
        raise CoordonneesInvalides(
            f"Longitude {longitude:g} impossible : elle doit être comprise entre -180 et 180."
        )
    return round(latitude, DECIMALES), round(longitude, DECIMALES)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau (formule de haversine), suffisante pour un contrôle de
    vraisemblance."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * RAYON_TERRE_M * math.asin(math.sqrt(a))
