"""Connexion à PostgreSQL/PostGIS via SQLAlchemy."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    # Échoue vite si le serveur ne répond pas, au lieu de bloquer la requête.
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : ouvre une session par requête et la ferme ensuite."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
