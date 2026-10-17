"""Gabarits des e-mails (app/emails/gabarits) : une version HTML et une version texte brut.

Les clients de messagerie (Gmail, Outlook) ignorent les feuilles de style : le HTML porte des
styles intégrés et une mise en page en tableaux. Les couleurs viennent de la charte, lues dans
app/static/css/source.css : aucune n'est recopiée dans les gabarits.
"""

import re
from functools import cache
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.config import get_settings
from app.dates import date_courte, date_longue
from app.geo.verification import formater_distance
from app.libelles import LIBELLES_ROLE

DOSSIER = Path(__file__).resolve().parent / "gabarits"
SOURCE_CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "source.css"


@cache
def charte() -> SimpleNamespace:
    """Couleurs du thème Tailwind (--color-primaire…) : c.primaire, c.texte_doux…"""
    couleurs = re.findall(
        r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})", SOURCE_CSS.read_text(encoding="utf-8")
    )
    return SimpleNamespace(**{nom.replace("-", "_"): valeur for nom, valeur in couleurs})


def url(chemin: str) -> str:
    """Lien absolu vers une page du site."""
    return get_settings().url_publique + chemin


def _environnement(texte: bool) -> Environment:
    environnement = Environment(
        loader=FileSystemLoader(DOSSIER),
        autoescape=False if texte else select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,  # une variable oubliée fait échouer le rendu (et les tests)
    )
    environnement.filters.update(
        date_longue=date_longue, date_courte=date_courte, distance=formater_distance
    )
    environnement.globals.update(c=charte(), url=url, LIBELLES_ROLE=LIBELLES_ROLE)
    return environnement


_HTML = _environnement(texte=False)
_TEXTE = _environnement(texte=True)


def rendre(nom: str, **contexte) -> tuple[str, str]:
    """(html, texte) du gabarit `nom` : nom.html et nom.txt."""
    return _HTML.get_template(f"{nom}.html").render(**contexte), _TEXTE.get_template(
        f"{nom}.txt"
    ).render(**contexte)
