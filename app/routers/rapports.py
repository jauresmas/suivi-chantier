"""Rapports : création (ou reprise) du brouillon, publication, suppression de photo,
consultation."""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, membre_du_chantier_api, utilisateur_api
from app.database import get_db
from app.emails.notifications import notifier_rapport_publie
from app.models import HistoriqueAvancement, MembreChantier, Photo, Rapport, StatutRapport, User
from app.rapports import (
    PublicationRefusee,
    brouillon_recent,
    publier,
    rapport_visible,
    supprimer_photo,
)
from app.routers.photos import page_envoi, uuid_ou_404
from app.templating import templates

router = APIRouter(tags=["rapports"], include_in_schema=False)


@router.post("/chantiers/{id}/rapports")
def creer_rapport(
    membre: MembreChantier = Depends(membre_du_chantier_api), db: Session = Depends(get_db)
):
    """Brouillon auquel la page d'envoi rattache ses photos. Un brouillon récent est repris (200)
    plutôt que d'en créer un nouveau (201)."""
    rapport = brouillon_recent(db, membre.chantier_id, membre.user_id)
    if rapport is not None:
        return {"id": str(rapport.id)}
    rapport = Rapport(chantier_id=membre.chantier_id, auteur_id=membre.user_id)
    db.add(rapport)
    db.commit()
    return JSONResponse({"id": str(rapport.id)}, status_code=201)


@router.post("/rapports/{id}/publier")
def publier_rapport(
    request: Request,
    id: str,
    taches: BackgroundTasks,
    texte: str = Form(""),
    etape_id: str = Form(""),
    avancement_pct: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rapport = rapport_visible(db, uuid_ou_404(id), user)
    if rapport is None:
        raise HTTPException(404)
    if rapport.auteur_id != user.id:
        raise HTTPException(403, "Seul l'auteur peut publier ce rapport.")
    try:
        publier(db, rapport, user, texte, etape_id, avancement_pct)
    except PublicationRefusee as exc:
        db.rollback()
        if exc.code_http != 400:
            raise HTTPException(exc.code_http, str(exc)) from None
        membre = db.get(MembreChantier, (rapport.chantier_id, user.id))
        valeurs = {"texte": texte, "etape_id": etape_id, "avancement_pct": avancement_pct}
        return page_envoi(request, db, membre, rapport, erreur=str(exc), valeurs=valeurs)
    db.commit()
    notifier_rapport_publie(db, taches, rapport.id)  # e-mails envoyés après la réponse
    return RedirectResponse(f"/rapports/{rapport.id}", status_code=303)


@router.delete("/rapports/{id}/photos/{photo_id}", status_code=204)
def supprimer_photo_du_brouillon(
    id: str, photo_id: str, user: User = Depends(utilisateur_api), db: Session = Depends(get_db)
):
    rapport = rapport_visible(db, uuid_ou_404(id), user)
    if rapport is None:
        raise HTTPException(404)
    if rapport.statut == StatutRapport.PUBLIE:
        raise HTTPException(409, "Rapport publié : ses photos ne peuvent plus être supprimées.")
    if rapport.auteur_id != user.id:
        raise HTTPException(404)
    photo = db.get(Photo, uuid_ou_404(photo_id))
    if photo is None or photo.rapport_id != rapport.id:
        raise HTTPException(404)
    supprimer_photo(db, photo)
    return Response(status_code=204)


@router.get("/rapports/{id}")
def detail_rapport(
    request: Request, id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rapport = rapport_visible(db, uuid_ou_404(id), user)
    if rapport is None:
        raise HTTPException(404)
    photos = db.scalars(
        select(Photo).where(Photo.rapport_id == rapport.id).order_by(Photo.envoyee_le)
    ).all()
    avancement = db.scalar(
        select(HistoriqueAvancement).where(HistoriqueAvancement.rapport_id == rapport.id)
    )
    return templates.TemplateResponse(
        request,
        "rapports/detail.html",
        {
            "rapport": rapport,
            "photos": photos,
            "avancement": avancement,
            "est_auteur": rapport.auteur_id == user.id,
        },
    )
