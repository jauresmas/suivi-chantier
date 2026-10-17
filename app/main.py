"""Point d'entrée de l'application : uvicorn app.main:app --reload"""

import logging
from http import HTTPStatus
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.csrf import middleware_csrf, verifier_csrf
from app.auth.dependencies import ConnexionRequise, get_current_user_optional
from app.journalisation import MasquerJetons
from app.models import User
from app.photos.traitement import TAILLE_MAX_OCTETS
from app.routers import (
    api,
    auth,
    chantiers,
    compte,
    etapes,
    health,
    invitations,
    photos,
    position,
    rapports,
    releve,
    tableau_de_bord,
)
from app.templating import APP_DIR, templates

# Les URL d'invitation et de relevé contiennent le jeton : on le masque dans le journal des accès.
logging.getLogger("uvicorn.access").addFilter(MasquerJetons())

# Photo de 15 Mo + marge pour les autres champs du formulaire multipart.
TAILLE_MAX_REQUETE = TAILLE_MAX_OCTETS + 1024 * 1024

app = FastAPI(title="Suivi de chantier", version="0.1.0", dependencies=[Depends(verifier_csrf)])
app.middleware("http")(middleware_csrf)
# Compression des pages, styles et scripts : décisive sur une connexion 3G.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def limiter_taille_requete(request: Request, call_next):
    """Refuse un envoi trop lourd dès l'en-tête, avant de recevoir le corps."""
    longueur = request.headers.get("content-length", "")
    if longueur.isdigit() and int(longueur) > TAILLE_MAX_REQUETE:
        return JSONResponse({"detail": "Photo trop lourde : 15 Mo maximum."}, status_code=413)
    return await call_next(request)


# Seuls style et scripts sont publics : les photos passent par /photos/{id}, après contrôle d'accès.
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(
    chantiers.router
)  # avant tableau_de_bord : /chantiers/nouveau doit passer en premier
app.include_router(tableau_de_bord.router)
app.include_router(etapes.router)
app.include_router(invitations.router)
app.include_router(compte.router)
app.include_router(position.router)
app.include_router(releve.router)
app.include_router(photos.router)
app.include_router(rapports.router)
app.include_router(api.router)

MESSAGES_ERREUR = {
    403: "Vous n'avez pas accès à cette page.",
    404: "Cette page n'existe pas.",
}


@app.exception_handler(ConnexionRequise)
async def rediriger_vers_connexion(request: Request, exc: ConnexionRequise):
    url = "/connexion" + (f"?suivant={quote(exc.suivant, safe='')}" if exc.suivant else "")
    return RedirectResponse(url, status_code=303)


@app.exception_handler(StarletteHTTPException)
async def page_erreur(request: Request, exc: StarletteHTTPException):
    if "text/html" not in request.headers.get("accept", ""):
        return await http_exception_handler(request, exc)
    message = exc.detail
    if not isinstance(message, str) or message == HTTPStatus(exc.status_code).phrase:
        message = MESSAGES_ERREUR.get(exc.status_code, "Une erreur est survenue.")
    return templates.TemplateResponse(
        request,
        "erreur.html",
        {"code": exc.status_code, "message": message},
        status_code=exc.status_code,
    )


@app.get("/", include_in_schema=False)
def accueil(request: Request, utilisateur: User | None = Depends(get_current_user_optional)):
    if utilisateur:
        return RedirectResponse("/chantiers", status_code=303)
    return templates.TemplateResponse(request, "accueil.html")
