"""Limitation des tentatives de connexion : 5 échecs par e-mail en 15 minutes, puis blocage."""

import math
import time
from collections import deque
from threading import Lock


class LimiteurTentatives:
    """Garde les échecs récents en mémoire du processus.

    Suffisant avec un seul processus uvicorn. Avec plusieurs processus ou serveurs,
    il faudra stocker les échecs en base (ou dans Redis) pour qu'ils soient partagés.
    """

    def __init__(self, max_echecs: int = 5, fenetre_s: int = 15 * 60):
        self.max_echecs = max_echecs
        self.fenetre_s = fenetre_s
        self._echecs: dict[str, deque[float]] = {}
        self._verrou = Lock()

    def _echecs_recents(self, cle: str, maintenant: float) -> deque[float]:
        echecs = self._echecs.get(cle, deque())
        while echecs and maintenant - echecs[0] >= self.fenetre_s:
            echecs.popleft()
        if echecs:
            self._echecs[cle] = echecs
        else:
            self._echecs.pop(cle, None)
        return echecs

    def secondes_de_blocage(self, cle: str) -> int:
        """0 si la connexion est permise, sinon le temps d'attente restant."""
        with self._verrou:
            maintenant = time.monotonic()
            echecs = self._echecs_recents(cle, maintenant)
            if len(echecs) < self.max_echecs:
                return 0
            return math.ceil(self.fenetre_s - (maintenant - echecs[0]))

    def enregistrer_echec(self, cle: str) -> None:
        with self._verrou:
            maintenant = time.monotonic()
            echecs = self._echecs_recents(cle, maintenant)
            echecs.append(maintenant)
            self._echecs[cle] = echecs

    def reinitialiser(self, cle: str | None = None) -> None:
        with self._verrou:
            if cle is None:
                self._echecs.clear()
            else:
                self._echecs.pop(cle, None)


limiteur_connexions = LimiteurTentatives()
