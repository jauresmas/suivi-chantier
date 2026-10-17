"""Données JSON pour les cartes : chantiers de l'utilisateur et recherche de lieux."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import membre_du_chantier_api, utilisateur_api
from app.database import get_db
from app.geo.nominatim import NominatimIndisponible, nominatim
from app.libelles import LIBELLES_ROLE, LIBELLES_STATUT
from app.models import Chantier, MembreChantier, User
from app.services.tableau_de_bord import Filtres, photos_geojson

router = APIRouter(prefix="/api", tags=["api"])

RECHERCHE_MIN, RECHERCHE_MAX = 3, 200


@router.get("/chantiers.geojson")
def chantiers_geojson(user: User = Depends(utilisateur_api), db: Session = Depends(get_db)):
    """FeatureCollection (RFC 7946) des chantiers positionnés dont l'utilisateur est membre, et de
    son rôle sur chacun."""
    lignes = db.execute(
        select(
            Chantier.id,
            Chantier.nom,
            Chantier.statut,
            MembreChantier.role,
            func.ST_AsGeoJSON(Chantier.position, 6).label("geometrie"),
        )
        .join(MembreChantier, MembreChantier.chantier_id == Chantier.id)
        .where(MembreChantier.user_id == user.id, Chantier.position.is_not(None))
        .order_by(Chantier.nom)
    ).all()
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(ligne.geometrie),
                "properties": {
                    "id": str(ligne.id),
                    "nom": ligne.nom,
                    "statut": ligne.statut.value,
                    "role": ligne.role.value,
                    "libelle_statut": LIBELLES_STATUT[ligne.statut],
                    "libelle_role": LIBELLES_ROLE[ligne.role],
                },
            }
            for ligne in lignes
        ],
    }
    return JSONResponse(collection, media_type="application/geo+json")


@router.get("/recherche-lieu")
def recherche_lieu(q: str = "", pays: str = "", user: User = Depends(utilisateur_api)):
    """Lieux (ville, quartier) pour zoomer sur la zone ; la position du chantier vient ensuite du
    clic."""
    q = " ".join(q.split())
    if not RECHERCHE_MIN <= len(q) <= RECHERCHE_MAX:
        return {"resultats": []}
    try:
        resultats = nominatim.rechercher(
            q, pays=pays if re.fullmatch(r"[A-Za-z]{2}", pays) else None
        )
    except NominatimIndisponible:
        raise HTTPException(502, "Recherche de lieu indisponible pour le moment.") from None
    return {"resultats": resultats}


@router.get("/chantiers/{id}/photos.geojson")
def photos_du_chantier_geojson(
    request: Request,
    membre: MembreChantier = Depends(membre_du_chantier_api),
    db: Session = Depends(get_db),
):
    """Photos géolocalisées des rapports publiés, avec les mêmes filtres que la timeline."""
    filtres = Filtres.depuis_requete(request.query_params)
    return JSONResponse(
        photos_geojson(db, membre.chantier_id, filtres), media_type="application/geo+json"
    )
