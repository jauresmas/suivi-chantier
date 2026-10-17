"""Rapports : visibilité des brouillons, reprise, publication définitive et nettoyage.

Principe de confiance : un rapport publié ne peut plus être modifié ni supprimé ;
une correction se fait par un nouveau rapport.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import Etape, MembreChantier, Photo, Rapport, StatutRapport, User
from app.photos import stockage
from app.services.avancement import changer_avancement

DUREE_REPRISE_BROUILLON = timedelta(hours=24)
DUREE_VIE_BROUILLON = timedelta(days=7)
TEXTE_MAX = 5000


class PublicationRefusee(Exception):
    def __init__(self, message: str, code_http: int = 400):
        super().__init__(message)
        self.code_http = code_http


def visible_par(user_id: uuid.UUID):
    """Condition SQL : rapport publié, ou brouillon de cet utilisateur (les autres ne le voient
    pas)."""
    return or_(Rapport.statut == StatutRapport.PUBLIE, Rapport.auteur_id == user_id)


def derniere_activite():
    """Création du rapport ou dernière photo envoyée, la plus récente des deux."""
    derniere_photo = (
        select(func.max(Photo.envoyee_le)).where(Photo.rapport_id == Rapport.id).scalar_subquery()
    )
    return func.greatest(Rapport.cree_le, func.coalesce(derniere_photo, Rapport.cree_le))


def rapport_visible(db: Session, rapport_id: uuid.UUID, user: User) -> Rapport | None:
    """Rapport d'un chantier dont l'utilisateur est membre, et qu'il a le droit de voir."""
    return db.scalar(
        select(Rapport)
        .join(MembreChantier, MembreChantier.chantier_id == Rapport.chantier_id)
        .where(Rapport.id == rapport_id, MembreChantier.user_id == user.id, visible_par(user.id))
    )


def brouillon_recent(db: Session, chantier_id: uuid.UUID, auteur_id: uuid.UUID) -> Rapport | None:
    """Brouillon de l'auteur sur ce chantier, actif depuis moins de 24 h : il est repris, pas
    recréé."""
    limite = datetime.now(UTC) - DUREE_REPRISE_BROUILLON
    return db.scalar(
        select(Rapport)
        .where(
            Rapport.chantier_id == chantier_id,
            Rapport.auteur_id == auteur_id,
            Rapport.statut == StatutRapport.BROUILLON,
            derniere_activite() >= limite,
        )
        .order_by(derniere_activite().desc())
        .limit(1)
    )


def publier(
    db: Session, rapport: Rapport, auteur: User, texte: str, etape_id: str, avancement: str
) -> None:
    """Publie le rapport et, si l'avancement de l'étape change, le met à jour avec son historique.

    Tout se fait dans la transaction de l'appelant, qui la valide (ou l'annule en cas d'erreur).
    """
    db.refresh(rapport, with_for_update=True)  # deux publications simultanées : une seule passe
    if rapport.statut == StatutRapport.PUBLIE:
        raise PublicationRefusee(
            "Ce rapport est déjà publié : pour corriger, publiez un nouveau rapport.", 409
        )

    texte = texte.strip()
    if len(texte) > TEXTE_MAX:
        raise PublicationRefusee(f"Le commentaire dépasse {TEXTE_MAX} caractères.")

    etape = None
    if etape_id:
        try:
            etape = db.get(Etape, uuid.UUID(etape_id), with_for_update=True)
        except ValueError:
            etape = None
        if etape is None or etape.chantier_id != rapport.chantier_id:
            raise PublicationRefusee("Étape inconnue pour ce chantier.")

    nouvel_avancement = None
    if etape is not None and avancement.strip():
        try:
            nouvel_avancement = int(avancement)
        except ValueError:
            nouvel_avancement = -1
        if not 0 <= nouvel_avancement <= 100:
            raise PublicationRefusee("L'avancement doit être compris entre 0 et 100 %.")

    nombre_photos = db.scalar(
        select(func.count()).select_from(Photo).where(Photo.rapport_id == rapport.id)
    )
    if not texte and not nombre_photos:
        raise PublicationRefusee("Ajoutez au moins une photo ou un commentaire avant de publier.")

    rapport.texte = texte or None
    rapport.etape_id = etape.id if etape else None
    rapport.statut = StatutRapport.PUBLIE
    rapport.publie_le = datetime.now(UTC)
    if nouvel_avancement is not None:
        changer_avancement(db, etape, nouvel_avancement, auteur.id, rapport=rapport)


def supprimer_photo(db: Session, photo: Photo) -> None:
    """Supprime une photo de brouillon : ligne en base, puis fichiers."""
    chemins = [photo.chemin_fichier, photo.chemin_miniature]
    db.delete(photo)
    db.commit()
    for chemin in filter(None, chemins):
        stockage.supprimer(chemin)


def nettoyer_brouillons(db: Session) -> tuple[int, int]:
    """Supprime les brouillons inactifs depuis plus de 7 jours, et leurs fichiers photo.

    Renvoie (rapports supprimés, photos supprimées). Un rapport publié n'est jamais touché.
    """
    limite = datetime.now(UTC) - DUREE_VIE_BROUILLON
    ids = db.scalars(
        select(Rapport.id).where(
            Rapport.statut == StatutRapport.BROUILLON, derniere_activite() < limite
        )
    ).all()
    if not ids:
        return 0, 0
    fichiers = db.execute(
        select(Photo.chemin_fichier, Photo.chemin_miniature).where(Photo.rapport_id.in_(ids))
    ).all()
    # Les photos suivent par ON DELETE CASCADE ; la condition sur le statut est répétée par
    # sécurité.
    db.execute(
        delete(Rapport).where(Rapport.id.in_(ids), Rapport.statut == StatutRapport.BROUILLON)
    )
    db.commit()
    for chemins in fichiers:
        for chemin in filter(None, chemins):
            stockage.supprimer(chemin)
    return len(ids), len(fichiers)
