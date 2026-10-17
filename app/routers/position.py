"""Position du chantier, côté propriétaire : relevés, validation, rejet et historique.

Toutes ces routes sont réservées au propriétaire : un chef de chantier reçoit une 403 (il sait
que le chantier existe), un non-membre une 404. Le chef n'a aucun moyen de changer la position.
"""

import json
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import proprietaire_du_chantier
from app.database import get_db
from app.emails.notifications import notifier_position_modifiee
from app.geo.fiabilite import (
    LIBELLES_SOURCE_POSITION,
    LIBELLES_STATUT_POSITION,
    fiabilite,
    niveau_fiabilite,
)
from app.geo.positions import lat_lon_chantier
from app.geo.releve import qualite_signal
from app.models import (
    MembreChantier,
    PhotoReleve,
    RelevePosition,
    StatutPosition,
    StatutReleve,
    TypeReleveur,
)
from app.photos import stockage
from app.routers.photos import CACHE_PHOTOS, uuid_ou_404
from app.services.position import (
    DISTANCE_ALERTE_M,
    DUREE_LIEN,
    ReleveRefuse,
    alertes_releve,
    annuler_releve,
    creer_releve,
    distance_a_la_position_actuelle,
    geojson_releve,
    historique,
    rejeter_releve,
    releves_du_chantier,
    source_du_releve,
    valider_releve,
)
from app.templating import templates

router = APIRouter(tags=["position"], include_in_schema=False)


LIBELLES_STATUT_RELEVE = {
    StatutReleve.EN_ATTENTE: ("Lien envoyé", "neutre", "clock"),
    StatutReleve.RECU: ("À valider", "primaire", "map-pin"),
    StatutReleve.VALIDE: ("Validé", "succes", "circle-check"),
    StatutReleve.REJETE: ("Rejeté", "danger", "circle-x"),
    StatutReleve.EXPIRE: ("Expiré", "neutre", "clock-alert"),
    StatutReleve.ANNULE: ("Annulé", "neutre", "ban"),
}
LIBELLES_TYPE_RELEVEUR = {
    TypeReleveur.PROCHE: "Proche",
    TypeReleveur.CHEF_CHANTIER: "Chef de chantier",
}


def contexte_libelles() -> dict:
    return {
        "LIBELLES_STATUT_RELEVE": LIBELLES_STATUT_RELEVE,
        "LIBELLES_TYPE_RELEVEUR": LIBELLES_TYPE_RELEVEUR,
        "LIBELLES_STATUT_POSITION": LIBELLES_STATUT_POSITION,
        "LIBELLES_SOURCE_POSITION": LIBELLES_SOURCE_POSITION,
        "fiabilite_de": fiabilite,
    }


def releve_du_proprietaire(
    releve_id: str,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
) -> RelevePosition:
    releve = db.scalar(
        select(RelevePosition)
        .options(joinedload(RelevePosition.realisateur))
        .where(
            RelevePosition.id == uuid_ou_404(releve_id),
            RelevePosition.chantier_id == membre.chantier_id,
        )
    )
    if releve is None:
        raise HTTPException(404)
    return releve


