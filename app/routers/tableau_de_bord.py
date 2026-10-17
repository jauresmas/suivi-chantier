"""Tableau de bord d'un chantier (vue d'ensemble), timeline par blocs (HTMX) et onglet Photos."""

import json
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import membre_du_chantier
from app.database import get_db
from app.geo.fiabilite import niveau_fiabilite
from app.geo.positions import lat_lon_chantier
from app.geo.verification import condition_photo_suspecte
from app.models import (
    Chantier,
    MembreChantier,
    Photo,
    Rapport,
    RoleMembre,
    StatutRapport,
    StatutReleve,
    User,
)
from app.services.avancement import etapes_du_chantier
from app.services.position import releve_du_chantier
from app.services.tableau_de_bord import Filtres, rapports_timeline, synthese
from app.templating import templates

router = APIRouter(tags=["tableau de bord"], include_in_schema=False)

PHOTOS_PAR_BLOC = 24


def _page(texte: str) -> int:
    return int(texte) if texte.isdigit() and int(texte) > 0 else 1


def etapes_filtrables(db: Session, chantier_id: uuid.UUID, etapes: list) -> list:
    """Étapes citées par au moins un rapport publié (ou déjà choisie dans les filtres) : les autres
    donneraient une liste vide, et leurs puces encombreraient l'écran."""
    citees = set(
        db.scalars(
            select(Rapport.etape_id).where(
                Rapport.chantier_id == chantier_id, Rapport.statut == StatutRapport.PUBLIE
            )
        )
    )
    return [e for e in etapes if e.id in citees]


def contexte_timeline(db: Session, membre: MembreChantier, filtres: Filtres, page: int) -> dict:
    rapports, suivant = rapports_timeline(db, membre.chantier_id, filtres, page)
    etapes = etapes_du_chantier(db, membre.chantier_id)
    filtrables = etapes_filtrables(db, membre.chantier_id, etapes)
    return {
        "chantier": membre.chantier,
        "filtres": filtres,
        "rapports": rapports,
        "page": page,
        "suivant": suivant,
        "etapes": etapes,
        "etapes_filtres": [e for e in etapes if e in filtrables or e.id == filtres.etape],
        "est_proprietaire": membre.role == RoleMembre.PROPRIETAIRE,
    }


@router.get("/chantiers/{id}")
def tableau_de_bord(
    request: Request,
    alerte_distance_km: str = "",
    photo: str = "",
    position: str = "",
    membre: MembreChantier = Depends(membre_du_chantier),
    db: Session = Depends(get_db),
):
    chantier = membre.chantier
    filtres = Filtres.depuis_requete(request.query_params)
    contexte = contexte_timeline(db, membre, filtres, 1)
    latitude, longitude = lat_lon_chantier(db, chantier.id) or ("", "")
    emprise = db.scalar(
        select(func.ST_AsGeoJSON(Chantier.emprise, 6)).where(Chantier.id == chantier.id)
    )
    membres = db.scalars(
        select(MembreChantier)
        .join(MembreChantier.user)
        .options(joinedload(MembreChantier.user))
        .where(MembreChantier.chantier_id == chantier.id)
        .order_by(MembreChantier.role, User.nom_complet)
    ).all()
    brouillon = db.scalar(
        select(Rapport.id)
        .where(
            Rapport.chantier_id == chantier.id,
            Rapport.auteur_id == membre.user_id,
            Rapport.statut == StatutRapport.BROUILLON,
        )
        .limit(1)
    )
    try:
        photo_ciblee = str(uuid.UUID(photo)) if photo else None
    except ValueError:
        photo_ciblee = None
    # Relevés : seul le propriétaire en est averti (notification dans l'application).
    releves = {}
    if contexte["est_proprietaire"]:
        releves = {
            "releve_a_valider": releve_du_chantier(db, chantier.id, StatutReleve.RECU),
            "releve_en_attente": releve_du_chantier(db, chantier.id, StatutReleve.EN_ATTENTE),
        }
    return templates.TemplateResponse(
        request,
        "chantiers/tableau_de_bord.html",
        {
            **contexte,
            "synthese": synthese(db, chantier, contexte["etapes"]),
            "membres": membres,
            "brouillon": brouillon,
            "latitude": latitude,
            "longitude": longitude,
            "emprise": json.loads(emprise) if emprise else None,
            "photo_ciblee": photo_ciblee,
            "alerte_distance_km": int(alerte_distance_km) if alerte_distance_km.isdigit() else None,
            "fiabilite": niveau_fiabilite(chantier),
            "position_validee": position == "validee",
            **releves,
            "onglet": "vue",
        },
    )


@router.get("/chantiers/{id}/timeline")
def bloc_timeline(
    request: Request,
    page: str = "1",
    membre: MembreChantier = Depends(membre_du_chantier),
    db: Session = Depends(get_db),
):
    """Page 1 : zone complète (filtres + rapports), pour un changement de filtre.
    Pages suivantes : rapports seuls, ajoutés au défilement (hx-trigger="revealed")."""
    numero = _page(page)
    contexte = contexte_timeline(db, membre, Filtres.depuis_requete(request.query_params), numero)
    gabarit = (
        "chantiers/_zone_timeline.html" if numero == 1 else "chantiers/_rapports_timeline.html"
    )
    return templates.TemplateResponse(request, gabarit, contexte)


def photos_du_bloc(
    db: Session, chantier_id: uuid.UUID, filtres: Filtres, page: int
) -> tuple[list[Photo], bool]:
    requete = (
        select(Photo)
        .join(Rapport, Photo.rapport_id == Rapport.id)
        .where(Rapport.chantier_id == chantier_id, *filtres.conditions_rapport())
        .options(joinedload(Photo.rapport).joinedload(Rapport.etape))
        .order_by(Photo.envoyee_le.desc(), Photo.id)
        .offset((page - 1) * PHOTOS_PAR_BLOC)
        .limit(PHOTOS_PAR_BLOC + 1)
    )
    if filtres.alertes:
        requete = requete.where(condition_photo_suspecte())
    photos = db.scalars(requete).all()
    return list(photos[:PHOTOS_PAR_BLOC]), len(photos) > PHOTOS_PAR_BLOC


@router.get("/chantiers/{id}/photos")
def onglet_photos(
    request: Request,
    page: str = "1",
    membre: MembreChantier = Depends(membre_du_chantier),
    db: Session = Depends(get_db),
):
    """Toutes les photos des rapports publiés, avec les mêmes filtres que le tableau de bord."""
    numero = _page(page)
    filtres = Filtres.depuis_requete(request.query_params)
    photos, suivant = photos_du_bloc(db, membre.chantier_id, filtres, numero)
    contexte = {
        "chantier": membre.chantier,
        "filtres": filtres,
        "photos": photos,
        "page": numero,
        "suivant": suivant,
        "etapes": etapes_du_chantier(db, membre.chantier_id),
        "onglet": "photos",
    }
    contexte["etapes_filtres"] = etapes_filtrables(db, membre.chantier_id, contexte["etapes"])
    gabarit = (
        "chantiers/_photos_bloc.html"
        if request.headers.get("HX-Request") and numero > 1
        else "chantiers/photos.html"
    )
    return templates.TemplateResponse(request, gabarit, contexte)
