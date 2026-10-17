"""Recherche de lieux avec Nominatim (OpenStreetMap), appelée côté serveur.

Politique d'usage (https://operations.osmfoundation.org/policies/nominatim/) :
User-Agent qui identifie l'application, 1 requête par seconde au maximum, résultats en cache.
"""

import json
import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import get_settings

INTERVALLE_MIN_S = 1.0
DUREE_CACHE_S = 24 * 3600
TAILLE_MAX_CACHE = 1000
DELAI_RESEAU_S = 5


class NominatimIndisponible(Exception):
    pass


def requete_http(url: str, user_agent: str, delai_s: float) -> list[dict]:
    """Seul point de contact avec le réseau ; remplacé par un faux dans les tests."""
    requete = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(requete, timeout=delai_s) as reponse:
        return json.load(reponse)


class ClientNominatim:
    def __init__(
        self,
        transport: Callable[[str, str, float], list[dict]] = requete_http,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self.transport = transport
        self.dormir = dormir
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._verrou = threading.Lock()
        self._dernier_appel = float("-inf")

    def vider_cache(self) -> None:
        with self._verrou:
            self._cache.clear()
            self._dernier_appel = float("-inf")

    def rechercher(self, texte: str, pays: str | None = None, limite: int = 5) -> list[dict]:
        """Lieux correspondant à `texte` : [{nom, lat, lon, bbox: [ouest, sud, est, nord]}]."""
        texte = " ".join(texte.split())
        cle = (texte.lower(), (pays or "").lower(), limite)
        with self._verrou:  # sérialise les appels : jamais plus d'une requête par seconde
            en_cache = self._cache.get(cle)
            if en_cache and time.monotonic() - en_cache[0] < DUREE_CACHE_S:
                return en_cache[1]

            attente = INTERVALLE_MIN_S - (time.monotonic() - self._dernier_appel)
            if attente > 0:
                self.dormir(attente)

            settings = get_settings()
            parametres = {"q": texte, "format": "jsonv2", "limit": limite, "accept-language": "fr"}
            if pays:
                parametres["countrycodes"] = pays.lower()
            try:
                bruts = self.transport(
                    f"{settings.nominatim_url}?{urlencode(parametres)}",
                    settings.nominatim_user_agent,
                    DELAI_RESEAU_S,
                )
                resultats = [_simplifier(r) for r in bruts]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise NominatimIndisponible(str(exc)) from exc
            finally:
                self._dernier_appel = time.monotonic()

            if len(self._cache) >= TAILLE_MAX_CACHE:
                self._cache.pop(next(iter(self._cache)))  # le plus ancien
            self._cache[cle] = (time.monotonic(), resultats)
            return resultats

    def geocoder_ville(self, ville: str, pays: str | None = None) -> tuple[float, float] | None:
        """(latitude, longitude) du premier résultat, ou None si la ville est introuvable ou le
        service indisponible."""
        try:
            resultats = self.rechercher(ville, pays=pays, limite=1)
        except NominatimIndisponible:
            return None
        return (resultats[0]["lat"], resultats[0]["lon"]) if resultats else None


def _simplifier(resultat: dict) -> dict:
    sud, nord, ouest, est = (float(v) for v in resultat["boundingbox"])
    return {
        "nom": resultat["display_name"],
        "lat": float(resultat["lat"]),
        "lon": float(resultat["lon"]),
        "bbox": [ouest, sud, est, nord],
    }


nominatim = ClientNominatim()
