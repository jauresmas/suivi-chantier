"""Tableau de bord d'un chantier : filtres, timeline des rapports, synthèse et photos pour la carte.

Les mêmes filtres s'appliquent à la timeline et à la carte, pour qu'elles montrent toujours
la même chose.
"""

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import get_settings
from app.dates import FUSEAU_CHANTIER, date_longue
from app.geo.verification import (
    Resume,
    badge_photo,
    condition_photo_suspecte,
    formater_distance,
    photo_suspecte,
    resume_chantier,
)
from app.libelles import libelle_etape
from app.models import Chantier, Etape, Photo, Rapport, StatutRapport
from app.services.avancement import StatutEtape, avancement_global, etat_etape

RAPPORTS_PAR_BLOC = 10
PERIODES = {"7j": 7, "30j": 30, "tout": None, "perso": None}


@dataclass(frozen=True)
class Filtres:
    """Filtres de la timeline et de la carte, lus depuis l'URL et réécrits dans les liens."""

    etape: uuid.UUID | None = None
    periode: str = "tout"
    du: date | None = None
    au: date | None = None
    alertes: bool = False  # « Hors zone ou alertes »

    @classmethod
    def depuis_requete(cls, parametres: Mapping[str, str]) -> "Filtres":
        """Lecture tolérante : une valeur invalide est ignorée plutôt que de casser la page."""

        def lire(convertir, cle):
            try:
                return convertir(parametres[cle]) if parametres.get(cle) else None
            except ValueError:
                return None

        periode = parametres.get("periode", "tout")
        return cls(
            etape=lire(uuid.UUID, "etape"),
            periode=periode if periode in PERIODES else "tout",
            du=lire(date.fromisoformat, "du"),
            au=lire(date.fromisoformat, "au"),
            alertes=parametres.get("alertes") == "1",
        )

    @property
    def actifs(self) -> bool:
        return self != Filtres()

    def avec(self, **changements) -> "Filtres":
        return replace(self, **changements)

    def parametres(self) -> str:
        """Chaîne de requête des seuls filtres actifs (vide si aucun)."""
        valeurs = {}
        if self.etape:
            valeurs["etape"] = str(self.etape)
        if self.periode != "tout":
            valeurs["periode"] = self.periode
        if self.periode == "perso":
            valeurs |= {k: v.isoformat() for k, v in (("du", self.du), ("au", self.au)) if v}
        if self.alertes:
            valeurs["alertes"] = "1"
        return urlencode(valeurs)

    def conditions_rapport(self, maintenant: datetime | None = None) -> list:
        """Rapports publiés uniquement, puis étape et période."""
        conditions = [Rapport.statut == StatutRapport.PUBLIE]
        if self.etape:
            conditions.append(Rapport.etape_id == self.etape)
        if jours := PERIODES.get(self.periode):
            conditions.append(
                Rapport.publie_le >= (maintenant or datetime.now(UTC)) - timedelta(days=jours)
            )
        if self.periode == "perso":
            # Bornes en jours du chantier (Bénin), incluses.
            if self.du:
                conditions.append(
                    Rapport.publie_le >= datetime.combine(self.du, time.min, FUSEAU_CHANTIER)
                )
            if self.au:
                conditions.append(
                    Rapport.publie_le
                    < datetime.combine(self.au + timedelta(days=1), time.min, FUSEAU_CHANTIER)
                )
        return conditions

    def garder_photo(self, photo: Photo) -> bool:
        return photo_suspecte(photo) if self.alertes else True


def rapports_timeline(
    db: Session, chantier_id: uuid.UUID, filtres: Filtres, page: int = 1
) -> tuple[list[Rapport], bool]:
    """Un bloc de rapports publiés, du plus récent au plus ancien, et s'il en reste après.

    Auteurs et étapes sont joints, les photos chargées en une requête (selectinload) :
    le nombre de requêtes ne dépend pas du nombre de rapports.
    """
    requete = (
        select(Rapport)
        .where(Rapport.chantier_id == chantier_id, *filtres.conditions_rapport())
        .options(
            joinedload(Rapport.auteur), joinedload(Rapport.etape), selectinload(Rapport.photos)
        )
        .order_by(Rapport.publie_le.desc(), Rapport.id)
        .offset((page - 1) * RAPPORTS_PAR_BLOC)
        .limit(RAPPORTS_PAR_BLOC + 1)
    )
    if filtres.alertes:
        requete = requete.where(
            exists(
                select(Photo.id).where(Photo.rapport_id == Rapport.id, condition_photo_suspecte())
            )
        )
    rapports = db.scalars(requete).all()
    return list(rapports[:RAPPORTS_PAR_BLOC]), len(rapports) > RAPPORTS_PAR_BLOC


@dataclass
class Synthese:
    avancement: int
    etapes_en_retard: int
    dernier_rapport: datetime | None
    jours_sans_nouvelles: int | None
    inactif: bool
    verification: Resume


def synthese(db: Session, chantier: Chantier, etapes: list[Etape]) -> Synthese:
    maintenant = datetime.now(UTC)
    dernier = db.scalar(
        select(func.max(Rapport.publie_le)).where(
            Rapport.chantier_id == chantier.id, Rapport.statut == StatutRapport.PUBLIE
        )
    )
    # Sans aucun rapport, on compte depuis la création du chantier.
    reference = dernier or chantier.cree_le
    jours = (maintenant - reference).days
    return Synthese(
        avancement=avancement_global(etapes),
        etapes_en_retard=sum(etat_etape(e).statut == StatutEtape.EN_RETARD for e in etapes),
        dernier_rapport=dernier,
        jours_sans_nouvelles=jours,
        inactif=jours >= get_settings().jours_inactivite,
        verification=resume_chantier(db, chantier.id),
    )


def photos_geojson(db: Session, chantier_id: uuid.UUID, filtres: Filtres) -> dict:
    """FeatureCollection (RFC 7946, [longitude, latitude]) des photos géolocalisées des rapports
    publiés."""
    lignes = db.execute(
        select(Photo, func.ST_AsGeoJSON(Photo.position, 6).label("geometrie"), Etape)
        .join(Rapport, Photo.rapport_id == Rapport.id)
        .outerjoin(Etape, Etape.id == Rapport.etape_id)
        .where(
            Rapport.chantier_id == chantier_id,
            Photo.position.is_not(None),
            *filtres.conditions_rapport(),
        )
        .where(condition_photo_suspecte() if filtres.alertes else True)
        .order_by(Photo.envoyee_le)
    ).all()
    statut_position = db.scalar(select(Chantier.statut_position).where(Chantier.id == chantier_id))
    features = []
    for photo, geometrie, etape in lignes:
        badge = badge_photo(photo, statut_position)
        quand = photo.prise_le or photo.envoyee_le
        features.append(
            {
                "type": "Feature",
                "geometry": json.loads(geometrie),
                "properties": {
                    "id": str(photo.id),
                    "rapport_id": str(photo.rapport_id),
                    "statut": photo.statut_verification.value,
                    "ton": badge.ton,
                    "badge": badge.texte,
                    "avertissement": badge.avertissement,
                    "alertes": badge.alertes,
                    "mention": badge.mention,
                    "distance": formater_distance(photo.distance_chantier_m),
                    "precision_m": round(photo.precision_m)
                    if photo.precision_m is not None
                    else None,
                    "date": date_longue(quand),
                    "etape": libelle_etape(etape),
                    "miniature": f"/photos/{photo.id}/miniature",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
