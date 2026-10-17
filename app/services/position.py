"""Position de référence d'un chantier : relevés par un proche, validation, verrouillage et
historique.

Seul le propriétaire change la position (routes de app/routers/position.py et formulaire du
chantier). Chaque changement crée une ligne d'historique et relance la vérification de toutes
les photos, dans la transaction de l'appelant.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select, update
from sqlalchemy.orm import Session, aliased, joinedload

from app.auth.securite import empreinte, nouveau_jeton
from app.geo.positions import point
from app.geo.releve import Resultat
from app.geo.verification import verifier_photos
from app.models import (
    Chantier,
    HistoriquePositionChantier,
    MembreChantier,
    RelevePosition,
    RoleMembre,
    SourcePositionChantier,
    StatutPosition,
    StatutReleve,
    TypeReleveur,
    User,
)

DUREE_LIEN = timedelta(hours=72)
DELAI_PHOTOS = timedelta(hours=1)  # après le relevé, pour ajouter les photos du terrain
PHOTOS_MAX = 3
DISTANCE_ALERTE_M = 500
NOM_MAX, LIEN_MAX = 150, 100

LIBELLES_ALERTES_RELEVE = {
    "chef_chantier_connecte": "Ce relevé a été fait depuis le compte du chef de chantier ({nom}), "
    "alors que le lien était destiné à un proche.",
}


class ReleveRefuse(Exception):
    """Le message est destiné à l'utilisateur."""

    def __init__(self, message: str, code_http: int = 409):
        super().__init__(message)
        self.code_http = code_http


def maintenant() -> datetime:
    return datetime.now(UTC)


# --- Lecture
# ---------------------------------------------------------------------------------------


