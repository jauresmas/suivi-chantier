"""Configuration de l'application, lue depuis les variables d'environnement et le fichier .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

CLE_PAR_DEFAUT = "dev-insecure-changez-moi"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/suivi_chantier"
    app_env: str = "development"
    secret_key: str = CLE_PAR_DEFAUT

    @field_validator("database_url")
    @classmethod
    def utiliser_psycopg3(cls, url: str) -> str:
        # Sans précision du pilote, SQLAlchemy chercherait psycopg2, qui n'est pas installé.
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @model_validator(mode="after")
    def refuser_cle_par_defaut_en_production(self) -> "Settings":
        if self.app_env == "production" and self.secret_key == CLE_PAR_DEFAUT:
            raise ValueError("SECRET_KEY doit être définie en production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
