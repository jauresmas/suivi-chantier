"""Point d'entrée de l'application : uvicorn app.main:app --reload"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routers import health

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Suivi de chantier", version="0.1.0")

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

app.include_router(health.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def accueil(request: Request):
    return templates.TemplateResponse(request, "base.html")
