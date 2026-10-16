# Suivi de chantier

Application web de suivi de chantiers : API FastAPI adossée à PostgreSQL / PostGIS, avec des pages HTML rendues par Jinja2.

## Stack

- Python 3.12, FastAPI, Uvicorn
- PostgreSQL + PostGIS
- SQLAlchemy 2, GeoAlchemy2, Alembic
- Jinja2 pour les templates HTML
- pytest pour les tests

## Prérequis

- **Python 3.12** : vérifier avec `python --version` (sous Windows, `py -3.12 --version` si le lanceur `py` est installé).
- **PostgreSQL avec l'extension PostGIS**, et une base `suivi_chantier` où PostGIS est activé :

```sql
CREATE DATABASE suivi_chantier;
\c suivi_chantier
CREATE EXTENSION IF NOT EXISTS postgis;
```

## Installation

### Windows (PowerShell)

```powershell
# 1. Créer l'environnement virtuel (ou : py -3.12 -m venv venv)
python -m venv venv

# 2. L'activer
.\venv\Scripts\Activate.ps1

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Créer le fichier de configuration, puis l'éditer
Copy-Item .env.example .env
```

Si PowerShell refuse d'exécuter `Activate.ps1`, autoriser les scripts locaux pour votre compte, une fois pour toutes :

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Depuis l'invite `cmd`, l'activation se fait avec `venv\Scripts\activate.bat`.

### Linux / macOS

```bash
# 1. Créer l'environnement virtuel
python3.12 -m venv venv

# 2. L'activer
source venv/bin/activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Créer le fichier de configuration, puis l'éditer
cp .env.example .env
```

## Configuration

Le fichier `.env` (jamais versionné) contient :

| Variable | Rôle | Exemple |
|---|---|---|
| `DATABASE_URL` | Connexion PostgreSQL | `postgresql+psycopg://postgres:motdepasse@localhost:5432/suivi_chantier` |
| `APP_ENV` | `development` ou `production` | `development` |
| `SECRET_KEY` | Clé secrète de l'application | générée avec `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

Si le mot de passe contient des caractères spéciaux, les encoder dans l'URL : `@` → `%40`, `:` → `%3A`, `/` → `%2F`.

En production (`APP_ENV=production`), l'application refuse de démarrer sans `SECRET_KEY`.

## Lancement

L'environnement virtuel étant activé, depuis la racine du projet :

```bash
uvicorn app.main:app --reload
```

| Adresse | Contenu |
|---|---|
| http://localhost:8000/ | Page d'accueil |
| http://localhost:8000/health | État du service et version de PostGIS |
| http://localhost:8000/docs | Documentation OpenAPI interactive |

`/health` renvoie `{"status": "ok", "database": "ok", "postgis_version": "..."}`, ou un code **503** si la base est inaccessible.

## Tests

```bash
pytest
```

Les deux premiers tests simulent la base de données. Le troisième interroge la vraie base définie dans `.env` : il est ignoré (`skipped`) si elle est inaccessible.

## Arborescence

```
suivi-chantier/
├── app/
│   ├── main.py          # application FastAPI, page d'accueil
│   ├── config.py        # lecture du .env
│   ├── database.py      # moteur SQLAlchemy, dépendance get_db
│   ├── routers/
│   │   └── health.py    # GET /health
│   ├── templates/
│   │   └── base.html
│   └── static/
├── tests/
│   └── test_health.py
├── .env.example
├── requirements.txt
└── README.md
```
