"""Envoi des photos depuis le chantier, et accès protégé aux fichiers."""

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import membre_du_chantier, utilisateur_api
from app.database import get_db
from app.geo.positions import point
from app.geo.verification import badge_photo, verifier_photos
from app.icones import icone
from app.models import Etape, MembreChantier, Photo, Rapport, StatutRapport, User
from app.photos import stockage
from app.photos.metadonnees import MetadonneesInvalides, lire_metadonnees
from app.photos.traitement import TAILLE_MAX_OCTETS, ImageRefusee, traiter_image
from app.rapports import brouillon_recent, rapport_visible, visible_par
from app.templating import templates

router = APIRouter(tags=["photos"], include_in_schema=False)

CACHE_PHOTOS = "private, max-age=86400"


def uuid_ou_404(texte: str) -> uuid.UUID:
    try:
        return uuid.UUID(texte)
    except ValueError:
        raise HTTPException(404) from None


def page_envoi(
    request: Request,
    db: Session,
    membre: MembreChantier,
    rapport: Rapport | None,
    erreur: str | None = None,
    valeurs: dict | None = None,
):
    """Page d'envoi des photos et formulaire de publication du rapport (brouillon repris s'il
    existe)."""
    photos = []
    if rapport is not None:
        photos = db.scalars(
            select(Photo).where(Photo.rapport_id == rapport.id).order_by(Photo.envoyee_le.desc())
        ).all()
    etapes = db.scalars(
        select(Etape).where(Etape.chantier_id == membre.chantier_id).order_by(Etape.ordre)
    ).all()
    valeurs = valeurs or {
        "texte": (rapport.texte if rapport else None) or "",
        "etape_id": str(rapport.etape_id) if rapport and rapport.etape_id else "",
    }
    return templates.TemplateResponse(
        request,
        "photos/envoyer.html",
        {
            "chantier": membre.chantier,
            "rapport": rapport,
            "photos": photos,
            "etapes": etapes,
            "erreur": erreur,
            "valeurs": valeurs,
        },
        status_code=400 if erreur else 200,
    )


@router.get("/chantiers/{id}/photos/envoyer")
def page_envoi_photos(
    request: Request,
    membre: MembreChantier = Depends(membre_du_chantier),
    db: Session = Depends(get_db),
):
    return page_envoi(request, db, membre, brouillon_recent(db, membre.chantier_id, membre.user_id))


def brouillon_de_l_auteur(
    id: str, user: User = Depends(utilisateur_api), db: Session = Depends(get_db)
) -> Rapport:
    """Brouillon dont l'utilisateur est l'auteur. Le brouillon d'un autre est invisible (404) ;
    un rapport publié ne reçoit plus de photos (409)."""
    rapport = rapport_visible(db, uuid_ou_404(id), user)
    if rapport is None:
        raise HTTPException(404)
    if rapport.statut == StatutRapport.PUBLIE:
        raise HTTPException(409, "Ce rapport est publié : il ne peut plus être modifié.")
    if rapport.auteur_id != user.id:
        raise HTTPException(404)
    return rapport


@router.post("/rapports/{id}/photos", status_code=201)
def envoyer_photo(
    fichier: UploadFile = File(...),
    latitude: str = Form(""),
    longitude: str = Form(""),
    precision_m: str = Form(""),
    position_obtenue_le: str = Form(""),
    source_position: str = Form("aucune"),
    prise_le: str = Form(""),
    empreinte_originale: str = Form(""),
    rapport: Rapport = Depends(brouillon_de_l_auteur),
    db: Session = Depends(get_db),
):
    donnees = fichier.file.read(TAILLE_MAX_OCTETS + 1)
    if len(donnees) > TAILLE_MAX_OCTETS:
        raise HTTPException(413, "Photo trop lourde : 15 Mo maximum.")
    if not donnees:
        raise HTTPException(400, "Fichier vide.")
    try:
        meta = lire_metadonnees(
            latitude=latitude,
            longitude=longitude,
            precision_m=precision_m,
            position_obtenue_le=position_obtenue_le,
            source_position=source_position,
            prise_le=prise_le,
            empreinte_originale=empreinte_originale,
        )
    except MetadonneesInvalides as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        image = traiter_image(donnees)
    except ImageRefusee as exc:
        raise HTTPException(415, str(exc)) from None

    photo_id = uuid.uuid4()
    chemin, chemin_miniature = stockage.chemins_photo(
        rapport.chantier_id, photo_id, datetime.now(UTC)
    )
    photo = Photo(
        id=photo_id,
        rapport_id=rapport.id,
        chemin_fichier=chemin,
        chemin_miniature=chemin_miniature,
        empreinte_sha256=hashlib.sha256(donnees).hexdigest(),
        empreinte_originale=meta.empreinte_originale,
        position=point(meta.latitude, meta.longitude) if meta.latitude is not None else None,
        source_position=meta.source_position,
        precision_m=meta.precision_m,
        position_obtenue_le=meta.position_obtenue_le,
        prise_le=meta.prise_le,
        largeur_px=image.largeur_px,
        hauteur_px=image.hauteur_px,
        taille_octets=len(image.contenu),
    )
    try:
        stockage.ecrire(chemin, image.contenu)
        stockage.ecrire(chemin_miniature, image.miniature)
        db.add(photo)
        verifier_photos(
            db, photo_id=photo_id
        )  # distance, statut et alertes, dans la même transaction
        db.commit()
    except Exception:
        db.rollback()
        stockage.supprimer(chemin)
        stockage.supprimer(chemin_miniature)
        raise

    db.refresh(photo)
    badge = badge_photo(photo, rapport.chantier.statut_position)
    return {
        "id": str(photo_id),
        "url": f"/photos/{photo_id}",
        "miniature_url": f"/photos/{photo_id}/miniature",
        "source_position": photo.source_position.value,
        "precision_m": photo.precision_m,
        "largeur_px": photo.largeur_px,
        "hauteur_px": photo.hauteur_px,
        "statut_verification": photo.statut_verification.value,
        "distance_chantier_m": photo.distance_chantier_m,
        "alertes": photo.alertes,
        "badge": {
            "texte": badge.texte,
            "ton": badge.ton,
            "icone_svg": str(icone(badge.icone, 14)),
            "titre": badge.titre,
            "avertissement": badge.avertissement,
            "alertes": badge.alertes,
            "mention": badge.mention,
        },
    }


def photo_du_membre(
    id: str, user: User = Depends(utilisateur_api), db: Session = Depends(get_db)
) -> Photo:
    """Photo d'un chantier dont l'utilisateur est membre, hors brouillons des autres ; sinon 404."""
    photo = db.scalar(
        select(Photo)
        .join(Rapport, Photo.rapport_id == Rapport.id)
        .join(MembreChantier, MembreChantier.chantier_id == Rapport.chantier_id)
        .where(Photo.id == uuid_ou_404(id), MembreChantier.user_id == user.id, visible_par(user.id))
    )
    if photo is None:
        raise HTTPException(404)
    return photo


def _fichier(relatif: str | None) -> FileResponse:
    chemin = stockage.chemin_existant(relatif)
    if chemin is None:
        raise HTTPException(404)
    return FileResponse(chemin, media_type="image/jpeg", headers={"Cache-Control": CACHE_PHOTOS})


@router.get("/photos/{id}")
def servir_photo(photo: Photo = Depends(photo_du_membre)):
    return _fichier(photo.chemin_fichier)


@router.get("/photos/{id}/miniature")
def servir_miniature(photo: Photo = Depends(photo_du_membre)):
    return _fichier(photo.chemin_miniature or photo.chemin_fichier)
