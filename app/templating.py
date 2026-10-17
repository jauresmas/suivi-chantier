"""Moteur de templates partagé par toutes les routes HTML."""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.auth.csrf import champ_csrf, jeton_csrf
from app.config import get_settings
from app.dates import balise_date, date_courte, date_longue, date_relative
from app.geo.verification import badge_photo, formater_distance
from app.icones import icone
from app.libelles import (
    APPARENCE_STATUT,
    LIBELLES_ROLE,
    LIBELLES_STATUT,
    LIBELLES_TYPE_ETAPE,
    initiales,
    libelle_etape,
)
from app.services.avancement import etat_etape

APP_DIR = Path(__file__).resolve().parent


def config_carte() -> dict:
    """Fonds de carte transmis au JavaScript (voir _carte_ressources.html)."""
    settings = get_settings()
    return {
        "plan_style_url": settings.fond_plan_style_url,
        "satellite_tiles_url": settings.fond_satellite_tiles_url,
        "satellite_attribution": settings.fond_satellite_attribution or "",
    }


def contexte_commun(request: Request) -> dict:
    return {
        "utilisateur": getattr(request.state, "utilisateur", None),
        "config_carte": config_carte(),
    }


_CSS = {"mtime": None, "contenu": ""}


def css_en_ligne() -> Markup:
    """Contenu de static/css/app.css, intégré dans le <head>.

    En 3G lente, chaque requête coûte environ 2 s de latence : intégrer la feuille (7 Ko compressée)
    évite d'attendre un second aller-retour avant d'afficher la page. Relue si le fichier change.
    """
    chemin = APP_DIR / "static" / "css" / "app.css"
    mtime = chemin.stat().st_mtime
    if mtime != _CSS["mtime"]:
        _CSS.update(mtime=mtime, contenu=chemin.read_text(encoding="utf-8"))
    return Markup(_CSS["contenu"])


templates = Jinja2Templates(directory=APP_DIR / "templates", context_processors=[contexte_commun])
templates.env.filters["distance"] = formater_distance
templates.env.filters["date_longue"] = date_longue
templates.env.filters["date_courte"] = date_courte
templates.env.filters["balise_date"] = balise_date
templates.env.filters["date_relative"] = date_relative
# {{ photos | select("garder_photo", filtres) }} : photos retenues par les filtres du tableau de
# bord
templates.env.tests["garder_photo"] = lambda photo, filtres: filtres.garder_photo(photo)
templates.env.globals.update(
    APPARENCE_STATUT=APPARENCE_STATUT,
    badge_photo=badge_photo,
    initiales=initiales,
    svg_icone=icone,
    etat_etape=etat_etape,
    LIBELLES_TYPE_ETAPE=LIBELLES_TYPE_ETAPE,
    libelle_etape=libelle_etape,
    champ_csrf=champ_csrf,
    css_en_ligne=css_en_ligne,
    jeton_csrf=jeton_csrf,
    LIBELLES_ROLE=LIBELLES_ROLE,
    LIBELLES_STATUT=LIBELLES_STATUT,
)
