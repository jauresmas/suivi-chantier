"""Chantiers : liste, création, modification, invitations. Le tableau de bord est dans
tableau_de_bord.py."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import get_current_user, proprietaire_du_chantier
from app.auth.securite import empreinte, nouveau_jeton
from app.database import get_db
from app.emails.notifications import notifier_position_modifiee
from app.formulaire_chantier import DonneesChantier, FormulaireChantier, valider
from app.geo.coordonnees import distance_m
from app.geo.fiabilite import niveau_fiabilite
from app.geo.nominatim import nominatim
from app.geo.positions import lat_lon_chantier
from app.geo.verification import verifier_photos
from app.libelles import LIBELLES_ROLE
from app.modeles_etapes import MODELES
from app.models import (
    Chantier,
    Etape,
    Invitation,
    MembreChantier,
    Rapport,
    RoleMembre,
    StatutPosition,
    StatutRapport,
    User,
)
from app.services.avancement import avancement_global
from app.services.position import placer_sur_la_carte
from app.templating import templates

router = APIRouter(tags=["chantiers"], include_in_schema=False)

DUREE_INVITATION = timedelta(days=7)
DISTANCE_ALERTE_KM = 50
# Le formulaire arrondit à 6 décimales (11 cm) : en deçà, la position n'a pas bougé.
DEPLACEMENT_MIN_M = 0.5
DEVISES = ("XOF", "XAF", "EUR", "USD")


@router.get("/chantiers")
def liste_chantiers(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    adhesions = db.scalars(
        select(MembreChantier)
        .join(MembreChantier.chantier)
        .options(joinedload(MembreChantier.chantier))
        .where(MembreChantier.user_id == user.id)
        .order_by(Chantier.nom)
    ).all()
    ids = [a.chantier_id for a in adhesions]
    # Deux requêtes groupées pour tous les chantiers, plutôt qu'une par chantier.
    etapes_par_chantier: dict = {}
    for etape in db.scalars(select(Etape).where(Etape.chantier_id.in_(ids))):
        etapes_par_chantier.setdefault(etape.chantier_id, []).append(etape)
    derniers_rapports = dict(
        db.execute(
            select(Rapport.chantier_id, func.max(Rapport.publie_le))
            .where(Rapport.chantier_id.in_(ids), Rapport.statut == StatutRapport.PUBLIE)
            .group_by(Rapport.chantier_id)
        ).all()
    )
    resumes = {
        a.chantier_id: {
            "avancement": avancement_global(etapes_par_chantier.get(a.chantier_id, [])),
            "dernier_rapport": derniers_rapports.get(a.chantier_id),
            "fiabilite": niveau_fiabilite(a.chantier),
        }
        for a in adhesions
    }
    return templates.TemplateResponse(
        request, "chantiers/liste.html", {"adhesions": adhesions, "resumes": resumes}
    )


def page_formulaire(
    request: Request,
    formulaire: FormulaireChantier,
    erreurs: dict[str, str] | None = None,
    chantier: Chantier | None = None,
    deplacement_m: float | None = None,
):
    """deplacement_m : la position confirmée serait déplacée d'autant, une confirmation est
    demandée."""
    return templates.TemplateResponse(
        request,
        "chantiers/formulaire.html",
        {
            "f": formulaire,
            "erreurs": erreurs or {},
            "chantier": chantier,
            "fiabilite": niveau_fiabilite(chantier) if chantier else None,
            "deplacement_m": deplacement_m,
            "devises": DEVISES,
            "modeles": MODELES,
        },
        status_code=400 if erreurs else 409 if deplacement_m is not None else 200,
    )


def url_apres_enregistrement(chantier_id: uuid.UUID, donnees: DonneesChantier) -> str:
    """Page du chantier, avec un avertissement si le point est loin de la ville saisie (inversion
    lat/lon ?)."""
    url = f"/chantiers/{chantier_id}"
    if (
        donnees.position
        and donnees.ville
        and (centre := nominatim.geocoder_ville(donnees.ville, donnees.pays))
    ):
        km = distance_m(*donnees.position, *centre) / 1000
        if km > DISTANCE_ALERTE_KM:
            url += f"?alerte_distance_km={round(km)}"
    return url


@router.get("/chantiers/nouveau")
def formulaire_nouveau_chantier(request: Request, user: User = Depends(get_current_user)):
    return page_formulaire(request, FormulaireChantier())


@router.post("/chantiers/nouveau")
def creer_chantier(
    request: Request,
    formulaire: Annotated[FormulaireChantier, Form()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    donnees, erreurs = valider(formulaire)
    if erreurs:
        return page_formulaire(request, formulaire, erreurs)

    chantier = Chantier(**donnees.colonnes())
    # Le créateur devient propriétaire, dans la même transaction que la création.
    chantier.membres.append(MembreChantier(user_id=user.id, role=RoleMembre.PROPRIETAIRE))
    if modele := MODELES.get(formulaire.modele_etapes):
        chantier.etapes += [
            Etape(type=type_, poids=poids, ordre=ordre)
            for ordre, (type_, poids) in enumerate(modele.etapes, start=1)
        ]
    db.add(chantier)
    db.flush()
    if donnees.position:
        # Sans position, le chantier reste « à définir » : un proche pourra la relever sur le
        # terrain.
        placer_sur_la_carte(db, chantier, *donnees.position, user)
    db.commit()
    return RedirectResponse(url_apres_enregistrement(chantier.id, donnees), status_code=303)


@router.get("/chantiers/{id}/modifier")
def formulaire_modifier_chantier(
    request: Request,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    formulaire = FormulaireChantier.depuis_chantier(
        membre.chantier, lat_lon_chantier(db, membre.chantier_id)
    )
    return page_formulaire(request, formulaire, chantier=membre.chantier)


@router.post("/chantiers/{id}/modifier")
def modifier_chantier(
    request: Request,
    formulaire: Annotated[FormulaireChantier, Form()],
    taches: BackgroundTasks,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    chantier = membre.chantier
    donnees, erreurs = valider(formulaire)
    position_avant = lat_lon_chantier(db, chantier.id)
    deplacement_m = None
    if donnees and donnees.position:
        deplacement_m = (
            distance_m(*donnees.position, *position_avant) if position_avant else float("inf")
        )
        if deplacement_m < DEPLACEMENT_MIN_M:
            deplacement_m = None
    if donnees and donnees.position is None and position_avant:
        erreurs["position"] = (
            "Le chantier est déjà positionné : "
            "déplacez le marqueur plutôt que d'effacer la position."
        )
    elif deplacement_m is not None and chantier.statut_position == StatutPosition.A_VALIDER:
        erreurs["position"] = (
            "Un relevé attend votre validation : "
            "validez-le ou rejetez-le avant de déplacer la position."
        )
    if erreurs:
        return page_formulaire(request, formulaire, erreurs, chantier=chantier)
    if (
        deplacement_m is not None
        and chantier.statut_position == StatutPosition.CONFIRMEE
        and not formulaire.confirmer_deplacement
    ):
        # Position verrouillée : la déplacer demande une confirmation explicite.
        return page_formulaire(request, formulaire, chantier=chantier, deplacement_m=deplacement_m)

    rayon_avant = chantier.rayon_verification_m
    for colonne, valeur in donnees.colonnes().items():
        setattr(chantier, colonne, valeur)
    changement = None
    if deplacement_m is not None:
        changement = placer_sur_la_carte(
            db, chantier, *donnees.position, membre.user
        )  # historique et revérification
    elif chantier.rayon_verification_m != rayon_avant:
        # Rayon modifié : toutes les photos du chantier sont revérifiées, en une requête.
        verifier_photos(db, chantier_id=chantier.id)
    db.commit()
    if changement is not None:
        notifier_position_modifiee(
            db, taches, changement.id
        )  # tous les propriétaires, non désactivable
    return RedirectResponse(url_apres_enregistrement(membre.chantier_id, donnees), status_code=303)


@router.get("/chantiers/{id}/inviter")
def page_inviter(request: Request, membre: MembreChantier = Depends(proprietaire_du_chantier)):
    return templates.TemplateResponse(
        request, "invitations/inviter.html", {"chantier": membre.chantier}
    )


@router.post("/chantiers/{id}/invitations")
def creer_invitation(
    request: Request,
    role: str = Form(RoleMembre.CHEF_CHANTIER.value),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    try:
        role_invite = RoleMembre(role)
    except ValueError:
        raise HTTPException(400, "Rôle inconnu.") from None

    jeton = nouveau_jeton()
    invitation = Invitation(
        chantier_id=membre.chantier_id,
        invite_par=membre.user_id,
        role=role_invite,
        empreinte_jeton=empreinte(jeton),
        expire_le=datetime.now(UTC) + DUREE_INVITATION,
    )
    db.add(invitation)
    db.commit()

    chantier = membre.chantier
    lien = str(request.url_for("voir_invitation", jeton=jeton))
    message = (
        f"Bonjour, je vous invite à suivre le chantier « {chantier.nom} » comme "
        f"{LIBELLES_ROLE[role_invite].lower()}. Créez votre compte avec ce lien, "
        f"valable 7 jours : {lien}"
    )
    # Le jeton n'est affiché qu'ici : la base n'en garde que l'empreinte.
    return templates.TemplateResponse(
        request,
        "invitations/lien.html",
        {
            "chantier": chantier,
            "invitation": invitation,
            "lien": lien,
            "lien_whatsapp": "https://wa.me/?text=" + quote(message),
        },
    )
