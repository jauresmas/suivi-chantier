"""Préférences de notification (/compte/notifications) et désinscription en un clic depuis un
e-mail."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.emails.preferences import LIBELLES_MODE, enregistrer_mode, lire_jeton_desinscription
from app.models import (
    Chantier,
    MembreChantier,
    ModeNotification,
    PreferenceNotification,
    RoleMembre,
    User,
)
from app.templating import templates

router = APIRouter(tags=["compte"], include_in_schema=False)


def chantiers_du_proprietaire(db: Session, user: User) -> list[tuple[Chantier, ModeNotification]]:
    lignes = db.execute(
        select(Chantier, PreferenceNotification.mode)
        .join(MembreChantier, MembreChantier.chantier_id == Chantier.id)
        .outerjoin(
            PreferenceNotification,
            (PreferenceNotification.chantier_id == Chantier.id)
            & (PreferenceNotification.user_id == user.id),
        )
        .where(MembreChantier.user_id == user.id, MembreChantier.role == RoleMembre.PROPRIETAIRE)
        .order_by(Chantier.nom)
    ).all()
    return [(chantier, mode or ModeNotification.IMMEDIAT) for chantier, mode in lignes]


def page_preferences(request: Request, db: Session, user: User, enregistre: bool = False):
    return templates.TemplateResponse(
        request,
        "compte/notifications.html",
        {
            "chantiers": chantiers_du_proprietaire(db, user),
            "chef_ailleurs": db.scalar(
                select(MembreChantier.chantier_id)
                .where(
                    MembreChantier.user_id == user.id,
                    MembreChantier.role == RoleMembre.CHEF_CHANTIER,
                )
                .limit(1)
            )
            is not None,
            "modes": LIBELLES_MODE,
            "enregistre": enregistre,
        },
    )


@router.get("/compte/notifications")
def preferences(
    request: Request,
    enregistre: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return page_preferences(request, db, user, enregistre == "1")


@router.post("/compte/notifications")
async def enregistrer_preferences(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Un champ « mode_<id du chantier> » par chantier ; seuls ceux dont l'utilisateur est
    propriétaire comptent."""
    formulaire = await request.form()
    for chantier, _ in chantiers_du_proprietaire(db, user):
        try:
            mode = ModeNotification(formulaire.get(f"mode_{chantier.id}", ""))
        except ValueError:
            continue
        enregistrer_mode(db, user.id, chantier.id, mode)
    db.commit()
    return RedirectResponse("/compte/notifications?enregistre=1", status_code=303)


def page_desinscription(request: Request, db: Session, jeton: str, fait: bool = False):
    lu = lire_jeton_desinscription(jeton)
    membre = None
    if lu is not None:
        user_id, chantier_id = lu
        membre = db.scalar(
            select(MembreChantier)
            .options(joinedload(MembreChantier.chantier))
            .where(MembreChantier.user_id == user_id, MembreChantier.chantier_id == chantier_id)
        )
    if membre is None:
        return templates.TemplateResponse(
            request, "compte/desinscription.html", {"invalide": True}, status_code=404
        )
    return templates.TemplateResponse(
        request,
        "compte/desinscription.html",
        {"invalide": False, "chantier": membre.chantier, "jeton": jeton, "fait": fait},
    )


@router.get("/notifications/desinscription/{jeton}")
def confirmer_desinscription(request: Request, jeton: str, db: Session = Depends(get_db)):
    """Ne change rien : les antivirus de messagerie ouvrent les liens des e-mails pour les analyser.
    Un seul bouton à toucher sur cette page ; les messageries compatibles envoient directement le
    POST."""
    return page_desinscription(request, db, jeton)


@router.post("/notifications/desinscription/{jeton}")
def desinscrire(request: Request, jeton: str, db: Session = Depends(get_db)):
    """Mode « aucun » pour ce chantier. Sans jeton CSRF (voir CHEMINS_SANS_CSRF) : le lien est
    signé."""
    lu = lire_jeton_desinscription(jeton)
    if lu is not None:
        user_id, chantier_id = lu
        if db.get(MembreChantier, (chantier_id, user_id)) is not None:
            enregistrer_mode(db, user_id, chantier_id, ModeNotification.AUCUN)
            db.commit()
    return page_desinscription(request, db, jeton, fait=True)