@router.get("/chantiers/{id}/position")
def page_position(
    request: Request,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    chantier = membre.chantier
    return templates.TemplateResponse(
        request,
        "position/index.html",
        {
            "chantier": chantier,
            "fiabilite": niveau_fiabilite(chantier),
            "position": lat_lon_chantier(db, chantier.id),
            "releves": releves_du_chantier(db, chantier.id),
            "historique": historique(db, chantier.id),
            **contexte_libelles(),
        },
    )


def page_demande(
    request: Request,
    membre: MembreChantier,
    type_releveur: TypeReleveur,
    erreur: str | None = None,
    valeurs: dict | None = None,
    code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "position/demander.html",
        {
            "chantier": membre.chantier,
            "type_releveur": type_releveur,
            "erreur": erreur,
            "valeurs": valeurs or {},
            "duree_h": int(DUREE_LIEN.total_seconds() // 3600),
        },
        status_code=code,
    )


@router.get("/chantiers/{id}/position/demander")
def formulaire_demande(
    request: Request,
    type: str = "proche",
    membre: MembreChantier = Depends(proprietaire_du_chantier),
):
    type_releveur = (
        TypeReleveur.CHEF_CHANTIER
        if type == TypeReleveur.CHEF_CHANTIER.value
        else TypeReleveur.PROCHE
    )
    return page_demande(request, membre, type_releveur)


def message_whatsapp(releve: RelevePosition, lien: str) -> str:
    if releve.type_releveur == TypeReleveur.CHEF_CHANTIER:
        return (
            "Bonjour, pouvez-vous relever la position du chantier ? "
            f"Ouvrez ce lien sur le terrain, ça prend 2 minutes : {lien}"
        )
    return (
        "Bonjour, peux-tu m'aider ? "
        f"Va sur mon terrain et ouvre ce lien, ça prend 2 minutes : {lien}"
    )


@router.post("/chantiers/{id}/position/releves")
def demander_releve(
    request: Request,
    nom_releveur: str = Form(""),
    lien_releveur: str = Form(""),
    type_releveur: str = Form(TypeReleveur.PROCHE.value),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    try:
        type_choisi = TypeReleveur(type_releveur)
    except ValueError:
        raise HTTPException(400, "Type de relevé inconnu.") from None
    valeurs = {"nom_releveur": nom_releveur, "lien_releveur": lien_releveur}
    try:
        releve, jeton = creer_releve(
            db, membre.chantier, membre.user, nom_releveur, lien_releveur, type_choisi
        )
        db.commit()
    except ReleveRefuse as exc:
        db.rollback()
        return page_demande(request, membre, type_choisi, str(exc), valeurs, exc.code_http)
    except IntegrityError:
        # Deux demandes au même instant : l'index unique partiel n'en laisse passer qu'une.
        db.rollback()
        return page_demande(
            request, membre, type_choisi, "Un lien de relevé est déjà en attente.", valeurs, 409
        )

    lien = str(request.url_for("page_releve", jeton=jeton))
    # Le jeton n'est affiché qu'ici : la base n'en garde que l'empreinte.
    return templates.TemplateResponse(
        request,
        "position/lien.html",
        {
            "chantier": membre.chantier,
            "releve": releve,
            "lien": lien,
            "lien_whatsapp": "https://wa.me/?text=" + quote(message_whatsapp(releve, lien)),
        },
    )


@router.post("/chantiers/{id}/position/releves/{releve_id}/annuler")
def annuler(
    releve: RelevePosition = Depends(releve_du_proprietaire),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    try:
        annuler_releve(releve, membre.user)
    except ReleveRefuse as exc:
        db.rollback()
        raise HTTPException(exc.code_http, str(exc)) from None
    db.commit()
    return RedirectResponse(f"/chantiers/{membre.chantier_id}/position", status_code=303)


def afficher_releve(
    request: Request,
    db: Session,
    membre: MembreChantier,
    releve: RelevePosition,
    erreur: str | None = None,
    code: int = 200,
):
    """Validation d'un relevé reçu, ou consultation d'un relevé déjà traité."""
    chantier = membre.chantier
    distance = (
        distance_a_la_position_actuelle(db, releve)
        if releve.position_resultat is not None
        else None
    )
    geojson = geojson_releve(db, releve) if releve.position_resultat is not None else {}
    return templates.TemplateResponse(
        request,
        "position/releve.html",
        {
            "chantier": chantier,
            "releve": releve,
            "fiabilite": niveau_fiabilite(chantier),
            # Niveau que prendra la position si le relevé est validé.
            "fiabilite_releve": fiabilite(StatutPosition.CONFIRMEE, source_du_releve(releve)),
            "distance_m": distance,
            "alerte_distance": distance is not None and distance > DISTANCE_ALERTE_M,
            "alertes": alertes_releve(releve),
            "qualite": qualite_signal(releve.precision_estimee_m, releve.signal_faible),
            "carte": {
                cle: json.loads(valeur) if valeur else None for cle, valeur in geojson.items()
            },
            "erreur": erreur,
            **contexte_libelles(),
        },
        status_code=code,
    )


@router.get("/chantiers/{id}/position/releves/{releve_id}")
def voir_releve(
    request: Request,
    releve: RelevePosition = Depends(releve_du_proprietaire),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    return afficher_releve(request, db, membre, releve)


@router.post("/chantiers/{id}/position/releves/{releve_id}/valider")
def valider(
    request: Request,
    taches: BackgroundTasks,
    confirmer_remplacement: str = Form(""),
    releve: RelevePosition = Depends(releve_du_proprietaire),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    remplace = (
        releve.statut == StatutReleve.RECU
        and membre.chantier.statut_position == StatutPosition.CONFIRMEE
    )
    if remplace and not confirmer_remplacement:
        # Position verrouillée : la remplacer demande une confirmation explicite.
        return afficher_releve(
            request,
            db,
            membre,
            releve,
            "Cochez la case pour confirmer le remplacement de la position confirmée.",
            400,
        )
    try:
        changement = valider_releve(db, releve, membre.user)
    except ReleveRefuse as exc:
        db.rollback()
        return afficher_releve(request, db, membre, releve, str(exc), exc.code_http)
    db.commit()
    notifier_position_modifiee(
        db, taches, changement.id
    )  # tous les propriétaires, non désactivable
    return RedirectResponse(f"/chantiers/{membre.chantier_id}?position=validee", status_code=303)


@router.post("/chantiers/{id}/position/releves/{releve_id}/rejeter")
def rejeter(
    request: Request,
    motif: str = Form(""),
    releve: RelevePosition = Depends(releve_du_proprietaire),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    try:
        rejeter_releve(db, releve, membre.user, motif)
    except ReleveRefuse as exc:
        db.rollback()
        return afficher_releve(request, db, membre, releve, str(exc), exc.code_http)
    db.commit()
    return RedirectResponse(f"/chantiers/{membre.chantier_id}/position", status_code=303)


@router.get("/chantiers/{id}/position/releves/{releve_id}/photos/{photo_id}")
def photo_du_releve(
    photo_id: str,
    miniature: bool = False,
    releve: RelevePosition = Depends(releve_du_proprietaire),
    db: Session = Depends(get_db),
):
    photo = db.scalar(
        select(PhotoReleve).where(
            PhotoReleve.id == uuid_ou_404(photo_id), PhotoReleve.releve_id == releve.id
        )
    )
    chemin = stockage.chemin_existant(
        (photo.chemin_miniature if miniature else photo.chemin_fichier) if photo else None
    )
    if chemin is None:
        raise HTTPException(404)
    return FileResponse(chemin, media_type="image/jpeg", headers={"Cache-Control": CACHE_PHOTOS})
