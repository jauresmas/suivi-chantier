"""Connexion à PostgreSQL/PostGIS via SQLAlchemy."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def creer_moteur(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            # Échoue vite si le serveur ne répond pas, au lieu de bloquer la requête.
            "connect_timeout": 5,
            # Les dates relues depuis la base sont exprimées en UTC.
            "options": "-c timezone=UTC",
        },
    )


engine = creer_moteur(get_settings().database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dépendance FastAPI : ouvre une session par requête et la ferme ensuite."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
