"""Dates en français, à l'heure du chantier (Bénin).

Le navigateur ajoute l'heure du lecteur si elle diffère (static/js/dates.js) : le
propriétaire vit souvent à l'étranger.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

FUSEAU_CHANTIER = ZoneInfo("Africa/Porto-Novo")
NOM_LIEU = "au Bénin"
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def heure(date: datetime) -> str:
    """« 14 h 05 », « 9 h 30 » (heure du chantier)."""
    locale = date.astimezone(FUSEAU_CHANTIER)
    return f"{locale.hour} h {locale.minute:02d}"


def date_longue(date: datetime) -> str:
    """« samedi 17 octobre 2026 à 14 h 05 » (heure du chantier)."""
    locale = date.astimezone(FUSEAU_CHANTIER)
    jour = f"{JOURS[locale.weekday()]} {locale.day} {MOIS[locale.month - 1]} {locale.year}"
    return f"{jour} à {heure(date)}"


def date_courte(date: datetime) -> str:
    """« 17/10/2026 » (date du chantier)."""
    return f"{date.astimezone(FUSEAU_CHANTIER):%d/%m/%Y}"


def balise_date(date: datetime | None) -> Markup:
    """<time> lisible par dates.js, qui y ajoute l'heure du lecteur si elle est différente."""
    if date is None:
        return Markup("")
    return Markup(
        '<time class="date-chantier" datetime="{iso}" data-fuseau="Africa/Porto-Novo"'
        ' data-lieu="{lieu}">{texte}</time>'
    ).format(iso=date.isoformat(), lieu=NOM_LIEU, texte=escape(date_longue(date)))


def date_relative(date: datetime | None, maintenant: datetime | None = None) -> str:
    """« à l'instant », « il y a 3 h », « hier », « il y a 3 jours », « il y a 2 mois »."""
    if date is None:
        return ""
    secondes = ((maintenant or datetime.now(date.tzinfo)) - date).total_seconds()
    minutes, heures, jours = secondes / 60, secondes / 3600, secondes / 86400
    if minutes < 1:
        return "à l'instant"
    if heures < 1:
        return f"il y a {int(minutes)} min"
    if jours < 1:
        return f"il y a {int(heures)} h"
    if jours < 2:
        return "hier"
    if jours < 30:
        return f"il y a {int(jours)} jours"
    if jours < 365:
        return f"il y a {int(jours // 30)} mois"
    annees = int(jours // 365)
    return f"il y a {annees} an{'s' if annees > 1 else ''}"
