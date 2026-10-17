"""Étapes d'un chantier : consultation par tous les membres, gestion par le propriétaire seul."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import membre_du_chantier, proprietaire_du_chantier
from app.database import get_db
from app.modeles_etapes import POIDS_PAR_DEFAUT
from app.models import (
    Etape,
    HistoriqueAvancement,
    MembreChantier,
    Rapport,
    RoleMembre,
    StatutRapport,
    TypeEtape,
    User,
)
from app.routers.photos import uuid_ou_404
from app.services.avancement import (
    avancement_global,
    changer_avancement,
    deplacer,
    etape_referencee,
    etapes_du_chantier,
    prochain_ordre,
)
from app.templating import templates

router = APIRouter(tags=["étapes"], include_in_schema=False)


class FormulaireEtape(BaseModel):
    type: str = TypeEtape.AUTRE.value  # à l'ajout seulement
    libelle: str = ""
    poids: str = ""
    date_fin_prevue: str = ""
    date_debut: str = ""  # dates réelles, modifiables après coup
    date_fin: str = ""


def _date(texte: str, libelle: str, erreurs: list[str]) -> date | None:
    if not texte.strip():
        return None
    try:
        return date.fromisoformat(texte.strip())
    except ValueError:
        erreurs.append(f"{libelle} : date invalide.")
        return None


def valider(formulaire: FormulaireEtape, type_etape: TypeEtape) -> tuple[dict, list[str]]:
    erreurs: list[str] = []
    libelle = formulaire.libelle.strip()
    if len(libelle) > 150:
        erreurs.append("Libellé : 150 caractères au maximum.")
    if type_etape == TypeEtape.AUTRE and not libelle:
        erreurs.append("Donnez un libellé à une étape de type « Autre ».")
    try:
        poids = int(formulaire.poids) if formulaire.poids.strip() else POIDS_PAR_DEFAUT[type_etape]
        if not 1 <= poids <= 100:
            raise ValueError
    except ValueError:
        poids = None
        erreurs.append("Poids : nombre entier entre 1 et 100.")
    valeurs = {
        "libelle": libelle or None,
        "poids": poids,
        "date_fin_prevue": _date(formulaire.date_fin_prevue, "Fin prévue", erreurs),
        "date_debut": _date(formulaire.date_debut, "Début réel", erreurs),
        "date_fin": _date(formulaire.date_fin, "Fin réelle", erreurs),
    }
    if (
        valeurs["date_debut"]
        and valeurs["date_fin"]
        and valeurs["date_fin"] < valeurs["date_debut"]
    ):
        erreurs.append("La fin réelle ne peut pas précéder le début réel.")
    return valeurs, erreurs


def page_etapes(
    request: Request,
    db: Session,
    membre: MembreChantier,
    erreur: str | None = None,
    code: int = 200,
):
    etapes = etapes_du_chantier(db, membre.chantier_id)
    return templates.TemplateResponse(
        request,
        "etapes/liste.html",
        {
            "chantier": membre.chantier,
            "etapes": etapes,
            "avancement": avancement_global(etapes),
            "types": list(TypeEtape),
            "poids_par_defaut": POIDS_PAR_DEFAUT,
            "erreur": erreur,
            "est_proprietaire": membre.role == RoleMembre.PROPRIETAIRE,
            "onglet": "etapes",
        },
        status_code=code,
    )


def retour(membre: MembreChantier, etape: Etape | None = None) -> RedirectResponse:
    ancre = f"#etape-{etape.id}" if etape else ""
    return RedirectResponse(f"/chantiers/{membre.chantier_id}/etapes{ancre}", status_code=303)


def etape_du_chantier(db: Session, membre: MembreChantier, etape_id: str) -> Etape:
    etape = db.get(Etape, uuid_ou_404(etape_id))
    if etape is None or etape.chantier_id != membre.chantier_id:
        raise HTTPException(404)
    return etape


@router.get("/chantiers/{id}/etapes")
def liste_etapes(
    request: Request,
    membre: MembreChantier = Depends(membre_du_chantier),
    db: Session = Depends(get_db),
):
    """Lecture pour tous les membres (onglet « Étapes ») ; les commandes de gestion ne s'affichent
    qu'au propriétaire."""
    return page_etapes(request, db, membre)


