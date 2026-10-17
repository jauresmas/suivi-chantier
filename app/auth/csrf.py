"""Protection CSRF par double soumission signée.

Un cookie aléatoire est posé à la première visite. Chaque formulaire contient un champ caché
égal à la signature HMAC de ce cookie (avec SECRET_KEY) : un autre site ne peut ni lire le
cookie ni calculer la signature.
"""

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request
from markupsafe import Markup

from app.auth.securite import cookies_securises
from app.config import get_settings

COOKIE_CSRF = "csrf"
CHAMP_CSRF = "csrf_token"
ENTETE_CSRF = "x-csrf-token"  # pour les requêtes HTMX à venir
METHODES_SURES = {"GET", "HEAD", "OPTIONS", "TRACE"}
# Désinscription depuis un e-mail : la messagerie envoie elle-même le POST (RFC 8058), sans jeton
# CSRF possible. Le lien est signé avec SECRET_KEY : c'est sa signature qui autorise l'action.
CHEMINS_SANS_CSRF = ("/notifications/desinscription/",)
DUREE_COOKIE_S = 30 * 24 * 3600


def _signer(valeur_cookie: str) -> str:
    return hmac.new(
        get_settings().secret_key.encode(), valeur_cookie.encode(), hashlib.sha256
    ).hexdigest()


async def middleware_csrf(request: Request, call_next):
    cookie = request.cookies.get(COOKIE_CSRF)
    nouveau = not cookie
    request.state.cookie_csrf = cookie if cookie else secrets.token_urlsafe(32)
    response = await call_next(request)
    if nouveau:
        response.set_cookie(
            COOKIE_CSRF,
            request.state.cookie_csrf,
            max_age=DUREE_COOKIE_S,
            httponly=True,
            samesite="lax",
            secure=cookies_securises(),
        )
    return response


def jeton_csrf(request: Request) -> str:
    """Jeton à renvoyer dans le champ csrf_token ou l'en-tête X-CSRF-Token."""
    return _signer(request.state.cookie_csrf)


def champ_csrf(request: Request) -> Markup:
    """Champ caché à placer dans chaque formulaire POST : {{ champ_csrf(request) }}."""
    return Markup(f'<input type="hidden" name="{CHAMP_CSRF}" value="{jeton_csrf(request)}">')


async def verifier_csrf(request: Request) -> None:
    """Dépendance appliquée à toute l'application : refuse les POST sans jeton valide."""
    if request.method in METHODES_SURES or request.url.path.startswith(CHEMINS_SANS_CSRF):
        return
    envoye = request.headers.get(ENTETE_CSRF)
    if envoye is None and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        envoye = (await request.form()).get(CHAMP_CSRF)
    cookie = request.cookies.get(COOKIE_CSRF)
    if (
        not cookie
        or not isinstance(envoye, str)
        or not hmac.compare_digest(envoye, _signer(cookie))
    ):
        raise HTTPException(403, "Le formulaire a expiré. Rechargez la page et réessayez.")
