import ipaddress
import socket
from contextlib import nullcontext

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.limitation import limiteur_connexions
from app.config import BASE_DIR, get_settings
from app.database import creer_moteur, get_db
from app.emails import envoi, expediteurs
from app.emails.expediteurs import ExpediteurMemoire
from app.geo.nominatim import nominatim
from app.main import app
from tests.outils import FauxNominatim


def adresse_locale(hote: str) -> bool:
    try:
        return ipaddress.ip_address(hote).is_loopback
    except ValueError:
        return hote == "localhost"


@pytest.fixture(autouse=True, scope="session")
def aucun_appel_reseau_externe():
    """Les tests n'appellent aucun service extérieur (Nominatim, SMTP, fonds de carte…) : toute
    connexion vers une adresse non locale échoue. PostgreSQL passe par libpq, hors de ce module."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def verifier(sock: socket.socket, adresse) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not adresse_locale(adresse[0]):
            raise RuntimeError(f"Appel réseau externe interdit pendant les tests : {adresse}")

    def connect_verifie(sock: socket.socket, adresse):
        verifier(sock, adresse)
        return connect(sock, adresse)

    def connect_ex_verifie(sock: socket.socket, adresse):
        verifier(sock, adresse)
        return connect_ex(sock, adresse)

    socket.socket.connect, socket.socket.connect_ex = connect_verifie, connect_ex_verifie
    yield
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex


@pytest.fixture(autouse=True)
def boite_mail(monkeypatch) -> ExpediteurMemoire:
    """Aucun e-mail réel ni affiché : ils sont gardés en mémoire (boite_mail.envoyes)."""
    boite = ExpediteurMemoire()
    monkeypatch.setattr(expediteurs, "obtenir_expediteur", lambda: boite)
    return boite


@pytest.fixture(autouse=True)
def faux_nominatim(monkeypatch) -> FauxNominatim:
    """Aucun appel réseau réel : Nominatim est simulé dans tous les tests, sans pause d'une
    seconde."""
    faux = FauxNominatim()
    monkeypatch.setattr(nominatim, "transport", faux)
    monkeypatch.setattr(nominatim, "dormir", faux.attentes.append)
    nominatim.vider_cache()
    yield faux
    nominatim.vider_cache()


def url_base_test() -> str:
    settings = get_settings()
    if settings.test_database_url:
        return settings.test_database_url
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def moteur_test():
    """Base de test remise à zéro par les migrations Alembic au début de la session."""
    url = url_base_test()
    if not make_url(url).database.endswith("_test"):
        pytest.exit(
            "TEST_DATABASE_URL doit viser une base dont le nom finit par _test : "
            "les tests la vident."
        )

    moteur = creer_moteur(url)
    try:
        with moteur.connect():
            pass
    except OperationalError:
        pytest.skip(
            f"Base de test {make_url(url).database} inaccessible : voir « Tests » dans le README"
        )

    config = Config(str(BASE_DIR / "alembic.ini"))
    config.attributes["sqlalchemy.url"] = url
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    yield moteur
    moteur.dispose()


@pytest.fixture
def session(moteur_test, monkeypatch):
    """Session dont tout le travail est annulé à la fin du test."""
    with moteur_test.connect() as connexion:
        transaction = connexion.begin()
        session = Session(bind=connexion, join_transaction_mode="create_savepoint")
        # Les envois en tâche de fond notent leur résultat dans cette même session.
        monkeypatch.setattr(envoi, "ouvrir_session", lambda: nullcontext(session))
        yield session
        session.close()
        transaction.rollback()


@pytest.fixture
def navigateurs(session):
    """Fabrique de navigateurs indépendants (chacun ses cookies), branchés sur la base de test.

    Deux navigateurs dans un même test simulent par exemple le propriétaire et une
    fenêtre de navigation privée.
    """
    app.dependency_overrides[get_db] = lambda: session
    limiteur_connexions.reinitialiser()
    ouverts = []

    def ouvrir() -> TestClient:
        client = TestClient(app)
        ouverts.append(client)
        return client

    yield ouvrir
    for client in ouverts:
        client.close()
    app.dependency_overrides.clear()
    limiteur_connexions.reinitialiser()


@pytest.fixture
def navigateur(navigateurs) -> TestClient:
    return navigateurs()
