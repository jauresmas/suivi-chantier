import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import engine, get_db
from app.main import app


class FausseSession:
    """Remplace la session SQLAlchemy : renvoie une version PostGIS ou lève une erreur."""

    def __init__(self, version=None, erreur=None):
        self.version = version
        self.erreur = erreur

    def execute(self, _requete):
        if self.erreur:
            raise self.erreur
        return self

    def scalar_one(self):
        return self.version


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_ok(client):
    app.dependency_overrides[get_db] = lambda: FausseSession(version="3.4 USE_GEOS=1 USE_PROJ=1")

    reponse = client.get("/health")

    assert reponse.status_code == 200
    assert reponse.json() == {
        "status": "ok",
        "database": "ok",
        "postgis_version": "3.4 USE_GEOS=1 USE_PROJ=1",
    }


def test_health_base_inaccessible(client):
    erreur = OperationalError("SELECT PostGIS_Version()", {}, Exception("connexion refusée"))
    app.dependency_overrides[get_db] = lambda: FausseSession(erreur=erreur)

    reponse = client.get("/health")

    assert reponse.status_code == 503
    assert reponse.json() == {"status": "error", "database": "unavailable", "postgis_version": None}


def test_health_avec_la_vraie_base(client):
    try:
        with engine.connect() as connexion:
            connexion.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Base PostgreSQL inaccessible : vérifier DATABASE_URL dans .env")

    reponse = client.get("/health")

    assert reponse.status_code == 200
    assert reponse.json()["status"] == "ok"
    assert reponse.json()["postgis_version"]
