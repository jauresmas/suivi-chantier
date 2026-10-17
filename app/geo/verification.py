"""Vérification des photos : distance au chantier, statut et signaux de confiance.

Tout est calculé en SQL, en une seule requête UPDATE ... FROM (pas de boucle Python), avec
ST_Distance sur des geography : distances en mètres.

Les signaux rendent visibles des situations suspectes, sans rien prouver : une position GPS
peut être falsifiée. L'interface parle donc de photo « prise sur le chantier », jamais
« garantie » ni « certifiée ».

Tant que la position du chantier n'est pas définie, aucune photo n'est vérifiable. Tant qu'elle
n'est pas confirmée (estimée sur la carte, relevé à valider), le calcul est fait mais le badge
porte la mention « position du chantier non confirmée ».
"""

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Photo, Rapport, StatutPosition, StatutRapport, StatutVerification

SEUIL_PRECISION_FAIBLE_M = 100
VITESSE_MAX_KMH = 150

LIBELLES_STATUT = {
    StatutVerification.VERIFIEE: "Photo prise sur le chantier",
    StatutVerification.HORS_ZONE: "Photo prise hors zone",
    StatutVerification.NON_VERIFIABLE: "Position non vérifiable",
}

# Signaux de confiance, dans l'ordre d'affichage.
LIBELLES_ALERTES = {
    "doublon": "Cette photo a déjà été envoyée : même fichier d'origine qu'une photo précédente.",
    "deplacement_impossible": (
        "Trop loin de la photo précédente du même auteur pour le temps écoulé (plus de 150 km/h)."
    ),
    "photo_ancienne": "Photo prise plus de 24 h avant son envoi.",
    "position_ancienne": "Position GPS relevée plus de 5 minutes avant la photo.",
    "precision_faible": "Position peu précise (entre 100 et 200 m).",
    "position_exif": "Position lue dans le fichier de la photo, et non relevée par le navigateur.",
}
ALERTES_INFORMATIVES = {"position_exif"}  # information, pas suspicion
MENTION_NON_CONFIRMEE = "Position du chantier non confirmée"
STATUTS_NON_CONFIRMES = {StatutPosition.ESTIMEE, StatutPosition.A_VALIDER}

_REQUETE = """
WITH calcul AS (
    SELECT
        p.id,
        c.rayon_verification_m AS rayon,
        -- Parcelle si son emprise est dessinée (0 m à l'intérieur), sinon point du chantier.
        -- Position du chantier à définir : pas de référence, donc pas de distance.
        CASE WHEN c.statut_position <> 'non_definie'
             THEN ST_Distance(p.position, COALESCE(c.emprise, c.position)) END AS distance,
        COALESCE(p.position_obtenue_le, p.prise_le, p.envoyee_le) AS moment,
        precedente.position AS position_precedente,
        precedente.moment AS moment_precedent,
        EXISTS (
            SELECT 1 FROM photos AS autre
            WHERE autre.empreinte_originale = p.empreinte_originale
              AND (autre.envoyee_le, autre.id) < (p.envoyee_le, p.id)
        ) AS doublon
    FROM photos AS p
    JOIN rapports AS r ON r.id = p.rapport_id
    JOIN chantiers AS c ON c.id = r.chantier_id
    -- Photo précédente du même auteur, tous chantiers confondus : sert à estimer sa vitesse.
    LEFT JOIN LATERAL (
        SELECT prec.position,
               COALESCE(prec.position_obtenue_le, prec.prise_le, prec.envoyee_le) AS moment
        FROM photos AS prec
        JOIN rapports AS rp ON rp.id = prec.rapport_id
        WHERE rp.auteur_id = r.auteur_id
          AND prec.id <> p.id
          AND prec.position IS NOT NULL
          AND COALESCE(prec.position_obtenue_le, prec.prise_le, prec.envoyee_le)
              < COALESCE(p.position_obtenue_le, p.prise_le, p.envoyee_le)
        ORDER BY moment DESC
        LIMIT 1
    ) AS precedente ON p.position IS NOT NULL
    WHERE {filtre}
)
UPDATE photos AS p
SET
    distance_chantier_m = round(calcul.distance),
    statut_verification = (CASE
        WHEN p.position IS NULL OR calcul.distance IS NULL
             OR p.precision_m > :precision_inexploitable
             THEN 'non_verifiable'
        WHEN calcul.distance <= calcul.rayon + LEAST(COALESCE(p.precision_m, 0), :tolerance_max)
             THEN 'verifiee'
        ELSE 'hors_zone'
    END)::statut_verification,
    alertes = array_remove(ARRAY[
        CASE WHEN calcul.doublon THEN 'doublon' END,
        CASE WHEN calcul.moment - calcul.moment_precedent > interval '1 minute'
              AND ST_Distance(p.position, calcul.position_precedente)
                  / EXTRACT(EPOCH FROM calcul.moment - calcul.moment_precedent) > :vitesse_max_m_s
             THEN 'deplacement_impossible' END,
        CASE WHEN p.prise_le < p.envoyee_le - interval '24 hours' THEN 'photo_ancienne' END,
        CASE WHEN p.position_obtenue_le < COALESCE(p.prise_le, p.envoyee_le) - interval '5 minutes'
             THEN 'position_ancienne' END,
        CASE WHEN p.precision_m > :seuil_precision_faible
              AND p.precision_m <= :precision_inexploitable
             THEN 'precision_faible' END,
        CASE WHEN p.source_position = 'exif' THEN 'position_exif' END
    ]::text[], NULL),
    verifiee_le = now()
FROM calcul
WHERE p.id = calcul.id
"""


