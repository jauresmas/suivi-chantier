"""Avancement des chantiers : statut des étapes, avancement global pondéré, historique, ordre."""

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dates import FUSEAU_CHANTIER
from app.models import Etape, HistoriqueAvancement, Rapport


class StatutEtape(StrEnum):
    A_VENIR = "a_venir"
    EN_COURS = "en_cours"
    TERMINEE = "terminee"
    EN_RETARD = "en_retard"


LIBELLES_STATUT_ETAPE = {
    StatutEtape.A_VENIR: "À venir",
    StatutEtape.EN_COURS: "En cours",
    StatutEtape.TERMINEE: "Terminée",
    StatutEtape.EN_RETARD: "En retard",
}


def aujourd_hui() -> date:
    """Date du jour sur le chantier (Bénin), qui sert de référence pour les retards."""
    return datetime.now(FUSEAU_CHANTIER).date()


def avancement_global(etapes: Sequence[Etape]) -> int:
    """Moyenne des avancements pondérée par les poids, arrondie à l'entier le plus proche.

    Les poids n'ont pas besoin de totaliser 100 : on divise par leur somme.
    """
    poids_total = sum(etape.poids for etape in etapes)
    if not poids_total:
        return 0
    moyenne = sum(etape.avancement_pct * etape.poids for etape in etapes) / poids_total
    return math.floor(moyenne + 0.5)  # 26,5 → 27, et non 26 comme le ferait round()


@dataclass
class EtatEtape:
    statut: StatutEtape
    jours_de_retard: int = 0

    @property
    def libelle(self) -> str:
        if self.statut == StatutEtape.EN_RETARD:
            return (
                f"En retard de {self.jours_de_retard} jour{'s' if self.jours_de_retard > 1 else ''}"
            )
        return LIBELLES_STATUT_ETAPE[self.statut]


def etat_etape(etape: Etape, jour: date | None = None) -> EtatEtape:
    jour = jour or aujourd_hui()
    if etape.avancement_pct >= 100:
        return EtatEtape(StatutEtape.TERMINEE)
    if etape.date_fin_prevue is not None and etape.date_fin_prevue < jour:
        return EtatEtape(StatutEtape.EN_RETARD, (jour - etape.date_fin_prevue).days)
    if etape.avancement_pct == 0:
        return EtatEtape(StatutEtape.A_VENIR)
    return EtatEtape(StatutEtape.EN_COURS)


def changer_avancement(
    db: Session,
    etape: Etape,
    nouveau_pct: int,
    auteur_id: uuid.UUID | None,
    rapport: Rapport | None = None,
    commentaire: str | None = None,
    jour: date | None = None,
) -> bool:
    """Met à jour l'avancement, l'historique et les dates réelles. Renvoie False si rien ne change.

    Dates réelles : `date_debut` à la première valeur au-dessus de 0 %, `date_fin` à 100 %
    (effacée si l'étape redescend sous 100 %).
    """
    if nouveau_pct == etape.avancement_pct:
        return False
    jour = jour or aujourd_hui()
    db.add(
        HistoriqueAvancement(
            etape_id=etape.id,
            rapport_id=rapport.id if rapport else None,
            auteur_id=auteur_id,
            ancien_pct=etape.avancement_pct,
            nouveau_pct=nouveau_pct,
            commentaire=commentaire or None,
        )
    )
    etape.avancement_pct = nouveau_pct
    if nouveau_pct > 0 and etape.date_debut is None:
        etape.date_debut = jour
    if nouveau_pct == 100:
        etape.date_fin = etape.date_fin or jour
    else:
        etape.date_fin = None
    return True


def etapes_du_chantier(db: Session, chantier_id: uuid.UUID) -> list[Etape]:
    return list(
        db.scalars(select(Etape).where(Etape.chantier_id == chantier_id).order_by(Etape.ordre))
    )


def prochain_ordre(db: Session, chantier_id: uuid.UUID) -> int:
    return (
        db.scalar(select(func.max(Etape.ordre)).where(Etape.chantier_id == chantier_id)) or 0
    ) + 1


def deplacer(db: Session, etape: Etape, sens: int) -> bool:
    """Échange l'étape avec sa voisine (sens -1 : monter, +1 : descendre). Renvoie False en bout de
    liste.

    L'unicité (chantier_id, ordre) est vérifiée à chaque instruction : on passe par un ordre
    temporaire négatif, libre par construction, plutôt que d'échanger directement les deux valeurs.
    """
    etapes = list(
        db.scalars(
            select(Etape)
            .where(Etape.chantier_id == etape.chantier_id)
            .order_by(Etape.ordre)
            .with_for_update()
        )
    )
    position = next(i for i, e in enumerate(etapes) if e.id == etape.id)
    voisine_position = position + sens
    if not 0 <= voisine_position < len(etapes):
        return False
    voisine = etapes[voisine_position]
    ordre_etape, ordre_voisine = etape.ordre, voisine.ordre
    etape.ordre = -ordre_etape
    db.flush()
    voisine.ordre = ordre_etape
    db.flush()
    etape.ordre = ordre_voisine
    db.flush()
    return True


def etape_referencee(db: Session, etape: Etape) -> bool:
    """Vrai si un rapport cite cette étape : on ne peut alors plus la supprimer."""
    return (
        db.scalar(select(func.count()).select_from(Rapport).where(Rapport.etape_id == etape.id)) > 0
    )
