"""Environnement Alembic : URL lue depuis app/config.py, types PostGIS gérés par GeoAlchemy2."""

from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Objets créés par l'extension PostGIS et ses compléments : l'autogénération ne doit
# ni les comparer aux modèles, ni proposer de les supprimer.
TABLES_POSTGIS = {"spatial_ref_sys"}
SCHEMAS_POSTGIS = {"tiger", "tiger_data", "topology"}


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name not in SCHEMAS_POSTGIS
    if type_ == "table":
        return name not in TABLES_POSTGIS and parent_names.get("schema_name") not in SCHEMAS_POSTGIS
    return True


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and obj.schema in SCHEMAS_POSTGIS:
        return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


OPTIONS_AUTOGENERATION = {
    "target_metadata": target_metadata,
    "include_name": include_name,
    "include_object": include_object,
    "process_revision_directives": alembic_helpers.writer,
    "render_item": alembic_helpers.render_item,
}


def url_base() -> str:
    # Les tests fournissent l'URL de la base de test ; sinon, DATABASE_URL du fichier .env.
    return config.attributes.get("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter (alembic upgrade head --sql)."""
    context.configure(
        url=url_base(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **OPTIONS_AUTOGENERATION,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    moteur = create_engine(url_base(), poolclass=pool.NullPool)
    with moteur.connect() as connexion:
        context.configure(connection=connexion, **OPTIONS_AUTOGENERATION)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
