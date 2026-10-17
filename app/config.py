"""Configuration de l'application, lue depuis les variables d'environnement et le fichier .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

CLE_PAR_DEFAUT = "dev-insecure-changez-moi"

ESRI_IMAGERIE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_IMAGERIE_ATTRIBUTION = "Imagerie © Esri, Maxar, Earthstar Geographics, GIS User Community"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/suivi_chantier"
    # Base dédiée aux tests ; à défaut, celle de DATABASE_URL suffixée par _test.
    test_database_url: str | None = None
    app_env: str = "development"
    secret_key: str = CLE_PAR_DEFAUT
    # Mot de passe des comptes créés par scripts/seed.py
    seed_password: str | None = None

    # Fonds de carte : changer de fournisseur sans toucher au code.
    fond_plan_style_url: str = "https://tiles.openfreemap.org/styles/liberty"
    fond_satellite_tiles_url: str | None = None
    fond_satellite_attribution: str | None = None

    # Vérification des photos : tolérance = min(précision GPS, ce plafond) ; au-delà de
    # PRECISION_INEXPLOITABLE_M, la position est jugée trop imprécise pour conclure.
    tolerance_precision_max_m: float = 50
    precision_inexploitable_m: float = 200

    # Tableau de bord : alerte « Aucune nouvelle depuis N jours » à partir de ce seuil.
    jours_inactivite: int = 7

    # Photos ré-encodées : {chantier_id}/{AAAA}/{MM}/{uuid}.jpg. Jamais servi en statique.
    stockage_photos_dir: Path = BASE_DIR / "stockage" / "photos"

    # Recherche de lieux (politique d'usage Nominatim : User-Agent identifiant l'application)
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    nominatim_user_agent: str = "suivi-chantier/0.1 (+https://github.com/jauresmas/suivi-chantier)"

    # Adresse publique du site : les liens des e-mails sont absolus (envoyés hors de toute requête).
    url_publique: str = "http://localhost:8000"

    # E-mails : « console » (affichés dans les journaux) ou « smtp ». Par défaut : console en
    # développement, smtp en production.
    email_backend: str | None = None
    email_expediteur: str = "Suivi Chantier <notifications@localhost>"
    smtp_host: str | None = None
    smtp_port: int = 587  # 587 : STARTTLS ; 465 : TLS dès la connexion
    smtp_user: str | None = None
    smtp_password: str | None = None

    @field_validator("database_url", "test_database_url")
    @classmethod
    def utiliser_psycopg3(cls, url: str | None) -> str | None:
        # Sans précision du pilote, SQLAlchemy chercherait psycopg2, qui n'est pas installé.
        if url and url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @model_validator(mode="after")
    def refuser_cle_par_defaut_en_production(self) -> "Settings":
        if self.app_env == "production" and self.secret_key == CLE_PAR_DEFAUT:
            raise ValueError("SECRET_KEY doit être définie en production.")
        return self

    @model_validator(mode="after")
    def envoi_des_emails(self) -> "Settings":
        if self.email_backend is None:
            self.email_backend = "smtp" if self.app_env == "production" else "console"
        if self.email_backend not in ("console", "smtp"):
            raise ValueError("EMAIL_BACKEND doit valoir « console » ou « smtp ».")
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError(
                "SMTP_HOST doit être défini pour envoyer les e-mails (EMAIL_BACKEND=smtp)."
            )
        self.url_publique = self.url_publique.rstrip("/")
        return self

    @model_validator(mode="after")
    def fond_satellite(self) -> "Settings":
        # Indispensable sur le terrain (voies sans nom) : imagerie Esri par défaut en développement,
        # fournisseur à choisir explicitement en production (conditions d'utilisation).
        if self.fond_satellite_tiles_url is None:
            if self.app_env == "production":
                raise ValueError("FOND_SATELLITE_TILES_URL doit être défini en production.")
            self.fond_satellite_tiles_url = ESRI_IMAGERIE_URL
            self.fond_satellite_attribution = (
                self.fond_satellite_attribution or ESRI_IMAGERIE_ATTRIBUTION
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