def expirer_releves(db: Session, chantier_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Marque en base les liens en attente dont les 72 h sont passées, et renvoie leurs
    identifiants.

    Appelée avant de créer un lien (l'index unique partiel ne laisse qu'un relevé « en attente » par
    chantier) et par la commande `expirer_releves`, qui prévient alors l'auteur de la demande.
    """
    requete = update(RelevePosition).where(
        RelevePosition.statut == StatutReleve.EN_ATTENTE, RelevePosition.expire_le <= func.now()
    )
    if chantier_id is not None:
        requete = requete.where(RelevePosition.chantier_id == chantier_id)
    resultat = db.execute(
        requete.values(statut=StatutReleve.EXPIRE).returning(RelevePosition.id),
        execution_options={"synchronize_session": "fetch"},
    )
    return list(resultat.scalars())


def releve_du_chantier(
    db: Session, chantier_id: uuid.UUID, statut: StatutReleve
) -> RelevePosition | None:
    """Dernier relevé du chantier dans ce statut ; un lien expiré n'est plus « en attente »."""
    requete = select(RelevePosition).where(
        RelevePosition.chantier_id == chantier_id, RelevePosition.statut == statut
    )
    if statut == StatutReleve.EN_ATTENTE:
        requete = requete.where(RelevePosition.expire_le > func.now())
    return db.scalar(requete.order_by(RelevePosition.cree_le.desc()).limit(1))


def releves_du_chantier(db: Session, chantier_id: uuid.UUID) -> list[RelevePosition]:
    return list(
        db.scalars(
            select(RelevePosition)
            .options(joinedload(RelevePosition.demandeur))
            .where(RelevePosition.chantier_id == chantier_id)
            .order_by(RelevePosition.cree_le.desc())
        )
    )


@dataclass
class LigneHistorique:
    entree: HistoriquePositionChantier
    auteur: str | None
    deplacement_m: float | None
    latitude: float | None
    longitude: float | None


def historique(db: Session, chantier_id: uuid.UUID) -> list[LigneHistorique]:
    nouvelle = cast(HistoriquePositionChantier.nouvelle_position, Geometry)
    lignes = db.execute(
        select(
            HistoriquePositionChantier,
            User.nom_complet,
            func.ST_Distance(
                HistoriquePositionChantier.ancienne_position,
                HistoriquePositionChantier.nouvelle_position,
            ),
            func.ST_Y(nouvelle),
            func.ST_X(nouvelle),
        )
        .outerjoin(User, User.id == HistoriquePositionChantier.modifie_par)
        .options(joinedload(HistoriquePositionChantier.releve))
        .where(HistoriquePositionChantier.chantier_id == chantier_id)
        .order_by(HistoriquePositionChantier.cree_le.desc(), HistoriquePositionChantier.id)
    ).all()
    return [LigneHistorique(*ligne) for ligne in lignes]


def distance_a_la_position_actuelle(db: Session, releve: RelevePosition) -> float | None:
    """Distance en mètres entre le point relevé et la position actuelle du chantier (None s'il n'en
    a pas)."""
    return db.scalar(
        select(func.ST_Distance(Chantier.position, RelevePosition.position_resultat))
        .join(RelevePosition, RelevePosition.chantier_id == Chantier.id)
        .where(RelevePosition.id == releve.id)
    )


def geojson_releve(db: Session, releve: RelevePosition) -> dict:
    """Point relevé, mesures retenues et ancienne position, pour la carte de validation."""
    actuelle = aliased(Chantier)
    resultat, mesures, ancienne = db.execute(
        select(
            func.ST_AsGeoJSON(RelevePosition.position_resultat, 7),
            func.ST_AsGeoJSON(RelevePosition.points_bruts, 7),
            func.ST_AsGeoJSON(actuelle.position, 7),
        )
        .join(actuelle, actuelle.id == RelevePosition.chantier_id)
        .where(RelevePosition.id == releve.id)
    ).one()
    return {"resultat": resultat, "mesures": mesures, "ancienne": ancienne}


def alertes_releve(releve: RelevePosition) -> list[str]:
    nom = releve.realisateur.nom_complet if releve.realisateur else "compte supprimé"
    return [
        LIBELLES_ALERTES_RELEVE[code].format(nom=nom)
        for code in releve.alertes or []
        if code in LIBELLES_ALERTES_RELEVE
    ]


# --- Lien du proche
# --------------------------------------------------------------------------------


def creer_releve(
    db: Session,
    chantier: Chantier,
    demandeur: User,
    nom: str,
    lien: str,
    type_releveur: TypeReleveur,
) -> tuple[RelevePosition, str]:
    """Crée la demande et renvoie (relevé, jeton). Le jeton n'est jamais stocké : seulement son
    empreinte."""
    nom, lien = " ".join(nom.split()), " ".join(lien.split())
    if not nom:
        raise ReleveRefuse("Indiquez le nom de la personne qui fera le relevé.", 400)
    if len(nom) > NOM_MAX or len(lien) > LIEN_MAX:
        raise ReleveRefuse(
            f"Nom : {NOM_MAX} caractères au maximum ; lien de parenté : {LIEN_MAX}.", 400
        )
    expirer_releves(db, chantier.id)
    if releve_du_chantier(db, chantier.id, StatutReleve.EN_ATTENTE):
        raise ReleveRefuse(
            "Un lien de relevé est déjà en attente : annulez-le avant d'en envoyer un nouveau."
        )
    if releve_du_chantier(db, chantier.id, StatutReleve.RECU):
        raise ReleveRefuse(
            "Un relevé attend votre validation : "
            "validez-le ou rejetez-le avant d'en demander un nouveau."
        )
    jeton = nouveau_jeton()
    releve = RelevePosition(
        chantier_id=chantier.id,
        demande_par=demandeur.id,
        nom_releveur=nom,
        lien_releveur=lien or None,
        type_releveur=type_releveur,
        empreinte_jeton=empreinte(jeton),
        cree_le=maintenant(),
        expire_le=maintenant() + DUREE_LIEN,
    )
    db.add(releve)
    db.flush()  # l'index unique partiel refuse un second relevé en attente créé au même instant
    return releve, jeton


def annuler_releve(releve: RelevePosition, proprietaire: User) -> None:
    if releve.statut_effectif != StatutReleve.EN_ATTENTE:
        raise ReleveRefuse("Ce lien n'est plus en attente : il ne peut pas être annulé.")
    releve.statut = StatutReleve.ANNULE
    releve.traite_le = maintenant()
    releve.traite_par = proprietaire.id


def charger_par_jeton(db: Session, jeton: str, verrouiller: bool = False) -> RelevePosition | None:
    requete = (
        select(RelevePosition)
        .options(joinedload(RelevePosition.chantier), joinedload(RelevePosition.demandeur))
        .where(RelevePosition.empreinte_jeton == empreinte(jeton))
    )
    if verrouiller:
        # Deux envois simultanés (double appui, deux téléphones) : un seul l'emporte.
        requete = requete.with_for_update(of=RelevePosition)
    return db.scalar(requete)


def probleme_lien(releve: RelevePosition | None) -> str | None:
    """None si le lien peut servir ; sinon inconnu, annule, expire ou utilise."""
    if releve is None:
        return "inconnu"
    statut = releve.statut_effectif
    if statut == StatutReleve.ANNULE:
        return "annule"
    if statut == StatutReleve.EXPIRE:
        return "expire"
    if statut != StatutReleve.EN_ATTENTE:
        return "utilise"
    return None


def enregistrer_mesures(
    db: Session, releve: RelevePosition, resultat: Resultat, appareil: str, utilisateur: User | None
) -> None:
    """Le relevé est reçu : le lien ne peut plus servir, le propriétaire doit le valider."""
    releve.statut = StatutReleve.RECU
    releve.points_bruts = resultat.multipoint()
    releve.position_resultat = resultat.point()
    releve.precision_estimee_m = round(resultat.precision_estimee_m, 1)
    releve.nb_mesures = len(resultat.retenues)
    releve.duree_releve_s = resultat.duree_s
    releve.signal_faible = resultat.signal_faible
    releve.appareil = appareil[:300] or None
    releve.realise_le = maintenant()
    if utilisateur is not None:
        releve.realise_par = utilisateur.id
        role = db.scalar(
            select(MembreChantier.role).where(
                MembreChantier.chantier_id == releve.chantier_id,
                MembreChantier.user_id == utilisateur.id,
            )
        )
        if role == RoleMembre.CHEF_CHANTIER and releve.type_releveur == TypeReleveur.PROCHE:
            # La personne dont on vérifie le travail ne doit pas fixer le point de référence.
            releve.alertes = [*releve.alertes, "chef_chantier_connecte"]
    chantier = chantier_verrouille(db, releve.chantier_id)
    if chantier.statut_position != StatutPosition.CONFIRMEE:
        # Une position déjà confirmée reste la référence tant que le nouveau relevé n'est pas
        # validé.
        chantier.statut_position = StatutPosition.A_VALIDER


def photos_possibles(releve: RelevePosition) -> bool:
    return (
        releve.realise_le is not None
        and maintenant() < releve.realise_le + DELAI_PHOTOS
        and len(releve.photos) < PHOTOS_MAX
    )


# --- Décision du propriétaire et verrouillage -----------------------------------------------------


def chantier_verrouille(db: Session, chantier_id: uuid.UUID) -> Chantier:
    return db.scalar(
        select(Chantier)
        .where(Chantier.id == chantier_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def statut_hors_releve(chantier: Chantier) -> StatutPosition:
    """Statut du chantier sans le relevé en attente de validation."""
    if chantier.statut_position == StatutPosition.A_VALIDER:
        return (
            StatutPosition.ESTIMEE if chantier.position is not None else StatutPosition.NON_DEFINIE
        )
    return chantier.statut_position


def changer_position(
    db: Session,
    chantier: Chantier,
    nouvelle_position,
    statut: StatutPosition,
    source: SourcePositionChantier,
    auteur: User,
    releve: RelevePosition | None = None,
) -> HistoriquePositionChantier:
    """Applique la nouvelle position, l'inscrit dans l'historique et revérifie toutes les photos.

    Les positions sont recopiées en SQL (sous-requêtes) : un point lu en base reste au format WKB,
    que GeoAlchemy ne sait pas renvoyer tel quel sans Shapely.
    """
    ligne = HistoriquePositionChantier(
        chantier_id=chantier.id,
        ancienne_position=select(Chantier.position)
        .where(Chantier.id == chantier.id)
        .scalar_subquery(),
        nouvelle_position=nouvelle_position,
        ancien_statut=statut_hors_releve(chantier),
        nouveau_statut=statut,
        source=source,
        releve_id=releve.id if releve else None,
        modifie_par=auteur.id,
    )
    db.add(ligne)
    # historique écrit avant le déplacement : sa sous-requête lit encore l'ancienne position
    db.flush()
    chantier.position = nouvelle_position
    chantier.source_position = source
    chantier.statut_position = statut
    chantier.position_confirmee_le = maintenant() if statut == StatutPosition.CONFIRMEE else None
    verifier_photos(db, chantier_id=chantier.id)
    return ligne


def placer_sur_la_carte(
    db: Session, chantier: Chantier, latitude: float, longitude: float, auteur: User
) -> HistoriquePositionChantier:
    """Position choisie par le propriétaire sur la carte : « estimée », même si elle remplace un
    relevé."""
    if chantier.statut_position == StatutPosition.A_VALIDER:
        raise ReleveRefuse(
            "Un relevé attend votre validation : "
            "validez-le ou rejetez-le avant de déplacer la position.",
            400,
        )
    return changer_position(
        db,
        chantier,
        point(latitude, longitude),
        StatutPosition.ESTIMEE,
        SourcePositionChantier.CARTE_PROPRIETAIRE,
        auteur,
    )


def source_du_releve(releve: RelevePosition) -> SourcePositionChantier:
    """Un lien « proche » utilisé depuis le compte du chef compte comme un relevé du chef."""
    if releve.type_releveur == TypeReleveur.CHEF_CHANTIER or "chef_chantier_connecte" in (
        releve.alertes or []
    ):
        return SourcePositionChantier.RELEVE_CHEF
    return SourcePositionChantier.RELEVE_PROCHE


def valider_releve(
    db: Session, releve: RelevePosition, proprietaire: User
) -> HistoriquePositionChantier:
    """Une seule transaction : position, statut, historique, décision et revérification des
    photos."""
    if releve.statut != StatutReleve.RECU:
        raise ReleveRefuse("Ce relevé a déjà été traité.")
    chantier = chantier_verrouille(db, releve.chantier_id)
    position_relevee = (
        select(RelevePosition.position_resultat)
        .where(RelevePosition.id == releve.id)
        .scalar_subquery()
    )
    ligne = changer_position(
        db,
        chantier,
        position_relevee,
        StatutPosition.CONFIRMEE,
        source_du_releve(releve),
        proprietaire,
        releve,
    )
    releve.statut = StatutReleve.VALIDE
    releve.traite_le = maintenant()
    releve.traite_par = proprietaire.id
    return ligne


def rejeter_releve(db: Session, releve: RelevePosition, proprietaire: User, motif: str) -> None:
    """La position du chantier ne change pas ; seul le statut « à valider » est retiré."""
    if releve.statut != StatutReleve.RECU:
        raise ReleveRefuse("Ce relevé a déjà été traité.")
    chantier = chantier_verrouille(db, releve.chantier_id)
    chantier.statut_position = statut_hors_releve(chantier)
    releve.statut = StatutReleve.REJETE
    releve.motif_rejet = motif.strip()[:1000] or None
    releve.traite_le = maintenant()
    releve.traite_par = proprietaire.id