def verifier_photos(
    db: Session, *, chantier_id: uuid.UUID | None = None, photo_id: uuid.UUID | None = None
) -> int:
    """Recalcule distance, statut et alertes : d'une photo, des photos d'un chantier, ou de toutes.

    Renvoie le nombre de photos traitées. La transaction n'est pas validée : c'est à l'appelant de
    le faire.
    """
    if photo_id is not None:
        filtre, parametres = "p.id = :photo_id", {"photo_id": photo_id}
    elif chantier_id is not None:
        filtre, parametres = "r.chantier_id = :chantier_id", {"chantier_id": chantier_id}
    else:
        filtre, parametres = "TRUE", {}
    settings = get_settings()
    db.flush()  # la photo qu'on vient d'ajouter doit être en base avant le calcul
    resultat = db.execute(
        text(_REQUETE.format(filtre=filtre)),
        {
            **parametres,
            "precision_inexploitable": settings.precision_inexploitable_m,
            "tolerance_max": settings.tolerance_precision_max_m,
            "seuil_precision_faible": SEUIL_PRECISION_FAIBLE_M,
            "vitesse_max_m_s": VITESSE_MAX_KMH / 3.6,
        },
    )
    db.expire_all()  # les objets déjà chargés reliront les valeurs calculées
    return resultat.rowcount


# --- Affichage ------------------------------------------------------------------------------------


def formater_distance(metres: float | None) -> str:
    """« 35 m » sous 1 km, « 1,8 km » au-delà, « 4 653 km » à partir de 100 km (format français)."""
    if metres is None:
        return ""
    if metres < 999.5:
        return f"{round(metres)} m"
    kilometres = metres / 1000
    texte = f"{kilometres:,.1f}" if kilometres < 99.95 else f"{kilometres:,.0f}"
    # Séparateurs à la française : espace insécable pour les milliers, virgule décimale.
    return texte.replace(",", " ").replace(".", ",") + " km"


@dataclass
class Badge:
    texte: str
    ton: str  # couleur de la charte : succes, danger ou neutre
    icone: str  # icône Lucide : la couleur ne porte jamais seule l'information
    titre: str
    avertissement: bool  # au moins un signal autre que purement informatif
    alertes: list[str]  # libellés, dans l'ordre d'affichage
    mention: str | None = None  # « Position du chantier non confirmée » : résultat indicatif


