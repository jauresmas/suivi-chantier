# Suivi de chantier

[![CI](https://github.com/jauresmas/suivi-chantier/actions/workflows/ci.yml/badge.svg)](https://github.com/jauresmas/suivi-chantier/actions/workflows/ci.yml)

Application web de suivi de chantiers : API FastAPI adossée à PostgreSQL / PostGIS, avec des pages HTML rendues par Jinja2.

## Stack

- Python 3.12, FastAPI, Uvicorn
- PostgreSQL + PostGIS
- SQLAlchemy 2, GeoAlchemy2, Alembic
- Jinja2 pour les templates HTML, HTMX pour le chargement progressif
- Tailwind CSS (exécutable autonome), police Inter, icônes Lucide
- pytest et Ruff, vérifiés par GitHub Actions à chaque push et pull request

## Prérequis

- **Python 3.12** : vérifier avec `python --version` (sous Windows, `py -3.12 --version` si le lanceur `py` est installé).
- **PostgreSQL avec l'extension PostGIS**, et une base `suivi_chantier` où PostGIS est activé :

```sql
CREATE DATABASE suivi_chantier;
\c suivi_chantier
CREATE EXTENSION IF NOT EXISTS postgis;
```

- Pour les tests, une seconde base `suivi_chantier_test`, elle aussi avec PostGIS :

```sql
CREATE DATABASE suivi_chantier_test;
\c suivi_chantier_test
CREATE EXTENSION IF NOT EXISTS postgis;
```

## Installation

### Windows (PowerShell)

```powershell
# 1. Créer l'environnement virtuel (ou : py -3.12 -m venv venv)
python -m venv venv

# 2. L'activer
.\venv\Scripts\Activate.ps1

# 3. Installer les dépendances (requirements-dev.txt ajoute pytest et Ruff à requirements.txt)
pip install -r requirements-dev.txt

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

# 3. Installer les dépendances (requirements-dev.txt ajoute pytest et Ruff à requirements.txt)
pip install -r requirements-dev.txt

# 4. Créer le fichier de configuration, puis l'éditer
cp .env.example .env
```

## Configuration

Le fichier `.env` (jamais versionné) contient :

| Variable | Rôle | Exemple |
|---|---|---|
| `DATABASE_URL` | Connexion PostgreSQL | `postgresql+psycopg://postgres:motdepasse@localhost:5432/suivi_chantier` |
| `TEST_DATABASE_URL` | Base des tests (facultatif, par défaut `DATABASE_URL` suffixée par `_test`) | `postgresql+psycopg://postgres:motdepasse@localhost:5432/suivi_chantier_test` |
| `APP_ENV` | `development` ou `production` | `development` |
| `SECRET_KEY` | Clé secrète de l'application | générée avec `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `URL_PUBLIQUE` | Adresse du site, pour les liens des e-mails | `https://chantier.exemple.com` (défaut : `http://localhost:8000`) |
| `EMAIL_BACKEND` | `console` (e-mails affichés dans les journaux) ou `smtp` | `console` en développement, `smtp` en production |
| `EMAIL_EXPEDITEUR` | Expéditeur des e-mails | `Suivi Chantier <notifications@exemple.com>` |
| `SMTP_HOST`, `SMTP_PORT` | Serveur d'envoi (587 : STARTTLS ; 465 : TLS direct) | `smtp-relay.brevo.com`, `587` |
| `SMTP_USER`, `SMTP_PASSWORD` | Identifiants SMTP | fournis par le service d'envoi |

Si le mot de passe contient des caractères spéciaux, les encoder dans l'URL : `@` → `%40`, `:` → `%3A`, `/` → `%2F`.

En production (`APP_ENV=production`), l'application refuse de démarrer sans `SECRET_KEY`, et sans `SMTP_HOST` (les e-mails y passent par SMTP).

## Base de données

### Modèle

| Table | Contenu |
|---|---|
| `users` | Comptes : propriétaires et chefs de chantier |
| `chantiers` | Chantiers, avec leur position (`geography(POINT, 4326)`, vide tant qu'elle n'est pas définie), son statut et sa source, et le rayon de vérification des photos |
| `releves_position` | Relevés de la position faits sur le terrain par un proche : lien (jeton haché), mesures retenues (`MULTIPOINT`), point calculé, précision, décision du propriétaire |
| `photos_releve` | Photos du terrain prises pendant un relevé |
| `historique_position_chantier` | Chaque changement de position : ancienne et nouvelle position, statuts, source, auteur |
| `membres_chantier` | Rôle de chaque utilisateur sur chaque chantier (`proprietaire`, `chef_chantier`) |
| `etapes` | Étapes de construction ordonnées, avec leur avancement en % |
| `rapports` | Rapports de visite, rattachés à un chantier et éventuellement à une étape |
| `photos` | Photos des rapports, avec position GPS, source de la position et statut de vérification |

Les clés primaires sont des UUID. Les colonnes géographiques sont en `geography` : `ST_Distance` et `ST_DWithin` y travaillent directement en mètres, et chacune a un index spatial GiST. Les dates sont en `TIMESTAMP WITH TIME ZONE`, relues en UTC.

### Migrations (Alembic)

```bash
# Créer ou mettre à jour le schéma
alembic upgrade head

# Revenir à une base vide (supprime aussi les types ENUM)
alembic downgrade base

# Après une modification des modèles : générer la migration, puis la relire
alembic revision --autogenerate -m "description du changement"

# Vérifier que les modèles et la base sont synchronisés
alembic check
```

L'URL de la base vient de `.env` : `alembic.ini` n'en contient pas. Les tables système de PostGIS (`spatial_ref_sys`, schémas `tiger` et `topology`) sont ignorées par l'autogénération.

### Données de démonstration

```bash
python scripts/seed.py
```

Le script insère un chantier près d'Abomey-Calavi (Bénin) avec 2 utilisateurs, 4 étapes, 2 rapports et 3 photos : une à 30 m du chantier, une à 2 km et une sans position. Sa position a été relevée par un proche (58 mesures retenues, 2 valeurs aberrantes écartées) puis validée ; celle du second chantier, à Cotonou, est seulement estimée sur la carte. Il peut être relancé sans créer de doublons, et refuse de s'exécuter si `APP_ENV=production`.

Pour voir les données sur une carte, ajouter la table `chantiers` (ou `photos`, `releves_position`) depuis une connexion PostGIS dans QGIS.

## Lancement

L'environnement virtuel étant activé, depuis la racine du projet :

```bash
uvicorn app.main:app --reload
```

| Adresse | Contenu |
|---|---|
| http://localhost:8000/ | Page d'accueil |
| http://localhost:8000/connexion | Connexion (comptes de démonstration : voir ci-dessous) |
| http://localhost:8000/chantiers | Mes chantiers |
| http://localhost:8000/health | État du service et version de PostGIS |
| http://localhost:8000/docs | Documentation OpenAPI interactive |

`/health` renvoie `{"status": "ok", "database": "ok", "postgis_version": "..."}`, ou un code **503** si la base est inaccessible.

Sous Windows, il arrive que le rechargement automatique (`--reload`) se bloque après une modification. Arrêtez alors uvicorn (Ctrl+C) et relancez-le.

## Charte graphique

L'interface vise la sobriété d'un service payant : fond `slate-50`, cartes blanches, bleu nuit `#1E3A5F` pour la confiance (boutons, liens, barre d'avancement) et touche de terre latérite `#C2410C`, avec parcimonie. Police **Inter**, hébergée dans `app/static/fonts/` (ni Google Fonts ni appel externe), chiffres tabulaires pour les pourcentages, distances et dates. Icônes **Lucide** en SVG intégré (`app/icones.py`).

Les composants sont des macros Jinja dans `app/templates/composants/` : `carte`, `bouton`, `badge` (toujours une icône avec le texte), `barre_progression`, `indicateur`, `etat_vide`, `squelette`, `alerte`, `icone`.

### Feuille de style (Tailwind CSS, sans Node.js)

Les couleurs et polices sont définies une seule fois, dans le thème de `app/static/css/source.css`. La palette par défaut de Tailwind est désactivée : seules les couleurs de la charte existent, et aucun template ne contient de couleur codée en dur (un test le vérifie).

La feuille générée, `app/static/css/app.css` (minifiée, versionnée), se compile avec l'**exécutable autonome** de Tailwind, sans Node.js :

```bash
tailwindcss -i app/static/css/source.css -o app/static/css/app.css --minify
```

L'exécutable (v4.3.3) se télécharge depuis les [releases de Tailwind](https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.3) (`tailwindcss-windows-x64.exe`, `tailwindcss-linux-x64`, `tailwindcss-macos-arm64`…), de préférence dans `outils/`, que Git ignore. Après toute modification d'un template ou d'un script, il faut recompiler : un test (et la CI) recompile et compare avec le fichier versionné. Le script Tailwind par CDN est exclu.

La feuille (37 Ko, 7 Ko compressée) est intégrée dans le `<head>` de chaque page. En 3G lente, cela évite un aller-retour de 2 s avant l'affichage.

### Captures

Téléphone (390 px) et ordinateur (1440 px), dans `docs/captures/` : tableau de bord, visionneuse, connexion, envoi de photos, et parcours du relevé de position (page du proche, validation par le propriétaire, historique).

## Tableau de bord du chantier

La page `/chantiers/{id}` répond en quelques secondes à trois questions : le chantier avance-t-il, quand a-t-on eu des nouvelles, les photos viennent-elles bien du terrain.

- **Synthèse** : avancement global, dernier rapport (« il y a 3 jours »), part des photos vérifiées sur 30 jours, étapes en retard. Un bandeau signale l'absence de rapport publié depuis `JOURS_INACTIVITE` jours (7 par défaut).
- **Carte** : satellite par défaut, marqueur du chantier, cercle du rayon de vérification, photos colorées selon leur statut et regroupées aux petites échelles. Popups avec miniature, date, badge et lien vers le rapport.
- **Timeline** des rapports publiés, chargée par blocs de 10 au défilement (HTMX) avec squelettes de chargement.
- **Filtres combinables**, reflétés dans l'URL, appliqués à la fois à la timeline et à la carte : étape, période (7 jours, 30 jours, tout, dates choisies), « Hors zone ou alertes ».
- **Visionneuse** plein écran : balayage au doigt, flèches et Échap au clavier, panneau d'informations, bouton « Voir sur la carte ».

Sur téléphone, une barre fixe en bas donne accès aux trois onglets du chantier : Vue d'ensemble, Photos, Étapes. Sur ordinateur, la carte reste fixe à gauche pendant que la timeline défile.

**Pourcentage de photos vérifiées** : les photos « non vérifiables » (sans position ou trop imprécises) sont exclues du dénominateur, puisqu'on ne peut rien conclure sur elles.

| Route | Contenu |
|---|---|
| `GET /chantiers/{id}` | Tableau de bord |
| `GET /chantiers/{id}/timeline?page=N` | Bloc de rapports (HTMX) |
| `GET /chantiers/{id}/photos` | Toutes les photos des rapports publiés, avec les mêmes filtres |
| `GET /api/chantiers/{id}/photos.geojson` | Photos géolocalisées des rapports publiés (membres uniquement), avec les mêmes filtres |

**Performance** (3G lente simulée : 2 s de latence, 400 kbit/s) : la synthèse s'affiche en 2,3 s. Hors fond de carte, la page pèse 123 Ko. Les réponses sont compressées (gzip), MapLibre se charge sans bloquer l'affichage, les miniatures sont différées (`loading="lazy"`) et de taille fixe. Le nombre de requêtes SQL ne dépend pas du nombre de rapports : un test le vérifie.

**Accessibilité** : score Lighthouse de 100 sur les pages principales. Contrastes WCAG AA, focus clavier visible, zones tactiles de 44 px, textes alternatifs (étape et date) sur les photos.

## Intégration continue

Chaque push sur `main` et chaque pull request (y compris celles de l'agent Copilot) sont vérifiés par GitHub Actions (`.github/workflows/ci.yml`), avec un service PostgreSQL + PostGIS (`postgis/postgis:16-3.4`). Un nouveau push sur la même branche annule l'exécution en cours. Durée : 2 à 3 minutes.

### Ce que vérifie la CI, dans l'ordre

1. Installation de `requirements.txt` et `requirements-dev.txt` (Python 3.12, cache pip).
2. **Ruff** : `ruff check .` (règles `E`, `F`, `I`, `B`, `UP`), puis `ruff format --check .` (lignes de 100 caractères, configuration dans `pyproject.toml`).
3. Création des bases `suivi_chantier` et `suivi_chantier_test` avec PostGIS.
4. `alembic upgrade head`.
5. **`alembic check`** : échoue si un modèle a changé (colonne, index, table…) sans migration.
6. Aller-retour des migrations : `alembic downgrade base`, puis `alembic upgrade head`.
7. `pytest` avec couverture (`pytest-cov`) : échec sous **75 %**. Le détail s'affiche dans le résumé du job.
8. Recompilation de la feuille de style avec la même version de Tailwind (empreinte vérifiée) : échec si `app/static/css/app.css` versionné n'est pas à jour.

Les variables d'environnement de la CI sont des valeurs de test (`APP_ENV=test`, `SECRET_KEY` factice, `EMAIL_BACKEND=console`, photos dans un dossier temporaire) : aucun secret réel. Les tests n'appellent aucun service extérieur : Nominatim et l'envoi d'e-mails sont simulés, et toute connexion réseau non locale fait échouer le test (`tests/conftest.py`).

### Lancer les mêmes vérifications en local

```bash
ruff check .           # règles (ruff check . --fix corrige ce qui peut l'être)
ruff format .          # formate le code
pytest --cov           # tests et couverture
alembic check          # modèles et migrations synchronisés
```

Chaque migration générée par `alembic revision --autogenerate` est corrigée et formatée par Ruff automatiquement (`post_write_hooks` dans `alembic.ini`).

Facultatif : pour que Ruff passe avant chaque commit, installer [pre-commit](https://pre-commit.com/) (configuration dans `.pre-commit-config.yaml`) :

```bash
pip install pre-commit
pre-commit install
```

### Maintenance

- **Dependabot** (`.github/dependabot.yml`) propose chaque lundi les mises à jour des paquets Python et des actions GitHub ; les versions mineures et correctives sont regroupées en une seule pull request, vérifiée par la CI.
- **Protéger `main`** (à faire une fois, sur GitHub) : *Settings* → *Branches* → *Add branch ruleset* (ou *Add rule*) pour `main`, cocher *Require status checks to pass* et choisir le contrôle « Lint, migrations et tests ». Une pull request ne peut alors plus être fusionnée si la CI échoue.

### Ajouter une vérification

Ajouter une étape (`- name:` puis `run:`) dans `.github/workflows/ci.yml`, à l'endroit où elle doit s'exécuter, avec la commande qu'on lancerait en local. Par exemple, pour une future image Docker (issue #12) : `docker build .` après les tests. Si l'outil est un paquet Python, l'ajouter à `requirements-dev.txt` avec une version épinglée ; Dependabot le tiendra à jour.

## Création d'un chantier et cartes

Au Bénin, les adresses sont souvent imprécises et beaucoup de voies n'ont pas de nom : la vue satellite et la saisie de coordonnées sont au cœur de la création d'un chantier.

### Trois façons de placer le chantier (`/chantiers/nouveau`)

1. **Toucher la carte** (satellite par défaut) : un marqueur se pose, qu'on peut ensuite déplacer au doigt ou à la souris.
2. **Coller des coordonnées** : la carte zoome sur le point. Formats reconnus :
   - `6.4512, 2.3501` ou `6.4512 2.3501` (latitude puis longitude, point décimal) ;
   - lien Google Maps contenant `@6.4512,2.3501`, `?q=6.4512,2.3501` ou `!3d6.4512!4d2.3501` ;
   - position partagée sur WhatsApp (lien Google Maps, éventuellement précédé d'un texte).
3. **Rechercher un lieu** (ville, quartier) : la carte zoome sur la zone, puis on touche l'emplacement exact.

Le bouton « Centrer sur ma position » utilise le GPS du téléphone, pour la personne qui se trouve sur le terrain. Le curseur règle le rayon de vérification des photos (10 à 1000 m), et le cercle se met à jour en direct.

### Validation côté serveur

Le parseur de coordonnées est une fonction Python pure (`app/geo/coordonnees.py`) : le JavaScript n'en fait qu'un aperçu. Le serveur :

- accepte un chantier sans position (« à définir » : un proche pourra la relever, voir [Position du chantier](#position-du-chantier)) ; sinon, exige une latitude entre -90 et 90 et une longitude entre -180 et 180 ;
- arrondit les coordonnées à 6 décimales (environ 10 cm) ;
- enregistre `POINT(longitude latitude)` en SRID 4326 ;
- affiche un avertissement, sans bloquer l'enregistrement, si le point est à plus de 50 km de la ville saisie : signe probable d'une inversion latitude/longitude.

Le créateur devient propriétaire du chantier, dans la même transaction que la création. Seul le propriétaire peut ensuite le modifier (`/chantiers/{id}/modifier`).

### Fonds de carte et services externes

| Élément | Réglage | Par défaut |
|---|---|---|
| Plan | `FOND_PLAN_STYLE_URL` | OpenFreeMap « liberty » (sans clé) |
| Satellite | `FOND_SATELLITE_TILES_URL`, `FOND_SATELLITE_ATTRIBUTION` | Esri World Imagery en développement ; **obligatoire en production** |
| Recherche de lieux | `NOMINATIM_URL`, `NOMINATIM_USER_AGENT` | Nominatim d'OpenStreetMap |

MapLibre GL JS (5.24.0, avec empreinte d'intégrité SRI) et `@turf/circle` (7.4.0) sont chargés depuis un CDN, en versions épinglées. Les attributions des fonds restent toujours visibles.

Nominatim est appelé uniquement par le serveur (`/api/recherche-lieu`), conformément à sa politique d'usage : User-Agent identifiant l'application, une requête par seconde au maximum et résultats gardés en cache. Côté page, la recherche part après 500 ms sans frappe, à partir de 3 caractères.

### API

| Route | Contenu |
|---|---|
| `GET /api/chantiers.geojson` | FeatureCollection (RFC 7946, `[longitude, latitude]`) des chantiers dont l'utilisateur est membre, avec `id`, `nom`, `statut` et `role` |
| `GET /api/recherche-lieu?q=…&pays=BJ` | Lieux trouvés par Nominatim : `nom`, `lat`, `lon`, `bbox` `[ouest, sud, est, nord]` |

Les deux routes exigent d'être connecté (sinon **401**).

## Photos du chantier

Sur la page d'un chantier, le bouton « Envoyer des photos » ouvre `/chantiers/{id}/photos/envoyer`, accessible au chef de chantier comme au propriétaire.

### Dans le navigateur (`app/static/js/envoi-photos.js`)

1. La géolocalisation démarre dès l'ouverture de la page (`watchPosition`), pour laisser le GPS se stabiliser. Un indicateur affiche la précision en direct : vert jusqu'à 30 m, orange jusqu'à 100 m, rouge au-delà.
2. « Prendre une photo » ouvre l'appareil photo (`capture="environment"`). Pour chaque photo, le script :
   - prend la dernière position connue si elle date de moins de 2 minutes, sinon en demande une nouvelle ;
   - lit l'EXIF de l'**original** avec exifr (date de prise de vue, GPS éventuel) ;
   - calcule l'empreinte SHA-256 de l'original ;
   - compresse ensuite l'image en JPEG de 1920 px au plus (qualité 0,8, visée sous 500 Ko), en respectant l'orientation portrait.
3. Les photos partent une par une, avec une barre de progression. Après 3 tentatives espacées, un bouton « Réessayer » apparaît. La page prévient avant de fermer si des photos sont encore en attente.

La position envoyée est celle du navigateur ; à défaut, le GPS de l'EXIF ; sinon aucune, et la photo part quand même. iOS et Android retirent souvent le GPS de l'EXIF : la position du navigateur est donc la source principale.

**La géolocalisation exige HTTPS** (ou `localhost`). Pour tester sur un vrai téléphone, il faut une adresse en `https://`, par exemple un tunnel temporaire :

```bash
cloudflared tunnel --url http://localhost:8000
```

Le tunnel rend l'application accessible depuis Internet pendant le test : arrêtez-le ensuite (Ctrl+C).

### Côté serveur

| Route | Rôle |
|---|---|
| `POST /chantiers/{id}/rapports` | Crée le rapport auquel la page rattache ses photos (texte et étape : issue #7) |
| `POST /rapports/{id}/photos` | Reçoit une photo (multipart) et ses métadonnées |
| `GET /photos/{id}`, `GET /photos/{id}/miniature` | Sert la photo ou sa miniature (400 px), aux seuls membres du chantier |

À la réception, le serveur :

- refuse les fichiers de plus de 15 Mo, dès l'en-tête `Content-Length` ;
- ouvre le fichier avec Pillow (`Image.open` + `verify`) et refuse ce qui n'est pas une vraie image JPEG, PNG ou WebP. Le type MIME et l'extension du client sont ignorés, et un fichier HEIC reçoit un message explicatif ;
- remet la photo à l'endroit (`ImageOps.exif_transpose`), puis la **ré-encode** en JPEG (qualité 82, 1920 px au plus). Toutes les métadonnées disparaissent ainsi du fichier stocké : la position du chef de chantier ne peut pas fuiter par l'EXIF ;
- valide les coordonnées avec `app/geo/` (coordonnées invalides : photo gardée sans position), limite la précision à 0–10 000 m et refuse les dates situées dans le futur (tolérance de 5 minutes) ;
- stocke les fichiers dans `STOCKAGE_PHOTOS_DIR/{chantier_id}/{AAAA}/{MM}/{uuid}.jpg` (et `{uuid}_mini.jpg`), sans jamais réutiliser le nom de fichier du client.

Ce dossier n'est pas exposé en statique : chaque lecture vérifie que l'utilisateur est membre du chantier (sinon **404**), avec l'en-tête `Cache-Control: private, max-age=86400`.

## Étapes et avancement

Chaque chantier est découpé en étapes, chacune avec un **poids** : sa part dans l'avancement global.

### Modèle « Maison individuelle »

À la création d'un chantier, une case cochée par défaut crée 8 étapes, d'un poids total de 100. Le modèle est défini dans `app/modeles_etapes.py` : on peut y ajouter d'autres modèles sans toucher aux routes.

| Ordre | Étape | Poids |
|---|---|---|
| 1 | Terrassement | 5 |
| 2 | Fondations | 15 |
| 3 | Élévation | 20 |
| 4 | Dalle | 15 |
| 5 | Toiture | 10 |
| 6 | Menuiseries | 10 |
| 7 | Électricité et plomberie | 10 |
| 8 | Finitions | 15 |

### Calculs (`app/services/avancement.py`)

- **Avancement global** : moyenne des avancements pondérée par les poids, arrondie à l'entier le plus proche. Les poids n'ont pas besoin de totaliser 100.
- **Statut d'une étape** :
  - à venir : 0 % ;
  - en cours : de 1 à 99 % ;
  - terminée : 100 % ;
  - **en retard** : date de fin prévue dépassée et moins de 100 %, avec le nombre de jours de retard. La date du jour est celle du Bénin.
- **Dates réelles** (`date_debut`, `date_fin`) : remplies automatiquement à la première progression et à 100 %. `date_fin` est effacée si l'étape redescend sous 100 %.

Chaque changement d'avancement, qu'il vienne d'un rapport ou d'une mise à jour directe, ajoute une ligne à `historique_avancement`.

### Pages

| Route | Rôle |
|---|---|
| `GET /chantiers/{id}` | Barre d'avancement global et liste des étapes, avec pastille de statut et retard éventuel |
| `GET /chantiers/{id}/etapes` | Gestion des étapes (propriétaire) : ajout, modification du libellé, du poids et des dates, ordre (monter / descendre), suppression, mise à jour de l'avancement avec commentaire |
| `GET /chantiers/{id}/etapes/{etape_id}` | Historique de l'avancement d'une étape (tous les membres), avec lien vers les rapports |

Une étape citée par un rapport ne peut pas être supprimée. Le réordonnancement respecte l'unicité `(chantier_id, ordre)` à chaque instruction SQL : il passe par un ordre temporaire négatif. Le chef de chantier reçoit une **403** sur les actions de gestion, un non-membre une **404**.

## Rapports

Les photos d'une visite sont rattachées à un **rapport**. Le chef de chantier le complète sur la page d'envoi, sous les miniatures : étape concernée (facultatif), commentaire (5 000 caractères au plus, dictée possible avec le micro du clavier) et avancement de l'étape (curseur prérempli avec la valeur actuelle). Il clique ensuite sur « Publier le rapport ».

**Un rapport publié ne peut plus être modifié ni supprimé** : une correction se fait par un nouveau rapport.

- Tant qu'il est en **brouillon**, le rapport n'est visible que de son auteur ; les autres membres reçoivent une 404, y compris sur ses photos. L'auteur peut retirer une photo, ce qui supprime la ligne et les fichiers.
- En rouvrant la page d'envoi dans les 24 h suivant sa dernière activité, l'auteur reprend son brouillon au lieu d'en créer un nouveau.
- La publication est refusée si le rapport n'a ni photo ni texte. Elle se fait en une seule transaction : statut `publie`, date `publie_le`, et, si l'avancement change, mise à jour de l'étape avec une ligne dans `historique_avancement` (ancien et nouveau pourcentage).

| Route | Rôle |
|---|---|
| `POST /chantiers/{id}/rapports` | Crée le brouillon (201), ou renvoie le brouillon récent de l'auteur (200) |
| `POST /rapports/{id}/publier` | Publication (auteur uniquement) |
| `DELETE /rapports/{id}/photos/{photo_id}` | Retrait d'une photo (auteur, brouillon uniquement) |
| `GET /rapports/{id}` | Détail : auteur, date, étape, texte, photos avec badges et visionneuse |

Les rapports publiés apparaissent sur la page du chantier.

Les brouillons sans activité depuis plus de 7 jours se suppriment avec leurs fichiers photo :

```bash
python -m app.taches nettoyer_brouillons
```

### Dates

Les dates s'affichent en français, à l'heure du chantier (`Africa/Porto-Novo`), par exemple « samedi 17 octobre 2026 à 14 h 05 ». Si le lecteur est dans un autre fuseau, détecté par son navigateur, son heure est ajoutée : « 14 h 05 au Bénin — 15 h 05 chez vous ». Sous Windows, le paquet `tzdata` fournit la base des fuseaux horaires.

## Position du chantier

Toute la vérification des photos repose sur ce point de référence. Règle de confiance : **il ne doit pas dépendre de la personne dont on vérifie le travail**, le chef de chantier. Sinon, un point placé sur un autre chantier rendrait « vérifiées » des photos prises ailleurs.

### Statut, source et fiabilité (`app/geo/fiabilite.py`)

| Statut + source | Fiabilité | Libellé affiché |
|---|---|---|
| `non_definie` | — | Position du chantier à définir |
| `estimee` + `carte_proprietaire` | Faible | Position estimée sur la carte |
| `a_valider` | — | Relevé reçu — à valider |
| `confirmee` + `releve_chef` | Moyen | Relevé par le chef de chantier |
| `confirmee` + `releve_proche` | Bon | Relevé sur place par un proche |
| `confirmee` + `bornes_document` | Élevé | Bornes du plan foncier (issue future) |
| `confirmee` + `geometre` | Très élevé | Levé par un géomètre (issue future) |

Des contraintes `CHECK` gardent la table cohérente : pas de point sans source, pas de position « confirmée » sans point ni date de confirmation. Un index unique partiel n'autorise qu'un relevé `en_attente` par chantier.

### Relevé par un proche, sans compte

1. Sur la fiche du chantier (encart « Position du chantier »), le propriétaire demande un relevé : nom du proche, lien de parenté. Le lien (`secrets.token_urlsafe(32)`, haché en base, valable 72 h, à usage unique) s'envoie par WhatsApp avec un message prérempli. Demander au chef de chantier reste possible, moins mis en avant.
2. Le proche ouvre `/releve/{jeton}` au milieu du terrain : trois consignes illustrées, puis une mesure de 60 secondes (`watchPosition`, haute précision) avec un cercle de progression et un indicateur « Signal : bon / moyen / faible ». « Terminer maintenant » apparaît après 30 s s'il y a déjà 15 bonnes mesures. La page n'emploie ni « GPS », ni « précision », ni « coordonnées ».
3. Signal trop faible : « Le signal est faible ici », puis « Recommencer » ; après 2 échecs, « Envoyer quand même » (relevé marqué « signal faible »). Position refusée : explications pour Android et iPhone.
4. Une ou deux photos du terrain (bornes, repère), facultatives, compressées comme celles du chef de chantier (`app/static/js/compression.js`), puis « Merci ! ».

Un chef de chantier connecté qui utilise un lien destiné à un proche est repéré : le relevé porte une alerte visible du propriétaire, et ne compte que comme un relevé du chef.

### Calcul côté serveur (`app/geo/releve.py`)

Le navigateur envoie toutes ses mesures brutes ; il ne calcule rien.

1. Contrôles : coordonnées dans les bornes, aucune mesure postérieure à l'envoi ni antérieure au lien, pas plus de 5 mesures par seconde. Les horodatages sont recalés sur l'horloge du serveur (un téléphone peut être à l'heure d'un autre jour).
2. Mesures annoncées à plus de 50 m : ignorées.
3. Valeurs aberrantes : plus de 3 fois l'écart médian loin de la médiane.
4. Position = moyenne pondérée par 1 / précision².
5. Précision estimée = écart-type des positions autour du point : racine de la moyenne des carrés des distances, calculées par PostGIS (`geography`).
6. Moins de 10 bonnes mesures, ou précision finale au-delà de 25 m : signal faible.

Les mesures retenues sont conservées dans `points_bruts`. Vérification dans QGIS ou psql :

```sql
SELECT nb_mesures, ST_NumGeometries(points_bruts::geometry) AS points,
       ST_Distance(position_resultat, ST_Centroid(points_bruts)) AS ecart_au_centroide_m
FROM releves_position;
```

### Validation, verrouillage et historique

- Le propriétaire est averti par e-mail, sur le tableau de bord et dans la liste des chantiers. La page « Valider le relevé » montre sur fond satellite le point relevé, son cercle de précision, les mesures en petits points et l'ancienne position, avec un avertissement au-delà de 500 m (« Le relevé est à 2,3 km de la position que vous aviez indiquée »), les photos, la durée et la qualité du signal.
- La validation se fait en une transaction : nouvelle position, statut `confirmee`, ligne d'historique, revérification de toutes les photos. Le rejet (motif facultatif) ne change pas la position.
- Seul le propriétaire modifie la position : un chef de chantier n'a aucune route pour le faire (403). Déplacer une position confirmée demande une confirmation explicite ; elle redevient alors « estimée ».
- L'historique (date, ancien → nouveau statut, source, auteur, distance du déplacement) est sur la page `/chantiers/{id}/position`.

## Notifications par e-mail

Le propriétaire ne consulte pas l'application tous les jours : c'est elle qui le prévient.

### Qui reçoit quoi (`app/emails/notifications.py`)

| Événement | Destinataires | « À chaque rapport » | « Un résumé par jour » | « Aucun e-mail » |
|---|---|---|---|---|
| Rapport publié | propriétaires, sauf l'auteur | tout de suite | dans le résumé | rien |
| Photo hors zone ou avec alerte | propriétaires | en tête de l'e-mail du rapport (ou du résumé) | | rien |
| Aucune nouvelle depuis `JOURS_INACTIVITE` jours | propriétaires | une fois par période d'inactivité | idem | rien |
| Invitation acceptée | auteur de l'invitation | tout de suite | tout de suite | rien |
| Relevé de position reçu | propriétaires | tout de suite | **tout de suite** (jamais dans un résumé) | rien |
| Relevé expiré sans réponse | auteur de la demande | tout de suite | tout de suite | rien |
| **Position du chantier modifiée** | **tous** les propriétaires, auteur compris | **toujours** | **toujours** | **toujours** |
| Réinitialisation du mot de passe | l'utilisateur | toujours | toujours | toujours |

La notification de position modifiée n'est jamais désactivable : c'est une protection contre un déplacement discret du point de référence. Comme celui du mot de passe, cet e-mail de sécurité n'a ni lien de désinscription ni réglage. L'alerte d'inactivité ne concerne que les chantiers « en cours ». La création d'un chantier, par son unique propriétaire, n'envoie pas d'e-mail de position.

Le mode se règle par chantier sur `/compte/notifications` (menu du compte, « Notifications ») ; sans réglage, c'est « À chaque rapport ».

### Envoi

- **Expéditeurs** (`app/emails/expediteurs.py`) : `console` en développement (l'e-mail s'affiche dans le terminal d'uvicorn), `smtp` en production. Les tests utilisent un expéditeur en mémoire.
- **Jamais bloquant** : la page répond d'abord ; l'e-mail part ensuite en tâche de fond (`BackgroundTasks`). Un serveur SMTP en panne ne fait jamais échouer une publication : l'e-mail est noté `echec` dans `emails_envoyes`, avec l'erreur.
- **Journal** `emails_envoyes` : destinataire, type, statut (`en_attente`, `envoye`, `echec`), tentatives, dernière erreur, dates. Le contenu est gardé pour un nouvel envoi, sauf celui de réinitialisation du mot de passe (le jeton n'est stocké nulle part en clair).
- **Contenu** : HTML aux couleurs de la charte (lues dans `source.css`, styles intégrés et mise en page en tableaux pour Gmail et Outlook) et version texte brut. **Aucune photo**, ni jointe ni intégrée : les photos sont privées et la bande passante limitée. L'e-mail donne le texte du rapport, l'étape, l'avancement, le nombre de photos et leur vérification, et un bouton « Voir le rapport ».
- **Désinscription** : chaque e-mail (hors sécurité) contient « Gérer mes notifications » et un lien signé « Ne plus recevoir d'e-mails pour ce chantier ». Le lien ouvre une page à un seul bouton : les antivirus de messagerie ouvrent les liens pour les analyser, un simple affichage ne doit donc rien changer. Les messageries compatibles (Gmail, Apple Mail) proposent aussi la désinscription en un clic grâce aux en-têtes `List-Unsubscribe` et `List-Unsubscribe-Post` (RFC 8058).

### Commandes planifiées

```bash
python -m app.taches envoyer_resumes_quotidiens   # une fois par jour
python -m app.taches verifier_inactivite          # une fois par jour
python -m app.taches expirer_releves              # toutes les heures
python -m app.taches renvoyer_emails_echoues      # toutes les heures (3 tentatives au total)
```

Elles seront lancées par une tâche planifiée sur le serveur (issue #12). Relancer une commande n'envoie pas de doublon : le résumé repart de l'instant du précédent, et l'alerte d'inactivité n'est envoyée qu'une fois jusqu'au prochain rapport publié.

### Mot de passe oublié

- `/mot-de-passe-oublie` répond la même chose que l'adresse ait un compte ou non, et l'e-mail part en tâche de fond : ni le message ni le temps de réponse ne révèlent qui est inscrit.
- Le jeton (`secrets.token_urlsafe(32)`) est stocké haché, valable 1 heure, à usage unique ; 3 demandes au plus par compte et par heure.
- Après `/reinitialiser/{jeton}`, toutes les sessions de l'utilisateur sont supprimées : il est déconnecté de tous ses appareils.

### Essayer avec un vrai compte SMTP

Dans `.env` : `EMAIL_BACKEND=smtp`, puis `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` et `EMAIL_EXPEDITEUR`, fournis par un service d'envoi (Brevo, Mailjet, Gmail avec un mot de passe d'application…). Pour tester sans rien envoyer à personne, un serveur local comme [Mailpit](https://mailpit.axllent.org/) (`SMTP_HOST=localhost`, `SMTP_PORT=1025`, sans identifiants) affiche les e-mails reçus dans le navigateur.

## Vérification des photos

Chaque photo reçoit une distance au chantier et un statut, affichés en badge : **« Sur le chantier »** (vert), **« Hors zone — à 1,8 km »** (rouge) ou **« Non vérifiable »** (gris). La page du chantier indique aussi la part des photos des 30 derniers jours prises sur le chantier.

Une position GPS peut être falsifiée : ces indications rendent visibles les situations suspectes, sans constituer une preuve.

### Règles (`app/geo/verification.py`)

1. Position du chantier à définir, photo sans position, ou précision supérieure à `PRECISION_INEXPLOITABLE_M` (200 m) : **non vérifiable**.
2. Distance calculée en SQL avec `ST_Distance` sur des `geography` (mètres) : jusqu'à l'emprise du chantier si elle est dessinée (0 à l'intérieur), sinon jusqu'à son point.
3. Tolérance = `min(précision, TOLERANCE_PRECISION_MAX_M)`, soit 50 m au plus (0 si la précision est inconnue).
4. Distance ≤ rayon de vérification + tolérance : **sur le chantier** ; sinon **hors zone**.

La distance est enregistrée arrondie au mètre (`distance_chantier_m`), avec la date du calcul (`verifiee_le`). Tant que la position du chantier n'est pas confirmée (estimée sur la carte, ou relevé à valider), le badge porte la mention « Position du chantier non confirmée ».

### Signaux de confiance (colonne `alertes`)

| Code | Signification |
|---|---|
| `doublon` | Une photo précédente a la même empreinte d'origine |
| `deplacement_impossible` | Par rapport à la photo précédente du même auteur, vitesse implicite supérieure à 150 km/h (écart de plus d'une minute) |
| `photo_ancienne` | Prise plus de 24 h avant son envoi |
| `position_ancienne` | Position relevée plus de 5 minutes avant la photo |
| `precision_faible` | Précision entre 100 et 200 m |
| `position_exif` | Position lue dans le fichier plutôt que relevée par le navigateur (simple information) |

Les libellés s'affichent sous le badge, et une icône ⚠ signale tout signal autre que `position_exif`.

### Déclenchement

- automatiquement, à l'enregistrement de chaque photo (dans la même requête que l'envoi) ;
- pour toutes les photos d'un chantier, quand sa position (formulaire ou validation d'un relevé) ou son rayon change : une seule requête `UPDATE … FROM`, sans boucle Python ;
- à la demande :

```bash
python -m app.taches recalculer_verifications
```

L'option `--chantier ID` limite le recalcul à un chantier.

Les distances correspondent à une mesure dans QGIS en projection métrique : en UTM 31N (EPSG:32631), l'écart reste sous 0,1 % sur quelques kilomètres, à cause du facteur d'échelle de la projection.

## Comptes et invitations

Chaque membre d'un chantier y a un rôle :

- **propriétaire** : suit l'avancement, souvent depuis l'étranger, et invite d'autres personnes ;
- **chef de chantier** : sur place, il enverra les rapports et les photos.

### Inviter un chef de chantier

1. Le propriétaire ouvre son chantier et clique sur « Créer un lien d'invitation ».
2. Il copie le lien ou le partage directement sur WhatsApp. Le lien est valable 7 jours et ne sert qu'une fois.
3. La personne invitée ouvre le lien, crée son compte (ou se connecte), puis clique sur « Rejoindre le chantier ».

L'invitation ne dépend d'aucun envoi d'e-mail.

### Pages

| Route | Rôle |
|---|---|
| `GET/POST /inscription` | Création de compte (mot de passe de 8 caractères minimum) |
| `GET/POST /connexion`, `POST /deconnexion` | Connexion, déconnexion |
| `GET /chantiers` | Carte et liste des chantiers dont l'utilisateur est membre, avec son rôle |
| `GET/POST /chantiers/nouveau` | Création d'un chantier (le créateur en devient propriétaire) |
| `GET /chantiers/{id}` | Page du chantier avec sa carte (membres uniquement) |
| `GET/POST /chantiers/{id}/modifier` | Modification (propriétaire uniquement) |
| `POST /chantiers/{id}/invitations` | Création d'un lien d'invitation (propriétaire uniquement) |
| `GET /invitations/{jeton}`, `POST /invitations/{jeton}/accepter` | Ouverture et acceptation d'une invitation |

Dans le code, les dépendances de `app/auth/dependencies.py` protègent les routes : `get_current_user_optional`, `get_current_user` (redirige vers `/connexion`) et `require_membre(roles)`.

### Sécurité

- **Sessions côté serveur** : le cookie `session` (`HttpOnly`, `SameSite=Lax`, `Secure` en production) contient un jeton aléatoire, et la base n'en garde que l'empreinte SHA-256. La déconnexion supprime la session en base.
- **Mots de passe** hachés en Argon2id (`pwdlib`).
- **CSRF** : chaque formulaire POST porte un jeton signé avec `SECRET_KEY`, vérifié pour toute l'application.
- **Contrôle d'accès** : un non-membre reçoit une **404**, pour ne pas révéler l'existence du chantier. Un membre sans le bon rôle reçoit une **403**.
- **Connexion** : le message d'erreur est le même, que l'e-mail existe ou non. Après 5 échecs en 15 minutes pour un même e-mail, la connexion est bloquée temporairement.
- **Invitations** : jeton de `secrets.token_urlsafe(32)`, stocké haché et masqué dans le journal des accès.
- **Redirections** : le paramètre `suivant` n'accepte que des chemins internes (`/...`, jamais `//...`).

Le compteur d'échecs de connexion est gardé en mémoire : il convient à un seul processus uvicorn. Avec plusieurs processus, il faudra le stocker en base ou dans Redis.

### Comptes de démonstration

Après `python scripts/seed.py` :

| E-mail | Rôle |
|---|---|
| `proprietaire@demo.test` | Propriétaire de « Villa R+1 Calavi » |
| `chef@demo.test` | Chef de chantier de « Villa R+1 Calavi » |
| `autre.proprietaire@demo.test` | Propriétaire d'un autre chantier, « Immeuble R+2 Fidjrossè » |

Le mot de passe est celui de la variable `SEED_PASSWORD`. Sans elle, en développement, le script utilise une valeur par défaut et l'affiche dans la console.

## Tests

```bash
pytest           # ou pytest --cov pour la couverture (75 % au moins, voir pyproject.toml)
```

- `tests/test_health.py` : la route `/health`, avec une base simulée puis avec la vraie base de `.env`.
- `tests/test_models.py` : positions, distances en mètres, contraintes et suppressions en cascade, sur la base `suivi_chantier_test`.
- `tests/test_auth.py` : inscription, connexion, déconnexion, blocage après 5 échecs, CSRF, redirections et journaux.
- `tests/test_permissions.py` : matrice d'accès aux chantiers, création et acceptation des invitations.
- `tests/test_coordonnees.py` : formats de coordonnées reconnus et refusés.
- `tests/test_chantiers.py` : création et modification de chantiers, GeoJSON, recherche de lieu.
- `tests/test_etapes.py` : modèle d'étapes, avancement pondéré, retard, dates réelles, réordonnancement, suppression, droits.
- `tests/test_rapports.py` : publication, avancement et historique, rapport publié non modifiable, brouillons privés et repris, nettoyage, dates.
- `tests/test_verification.py` : statuts selon la distance et la précision (positions générées avec `ST_Project`), emprise, recalcul, signaux de confiance, affichage.
- `tests/test_tableau_de_bord.py` : GeoJSON des photos et filtres, synthèse, alerte d'inactivité, pagination de la timeline, nombre de requêtes constant, feuille de style à jour, absence de couleur codée en dur.
- `tests/test_photos.py` : envoi, ré-encodage sans EXIF, orientation, refus de fichiers, accès aux photos. Les images sont générées par Pillow et le stockage se fait dans un dossier temporaire.
- `tests/test_notifications.py` : e-mails selon les préférences, auteur jamais prévenu de son propre rapport, avertissement de photo hors zone en tête, résumé quotidien sans doublon, inactivité une seule fois, relevé reçu et expiré, position modifiée envoyée même en mode « aucun », panne SMTP sans effet sur la publication, nouvelles tentatives, désinscription signée, mot de passe oublié (jeton haché, expiré, réutilisé, sessions fermées, limite de 3 par heure), expéditeurs console et SMTP.
- `tests/test_isolation.py` : aucun appel réseau vers l'extérieur pendant les tests.
- `tests/test_releve_position.py` : calcul sur des mesures simulées (point retrouvé à moins de 3 m malgré 2 valeurs aberrantes), mesures imprécises ignorées, signal faible, horodatages incohérents, niveaux de fiabilité, lien du proche (usage unique, 72 h, annulation, jeton haché, sans compte), validation et rejet, verrouillage, droits du chef de chantier, alerte du chef connecté, avertissement au-delà de 500 m.

Nominatim est simulé dans tous les tests : aucun appel réseau réel.

Au début de chaque lancement, la base de test est vidée puis recréée par les migrations Alembic (`downgrade base`, puis `upgrade head`). Chaque test est annulé à la fin, et les tests sont ignorés (`skipped`) si la base est inaccessible.

## Arborescence

```
suivi-chantier/
├── app/
│   ├── main.py          # application FastAPI, pages d'erreur, page d'accueil
│   ├── config.py        # lecture du .env
│   ├── database.py      # moteur SQLAlchemy, dépendance get_db
│   ├── templating.py    # moteur Jinja2 partagé
│   ├── journalisation.py # masquage des jetons dans les journaux
│   ├── formulaire_chantier.py # formulaire de chantier et validation
│   ├── geo/             # coordonnées, Nominatim, points PostGIS, vérification des photos, relevé, fiabilité
│   ├── taches.py        # commandes : python -m app.taches …
│   ├── rapports.py      # brouillons, publication, nettoyage
│   ├── modeles_etapes.py # modèles d'étapes (maison individuelle…) et poids par défaut
│   ├── services/        # avancement, tableau de bord, position du chantier (relevés, validation, historique)
│   ├── dates.py         # dates en français, heure du chantier
│   ├── photos/          # traitement Pillow, métadonnées, stockage
│   ├── emails/          # expéditeurs, envoi en tâche de fond, notifications, préférences, gabarits/
│   ├── auth/            # sessions, CSRF, mots de passe et réinitialisation, limitation, dépendances
│   ├── models/          # un fichier par table, Base commune dans base.py
│   ├── routers/         # health, auth, chantiers, compte, etapes, invitations, photos, position, releve, rapports, api
│   ├── templates/       # pages HTML (Jinja2)
│   └── static/          # style.css et js/ (cartes, envoi de photos, visionneuse, dates)
├── migrations/          # Alembic : env.py et versions/
├── scripts/
│   └── seed.py          # données de démonstration
├── stockage/            # photos envoyées (ignoré par git)
├── tests/
│   ├── conftest.py      # base de test, session annulée, navigateurs de test
│   ├── outils.py        # connexion, jeton CSRF, création de comptes
│   ├── test_health.py
│   ├── test_models.py
│   ├── test_auth.py
│   ├── test_permissions.py
│   ├── test_coordonnees.py
│   ├── test_chantiers.py
│   ├── test_etapes.py
│   ├── test_photos.py
│   ├── test_rapports.py
│   ├── test_verification.py
│   ├── test_releve_position.py
│   └── test_notifications.py
├── .github/
│   ├── workflows/ci.yml # intégration continue (GitHub Actions)
│   └── dependabot.yml   # mises à jour hebdomadaires des dépendances
├── alembic.ini
├── pyproject.toml       # configuration de Ruff, pytest et de la couverture
├── .pre-commit-config.yaml
├── .env.example
├── requirements.txt     # dépendances de l'application
├── requirements-dev.txt # + pytest, pytest-cov, Ruff, client de test
└── README.md
```
