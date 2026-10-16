"""Route de santé : vérifie que l'API répond et que PostGIS est joignable."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["santé"])


class EtatSante(BaseModel):
    status: str
    database: str
    postgis_version: str | None = None


@router.get(
    "/health",
    response_model=EtatSante,
    responses={503: {"model": EtatSante, "description": "Base de données inaccessible"}},
)
def health(db: Session = Depends(get_db)):
    try:
        postgis_version = db.execute(text("SELECT PostGIS_Version()")).scalar_one()
    except SQLAlchemyError as exc:
        logger.warning("Base de données inaccessible : %s", exc)
        etat = EtatSante(status="error", database="unavailable")
        return JSONResponse(status_code=503, content=etat.model_dump())
    return EtatSante(status="ok", database="ok", postgis_version=postgis_version)
