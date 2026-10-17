"""Les tests ne dépendent d'aucun service extérieur (aucun_appel_reseau_externe, conftest.py)."""

import socket

import pytest


def test_appel_reseau_externe_refuse():
    # 192.0.2.1 : adresse réservée à la documentation (RFC 5737), jamais routée.
    with pytest.raises(RuntimeError, match="Appel réseau externe interdit"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)


def test_connexion_locale_permise():
    serveur = socket.create_server(("127.0.0.1", 0))
    with serveur, socket.create_connection(serveur.getsockname(), timeout=1):
        pass