def badge_verification(
    statut: StatutVerification,
    distance_m: float | None,
    alertes: list[str] | None,
    statut_position: StatutPosition | None = None,
) -> Badge:
    codes = alertes or []
    if statut == StatutVerification.VERIFIEE:
        texte, ton, icone = "Sur le chantier", "succes", "circle-check"
    elif statut == StatutVerification.HORS_ZONE:
        texte = (
            f"Hors zone — à {formater_distance(distance_m)}"
            if distance_m is not None
            else "Hors zone"
        )
        ton, icone = "danger", "circle-x"
    else:
        texte, ton, icone = "Non vérifiable", "neutre", "circle-help"
    titre = LIBELLES_STATUT[statut]
    mention = None
    if statut != StatutVerification.NON_VERIFIABLE and statut_position in STATUTS_NON_CONFIRMES:
        mention = MENTION_NON_CONFIRMEE
        titre += f" — {mention.lower()}"
    elif statut_position == StatutPosition.NON_DEFINIE:
        titre = "Position du chantier à définir : vérification impossible"
    return Badge(
        texte=texte,
        ton=ton,
        icone=icone,
        titre=titre,
        avertissement=any(code not in ALERTES_INFORMATIVES for code in codes),
        alertes=[libelle for code, libelle in LIBELLES_ALERTES.items() if code in codes],
        mention=mention,
    )


def badge_photo(photo: Photo, statut_position: StatutPosition | None = None) -> Badge:
    """statut_position : celui du chantier de la photo, pour la mention « non confirmée »."""
    return badge_verification(
        photo.statut_verification, photo.distance_chantier_m, photo.alertes, statut_position
    )


@dataclass
class Resume:
    verifiees: int
    hors_zone: int
    total: int  # y compris les photos non vérifiables

    @property
    def verifiables(self) -> int:
        return self.verifiees + self.hors_zone

    @property
    def pourcentage(self) -> int | None:
        """Part des photos vérifiables prises sur le chantier.

        Règle : les photos « non vérifiables » (sans position, ou trop imprécises) sont exclues
        du dénominateur. On ne peut rien conclure sur elles : les compter comme des échecs
        pénaliserait un chef de chantier dont le téléphone capte mal. None s'il n'y en a aucune.
        """
        return (
            math.floor(100 * self.verifiees / self.verifiables + 0.5) if self.verifiables else None
        )


def resume_chantier(db: Session, chantier_id: uuid.UUID, jours: int = 30) -> Resume:
    """Photos des rapports publiés des `jours` derniers jours, par statut de vérification."""
    depuis = datetime.now(UTC) - timedelta(days=jours)
    verifiees, hors_zone, total = db.execute(
        select(
            func.count(case((Photo.statut_verification == StatutVerification.VERIFIEE, 1))),
            func.count(case((Photo.statut_verification == StatutVerification.HORS_ZONE, 1))),
            func.count(Photo.id),
        )
        .join(Rapport, Photo.rapport_id == Rapport.id)
        .where(
            Rapport.chantier_id == chantier_id,
            Rapport.statut == StatutRapport.PUBLIE,
            Photo.envoyee_le >= depuis,
        )
    ).one()
    return Resume(verifiees, hors_zone, total)


def photo_suspecte(photo: Photo) -> bool:
    """Hors zone, ou porteuse d'un signal de confiance autre que purement informatif."""
    return photo.statut_verification == StatutVerification.HORS_ZONE or any(
        code not in ALERTES_INFORMATIVES for code in photo.alertes or []
    )


def condition_photo_suspecte():
    """Équivalent SQL de photo_suspecte(), pour filtrer la carte et la timeline."""
    alertes_significatives = Photo.alertes
    for code in ALERTES_INFORMATIVES:
        alertes_significatives = func.array_remove(alertes_significatives, code)
    return or_(
        Photo.statut_verification == StatutVerification.HORS_ZONE,
        func.cardinality(alertes_significatives) > 0,
    )