@router.post("/chantiers/{id}/etapes")
def ajouter_etape(
    request: Request,
    formulaire: Annotated[FormulaireEtape, Form()],
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    try:
        type_etape = TypeEtape(formulaire.type)
    except ValueError:
        return page_etapes(request, db, membre, "Type d'étape inconnu.", 400)
    valeurs, erreurs = valider(formulaire, type_etape)
    if erreurs:
        return page_etapes(request, db, membre, " ".join(erreurs), 400)
    etape = Etape(
        chantier_id=membre.chantier_id,
        type=type_etape,
        ordre=prochain_ordre(db, membre.chantier_id),
        **valeurs,
    )
    db.add(etape)
    db.commit()
    return retour(membre, etape)


@router.post("/chantiers/{id}/etapes/{etape_id}")
def modifier_etape(
    request: Request,
    etape_id: str,
    formulaire: Annotated[FormulaireEtape, Form()],
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    etape = etape_du_chantier(db, membre, etape_id)
    valeurs, erreurs = valider(formulaire, etape.type)
    if erreurs:
        return page_etapes(request, db, membre, " ".join(erreurs), 400)
    for colonne, valeur in valeurs.items():
        setattr(etape, colonne, valeur)
    db.commit()
    return retour(membre, etape)


@router.post("/chantiers/{id}/etapes/{etape_id}/monter")
def monter_etape(
    etape_id: str,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    etape = etape_du_chantier(db, membre, etape_id)
    deplacer(db, etape, -1)
    db.commit()
    return retour(membre, etape)


@router.post("/chantiers/{id}/etapes/{etape_id}/descendre")
def descendre_etape(
    etape_id: str,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    etape = etape_du_chantier(db, membre, etape_id)
    deplacer(db, etape, 1)
    db.commit()
    return retour(membre, etape)


@router.post("/chantiers/{id}/etapes/{etape_id}/supprimer")
def supprimer_etape(
    request: Request,
    etape_id: str,
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    etape = etape_du_chantier(db, membre, etape_id)
    if etape_referencee(db, etape):
        message = (
            "Cette étape est citée dans au moins un rapport : elle ne peut pas être supprimée. "
            "Vous pouvez changer son libellé ou son poids."
        )
        return page_etapes(request, db, membre, message, 409)
    db.delete(etape)
    db.commit()
    return retour(membre)


@router.post("/chantiers/{id}/etapes/{etape_id}/avancement")
def modifier_avancement(
    request: Request,
    etape_id: str,
    avancement_pct: str = Form(...),
    commentaire: str = Form(""),
    membre: MembreChantier = Depends(proprietaire_du_chantier),
    db: Session = Depends(get_db),
):
    etape = etape_du_chantier(db, membre, etape_id)
    try:
        nouveau = int(avancement_pct)
        if not 0 <= nouveau <= 100:
            raise ValueError
    except ValueError:
        return page_etapes(
            request, db, membre, "L'avancement doit être compris entre 0 et 100 %.", 400
        )
    if len(commentaire) > 1000:
        return page_etapes(request, db, membre, "Commentaire : 1 000 caractères au maximum.", 400)
    changer_avancement(db, etape, nouveau, membre.user_id, commentaire=commentaire.strip())
    db.commit()
    return retour(membre, etape)


@router.get("/chantiers/{id}/etapes/{etape_id}")
def historique_etape(
    request: Request,
    etape_id: str,
    membre: MembreChantier = Depends(membre_du_chantier),
    db: Session = Depends(get_db),
):
    """Historique de l'avancement d'une étape, ouvert à tous les membres du chantier."""
    etape = etape_du_chantier(db, membre, etape_id)
    lignes = db.execute(
        select(HistoriqueAvancement, User.nom_complet, Rapport)
        .outerjoin(User, User.id == HistoriqueAvancement.auteur_id)
        .outerjoin(Rapport, Rapport.id == HistoriqueAvancement.rapport_id)
        .where(HistoriqueAvancement.etape_id == etape.id)
        .order_by(HistoriqueAvancement.cree_le.desc())
    ).all()
    historique = [
        {
            "ligne": ligne,
            "auteur": auteur,
            # Lien vers le rapport seulement s'il est visible du lecteur (publié, ou son propre
            # brouillon).
            "rapport": rapport
            if rapport
            and (rapport.statut == StatutRapport.PUBLIE or rapport.auteur_id == membre.user_id)
            else None,
        }
        for ligne, auteur, rapport in lignes
    ]
    return templates.TemplateResponse(
        request,
        "etapes/historique.html",
        {
            "chantier": membre.chantier,
            "etape": etape,
            "historique": historique,
            "est_proprietaire": membre.role == RoleMembre.PROPRIETAIRE,
        },
    )
