"""Ouverture et acceptation d'un lien d'invitation."""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user_optional
from app.auth.securite import empreinte
from app.database import get_db
from app.emails.notifications import notifier_invitation_acceptee
from app.models import Invitation, MembreChantier, User
from app.templating import templates

router = APIRouter(tags=["invitations"], include_in_schema=False)

# (code HTTP, message) affichés quand le lien ne peut pas servir
LIEN_INCONNU = (404, "Ce lien d'invitation n'est pas valide. Vérifiez qu'il a été copié en entier.")
LIEN_UTILISE = (410, "Ce lien d'invitation a déjà été utilisé.")
LIEN_EXPIRE = (410, "Ce lien d'invitation a expiré.")


def charger_invitation(db: Session, jeton: str, verrouiller: bool = False) -> Invitation | None:
    requete = select(Invitation).where(Invitation.empreinte_jeton == empreinte(jeton))
    if verrouiller:
        # Deux personnes qui acceptent en même temps : une seule l'emporte.
        requete = requete.with_for_update()
    return db.scalar(requete)


def probleme_invitation(invitation: Invitation | None) -> tuple[int, str] | None:
    if invitation is None:
        return LIEN_INCONNU
    if invitation.utilisee_le is not None:
        return LIEN_UTILISE
    if invitation.expire_le <= datetime.now(UTC):
        return LIEN_EXPIRE
    return None


def page_lien_invalide(request: Request, probleme: tuple[int, str]):
    code, message = probleme
    return templates.TemplateResponse(
        request, "invitations/invalide.html", {"message": message}, status_code=code
    )


@router.get("/invitations/{jeton}", name="voir_invitation")
def voir_invitation(
    request: Request,
    jeton: str,
    utilisateur: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    invitation = charger_invitation(db, jeton)
    if probleme := probleme_invitation(invitation):
        return page_lien_invalide(request, probleme)

    deja_membre = utilisateur is not None and db.get(
        MembreChantier, (invitation.chantier_id, utilisateur.id)
    )
    return templates.TemplateResponse(
        request,
        "invitations/voir.html",
        {
            "invitation": invitation,
            "chantier": invitation.chantier,
            "deja_membre": deja_membre,
            "jeton": jeton,
            "suivant": quote(request.url.path, safe=""),
        },
    )


@router.post("/invitations/{jeton}/accepter")
def accepter_invitation(
    request: Request,
    jeton: str,
    taches: BackgroundTasks,
    utilisateur: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if utilisateur is None:
        return RedirectResponse(
            f"/connexion?suivant={quote(f'/invitations/{jeton}', safe='')}", status_code=303
        )

    invitation = charger_invitation(db, jeton, verrouiller=True)
    if probleme := probleme_invitation(invitation):
        db.rollback()
        return page_lien_invalide(request, probleme)

    url_chantier = f"/chantiers/{invitation.chantier_id}"
    if db.get(MembreChantier, (invitation.chantier_id, utilisateur.id)):
        # Déjà membre : l'invitation reste disponible pour la personne à qui elle était destinée.
        db.rollback()
        return RedirectResponse(url_chantier, status_code=303)

    db.add(
        MembreChantier(
            chantier_id=invitation.chantier_id, user_id=utilisateur.id, role=invitation.role
        )
    )
    invitation.utilisee_le = datetime.now(UTC)
    invitation.utilisee_par = utilisateur.id
    db.commit()
    notifier_invitation_acceptee(db, taches, invitation, utilisateur)
    return RedirectResponse(url_chantier, status_code=303)
