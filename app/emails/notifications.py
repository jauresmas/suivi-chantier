"""Notifications par e-mail : qui est prévenu de quoi, selon ses préférences.

- rapport publié : propriétaires sauf l'auteur ; tout de suite en mode « immediat », dans le
  résumé en mode « resume_quotidien » ;
- inactivité : propriétaires, une fois par période d'inactivité ;
- invitation acceptée : auteur de l'invitation, tout de suite ;
- relevé reçu : propriétaires, tout de suite (jamais dans un résumé) ;
- relevé expiré : auteur de la demande, tout de suite ;
- position modifiée : tous les propriétaires, toujours (sécurité, non désactivable) ;
- réinitialisation du mot de passe : l'utilisateur, toujours (sécurité).

En mode « aucun », seuls les deux e-mails de sécurité sont envoyés.

La notification de position modifiée protège contre un déplacement discret du point de
référence : elle n'a ni lien de désinscription ni réglage.
"""

import functools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from geoalchemy2 import Geometry
from sqlalchemy import cast, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.config import get_settings
from app.dates import date_courte
from app.emails.envoi import programmer
from app.emails.expediteurs import Message
from app.emails.preferences import Destinataire, mode_de, proprietaires, url_desinscription
from app.emails.rendu import rendre, url
from app.geo.fiabilite import fiabilite
from app.geo.releve import qualite_signal
from app.geo.verification import ALERTES_INFORMATIVES, LIBELLES_ALERTES, formater_distance
from app.libelles import libelle_etape
from app.models import (
    Chantier,
    EmailEnvoye,
    HistoriqueAvancement,
    HistoriquePositionChantier,
    Invitation,
    MembreChantier,
    ModeNotification,
    Photo,
    PreferenceNotification,
    Rapport,
    RelevePosition,
    RoleMembre,
    StatutChantier,
    StatutPosition,
    StatutRapport,
    StatutVerification,
    TypeEmail,
    User,
)
from app.services.position import (
    DISTANCE_ALERTE_M,
    alertes_releve,
    distance_a_la_position_actuelle,
    expirer_releves,
)

DUREE_RESUME = timedelta(hours=24)
APERCU_TEXTE = 400  # caractères du commentaire repris dans un résumé

journal = logging.getLogger("app.emails")


def sans_bloquer(notifier):
    """Appelée après la validation de l'action : une erreur de notification est journalisée,
    mais ne transforme jamais une publication réussie en page d'erreur."""

    @functools.wraps(notifier)
    def enveloppe(db: Session, *arguments, **options):
        try:
            notifier(db, *arguments, **options)
        except Exception:
            db.rollback()
            journal.exception("Notification « %s » impossible", notifier.__name__)

    return enveloppe


def maintenant() -> datetime:
    return datetime.now(UTC)


def message(
    type_: TypeEmail,
    user: User,
    chantier: Chantier | None,
    gabarit: str,
    sujet: str,
    securite: bool = False,
    sensible: bool = False,
    **contexte,
) -> Message:
    """Rend le gabarit pour un destinataire. Un e-mail de sécurité n'a pas de lien de
    désinscription."""
    desinscription = (
        None if securite or chantier is None else url_desinscription(user.id, chantier.id)
    )
    html, texte = rendre(
        gabarit,
        sujet=sujet,
        destinataire=user,
        chantier=chantier,
        securite=securite,
        url_desinscription=desinscription,
        url_gerer=url("/compte/notifications"),
        **contexte,
    )
    return Message(
        type=type_,
        destinataire=user.email,
        sujet=sujet,
        texte=texte,
        html=html,
        user_id=user.id,
        chantier_id=chantier.id if chantier else None,
        url_desinscription=desinscription,
        sensible=sensible,
    )


def a_prevenir(destinataires: list[Destinataire], exclure: uuid.UUID | None = None) -> list[User]:
    """Destinataires qui n'ont pas choisi « aucun e-mail » pour ce chantier."""
    return [
        d.user for d in destinataires if d.mode != ModeNotification.AUCUN and d.user.id != exclure
    ]


# --- Rapports ------------------------------------------------------------------------------------


@dataclass
class PhotosDuRapport:
    total: int = 0
    verifiees: int = 0
    hors_zone: int = 0
    distance_max_m: float | None = None
    a_verifier: int = 0  # photos portant un signal de confiance autre que purement informatif
    signaux: list[str] = field(default_factory=list)

    @property
    def non_verifiables(self) -> int:
        return self.total - self.verifiees - self.hors_zone

    def resume(self) -> str:
        """« 3 photos : 2 sur le chantier, 1 hors zone »."""
        if not self.total:
            return "Aucune photo"
        parties = [
            (self.verifiees, "sur le chantier"),
            (self.hors_zone, "hors zone"),
            (self.non_verifiables, "non vérifiable" + ("s" if self.non_verifiables > 1 else "")),
        ]
        detail = ", ".join(f"{n} {libelle}" for n, libelle in parties if n)
        return f"{self.total} photo{'s' if self.total > 1 else ''} : {detail}"

    def avertissements(self) -> list[str]:
        """Affichés en tête de l'e-mail : photo prise hors zone, signal de confiance."""
        lignes = []
        if self.hors_zone:
            distance = formater_distance(self.distance_max_m)
            lignes.append(
                f"1 photo prise hors zone, à {distance} du chantier"
                if self.hors_zone == 1
                else f"{self.hors_zone} photos prises hors zone, jusqu'à {distance} du chantier"
            )
        if self.a_verifier:
            pluriel = "s" if self.a_verifier > 1 else ""
            lignes.append(
                f"{self.a_verifier} photo{pluriel} à vérifier : " + " ".join(self.signaux)
            )
        return lignes


def photos_des_rapports(
    db: Session, rapport_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PhotosDuRapport]:
    resumes = {rapport_id: PhotosDuRapport() for rapport_id in rapport_ids}
    lignes = db.execute(
        select(
            Photo.rapport_id, Photo.statut_verification, Photo.distance_chantier_m, Photo.alertes
        ).where(Photo.rapport_id.in_(rapport_ids))
    ).all()
    for rapport_id, statut, distance, alertes in lignes:
        r = resumes[rapport_id]
        r.total += 1
        if statut == StatutVerification.VERIFIEE:
            r.verifiees += 1
        elif statut == StatutVerification.HORS_ZONE:
            r.hors_zone += 1
            r.distance_max_m = max(r.distance_max_m or 0, distance or 0)
        significatives = [code for code in alertes or [] if code not in ALERTES_INFORMATIVES]
        if significatives:
            r.a_verifier += 1
            for code in significatives:
                if LIBELLES_ALERTES[code] not in r.signaux:
                    r.signaux.append(LIBELLES_ALERTES[code])
    return resumes


@dataclass
class RapportResume:
    """Ce que l'e-mail dit d'un rapport : jamais de photo, seulement leur nombre et leur
    vérification."""

    rapport: Rapport
    auteur: str
    etape: str
    avancement: int | None
    texte: str
    photos: PhotosDuRapport
    lien: str

    @property
    def titre_etape(self) -> str:
        if not self.etape:
            return ""
        return f"{self.etape} {self.avancement} %" if self.avancement is not None else self.etape


def resumer_rapports(
    db: Session, rapports: list[Rapport], apercu: int | None = None
) -> list[RapportResume]:
    ids = [r.id for r in rapports]
    photos = photos_des_rapports(db, ids)
    avancements = dict(
        db.execute(
            select(HistoriqueAvancement.rapport_id, HistoriqueAvancement.nouveau_pct).where(
                HistoriqueAvancement.rapport_id.in_(ids)
            )
        ).all()
    )
    resumes = []
    for r in rapports:
        texte = r.texte or ""
        if apercu and len(texte) > apercu:
            texte = texte[:apercu].rsplit(" ", 1)[0] + " …"
        resumes.append(
            RapportResume(
                rapport=r,
                auteur=r.auteur.nom_complet if r.auteur else "Compte supprimé",
                etape=libelle_etape(r.etape),
                avancement=avancements.get(r.id, r.etape.avancement_pct if r.etape else None),
                texte=texte,
                photos=photos[r.id],
                lien=url(f"/rapports/{r.id}"),
            )
        )
    return resumes


def note_position(chantier: Chantier) -> str | None:
    """Rappel affiché sous la vérification des photos tant que la référence n'est pas confirmée."""
    if chantier.statut_position == StatutPosition.NON_DEFINIE:
        return (
            "La position du chantier n'est pas encore définie : "
            "les photos ne peuvent pas être vérifiées."
        )
    if chantier.statut_position != StatutPosition.CONFIRMEE:
        return (
            "La position du chantier n'est pas encore confirmée : "
            "la vérification des photos reste indicative."
        )
    return None


def charger_rapports(db: Session, conditions) -> list[Rapport]:
    return list(
        db.scalars(
            select(Rapport)
            .options(
                joinedload(Rapport.etape), joinedload(Rapport.auteur), joinedload(Rapport.chantier)
            )
            .where(Rapport.statut == StatutRapport.PUBLIE, *conditions)
            .order_by(Rapport.publie_le)
        ).unique()
    )


@sans_bloquer
def notifier_rapport_publie(
    db: Session, taches: BackgroundTasks | None, rapport_id: uuid.UUID
) -> None:
    """E-mail immédiat aux propriétaires en mode « immediat » (jamais à l'auteur du rapport)."""
    (rapport,) = charger_rapports(db, [Rapport.id == rapport_id])
    chantier = rapport.chantier
    immediats = [
        d.user
        for d in proprietaires(db, chantier.id)
        if d.mode == ModeNotification.IMMEDIAT and d.user.id != rapport.auteur_id
    ]
    if not immediats:
        return
    (resume,) = resumer_rapports(db, [rapport])
    sujet = f"{chantier.nom} — nouveau rapport" + (
        f" : {resume.titre_etape}" if resume.titre_etape else ""
    )
    programmer(
        db,
        taches,
        [
            message(
                TypeEmail.RAPPORT_PUBLIE,
                user,
                chantier,
                "rapport_publie",
                sujet,
                r=resume,
                note_position=note_position(chantier),
            )
            for user in immediats
        ],
    )


def envoyer_resumes_quotidiens(db: Session, instant: datetime | None = None) -> int:
    """Un e-mail par propriétaire et par chantier en mode « resume_quotidien », s'il y a des
    rapports
    publiés depuis le dernier résumé (24 h au plus). Relancer la commande n'envoie rien de plus."""
    instant = instant or maintenant()
    abonnes = db.execute(
        select(User, Chantier)
        .join(PreferenceNotification, PreferenceNotification.user_id == User.id)
        .join(Chantier, Chantier.id == PreferenceNotification.chantier_id)
        .join(
            MembreChantier,
            (MembreChantier.user_id == User.id) & (MembreChantier.chantier_id == Chantier.id),
        )
        .where(
            PreferenceNotification.mode == ModeNotification.RESUME_QUOTIDIEN,
            MembreChantier.role == RoleMembre.PROPRIETAIRE,
        )
    ).all()
    messages = []
    for user, chantier in abonnes:
        dernier_resume = db.scalar(
            select(func.max(EmailEnvoye.cree_le)).where(
                EmailEnvoye.type == TypeEmail.RESUME_QUOTIDIEN,
                EmailEnvoye.user_id == user.id,
                EmailEnvoye.chantier_id == chantier.id,
            )
        )
        depuis = max(filter(None, [instant - DUREE_RESUME, dernier_resume]))
        rapports = charger_rapports(
            db,
            [
                Rapport.chantier_id == chantier.id,
                Rapport.publie_le > depuis,
                Rapport.publie_le <= instant,
                or_(Rapport.auteur_id.is_(None), Rapport.auteur_id != user.id),
            ],
        )
        if not rapports:
            continue
        resumes = resumer_rapports(db, rapports, apercu=APERCU_TEXTE)
        nombre = len(resumes)
        sujet = f"{chantier.nom} — résumé du jour : {nombre} rapport{'s' if nombre > 1 else ''}"
        avertissements = [
            f"Rapport du {date_courte(r.rapport.publie_le)} : {ligne}"
            for r in resumes
            for ligne in r.photos.avertissements()
        ]
        resume = message(
            TypeEmail.RESUME_QUOTIDIEN,
            user,
            chantier,
            "resume_quotidien",
            sujet,
            rapports=resumes,
            avertissements=avertissements,
            note_position=note_position(chantier),
        )
        resume.cree_le = (
            instant  # le prochain résumé couvrira les rapports publiés après cet instant
        )
        messages.append(resume)
    programmer(db, None, messages)
    return len(messages)


# --- Inactivité ----------------------------------------------------------------------------------


def verifier_inactivite(db: Session, instant: datetime | None = None) -> int:
    """Chantiers en cours sans rapport publié depuis JOURS_INACTIVITE jours : les propriétaires sont
    prévenus une seule fois par période d'inactivité (jusqu'au prochain rapport publié)."""
    instant = instant or maintenant()
    jours_seuil = get_settings().jours_inactivite
    dernier_rapport = (
        select(func.max(Rapport.publie_le))
        .where(Rapport.chantier_id == Chantier.id, Rapport.statut == StatutRapport.PUBLIE)
        .scalar_subquery()
    )
    derniere_activite = func.coalesce(dernier_rapport, Chantier.cree_le)
    deja_prevenu = exists().where(
        EmailEnvoye.type == TypeEmail.INACTIVITE,
        EmailEnvoye.chantier_id == Chantier.id,
        EmailEnvoye.cree_le > derniere_activite,
    )
    chantiers = db.execute(
        select(Chantier, dernier_rapport, derniere_activite).where(
            Chantier.statut == StatutChantier.EN_COURS,
            derniere_activite < instant - timedelta(days=jours_seuil),
            ~deja_prevenu,
        )
    ).all()
    messages = []
    for chantier, dernier, activite in chantiers:
        chefs = db.scalars(
            select(User)
            .join(MembreChantier, MembreChantier.user_id == User.id)
            .where(
                MembreChantier.chantier_id == chantier.id,
                MembreChantier.role == RoleMembre.CHEF_CHANTIER,
            )
            .order_by(User.nom_complet)
        ).all()
        jours = (instant - activite).days
        sujet = f"{chantier.nom} — aucune nouvelle depuis {jours} jours"
        messages += [
            message(
                TypeEmail.INACTIVITE,
                user,
                chantier,
                "inactivite",
                sujet,
                jours=jours,
                dernier_rapport=dernier,
                chefs=chefs,
                lien=url(f"/chantiers/{chantier.id}"),
            )
            for user in a_prevenir(proprietaires(db, chantier.id))
        ]
    for alerte in messages:
        alerte.cree_le = (
            instant  # période d'inactivité close dès qu'un rapport est publié après cet instant
        )
    programmer(db, None, messages)
    return len(messages)


# --- Invitations et relevés ----------------------------------------------------------------------


@sans_bloquer
def notifier_invitation_acceptee(
    db: Session, taches: BackgroundTasks | None, invitation: Invitation, membre: User
) -> None:
    auteur = db.get(User, invitation.invite_par) if invitation.invite_par else None
    if auteur is None or mode_de(db, auteur.id, invitation.chantier_id) == ModeNotification.AUCUN:
        return
    chantier = db.get(Chantier, invitation.chantier_id)
    sujet = f"{chantier.nom} — {membre.nom_complet} a rejoint le chantier"
    programmer(
        db,
        taches,
        [
            message(
                TypeEmail.INVITATION_ACCEPTEE,
                auteur,
                chantier,
                "invitation_acceptee",
                sujet,
                membre=membre,
                role=invitation.role,
                lien=url(f"/chantiers/{chantier.id}"),
            )
        ],
    )


@sans_bloquer
def notifier_releve_recu(
    db: Session, taches: BackgroundTasks | None, releve: RelevePosition
) -> None:
    """Toujours immédiat, même en mode « resume_quotidien » : le relevé attend une décision."""
    chantier = releve.chantier
    distance = distance_a_la_position_actuelle(db, releve)
    sujet = f"{chantier.nom} — {releve.nom_releveur} a relevé la position de votre terrain"
    contexte = {
        "releve": releve,
        "distance_m": distance,
        "alerte_distance": distance is not None and distance > DISTANCE_ALERTE_M,
        "alertes": alertes_releve(releve),
        "qualite": qualite_signal(releve.precision_estimee_m, releve.signal_faible),
        "lien": url(f"/chantiers/{chantier.id}/position/releves/{releve.id}"),
    }
    programmer(
        db,
        taches,
        [
            message(TypeEmail.RELEVE_RECU, user, chantier, "releve_recu", sujet, **contexte)
            for user in a_prevenir(proprietaires(db, chantier.id))
        ],
    )


def expirer_et_notifier_releves(db: Session) -> int:
    """Passe en « expire » les liens de relevé dépassés et prévient l'auteur de chaque demande."""
    ids = expirer_releves(db)
    db.commit()
    if not ids:
        return 0
    releves = db.scalars(
        select(RelevePosition)
        .options(joinedload(RelevePosition.chantier), joinedload(RelevePosition.demandeur))
        .where(RelevePosition.id.in_(ids))
    ).all()
    messages = []
    for releve in releves:
        auteur, chantier = releve.demandeur, releve.chantier
        if auteur is None:
            continue
        destinataires = {
            d.user.id for d in proprietaires(db, chantier.id) if d.mode != ModeNotification.AUCUN
        }
        if (
            auteur.id not in destinataires
        ):  # plus propriétaire, ou « aucun e-mail » pour ce chantier
            continue
        sujet = f"{chantier.nom} — le lien de relevé envoyé à {releve.nom_releveur} a expiré"
        messages.append(
            message(
                TypeEmail.RELEVE_EXPIRE,
                auteur,
                chantier,
                "releve_expire",
                sujet,
                releve=releve,
                lien=url(f"/chantiers/{chantier.id}/position/demander"),
            )
        )
    programmer(db, None, messages)
    return len(ids)


# --- Position du chantier (sécurité)
# ----------------------------------------------------------------


@dataclass
class ChangementPosition:
    entree: HistoriquePositionChantier
    auteur: str
    ancienne: tuple[float, float] | None  # (latitude, longitude)
    nouvelle: tuple[float, float] | None
    deplacement_m: float | None
    libelle: str  # fiabilité de la nouvelle position
    releveur: str | None


def changement_position(db: Session, historique_id: uuid.UUID) -> ChangementPosition:
    h = aliased(HistoriquePositionChantier)
    ancienne, nouvelle = cast(h.ancienne_position, Geometry), cast(h.nouvelle_position, Geometry)
    entree, auteur, lat_a, lon_a, lat_n, lon_n, deplacement = db.execute(
        select(
            h,
            User.nom_complet,
            func.ST_Y(ancienne),
            func.ST_X(ancienne),
            func.ST_Y(nouvelle),
            func.ST_X(nouvelle),
            func.ST_Distance(h.ancienne_position, h.nouvelle_position),
        )
        .outerjoin(User, User.id == h.modifie_par)
        .options(joinedload(h.releve))
        .where(h.id == historique_id)
    ).one()
    return ChangementPosition(
        entree=entree,
        auteur=auteur or "Compte supprimé",
        ancienne=(lat_a, lon_a) if lat_a is not None else None,
        nouvelle=(lat_n, lon_n) if lat_n is not None else None,
        deplacement_m=deplacement,
        libelle=fiabilite(entree.nouveau_statut, entree.source).libelle,
        releveur=entree.releve.nom_releveur if entree.releve else None,
    )


@sans_bloquer
def notifier_position_modifiee(
    db: Session, taches: BackgroundTasks | None, historique_id: uuid.UUID
) -> None:
    """À tous les propriétaires, quelles que soient leurs préférences, auteur du changement
    compris."""
    changement = changement_position(db, historique_id)
    chantier = db.get(Chantier, changement.entree.chantier_id)
    sujet = f"{chantier.nom} — la position du chantier a été modifiée"
    programmer(
        db,
        taches,
        [
            message(
                TypeEmail.POSITION_MODIFIEE,
                d.user,
                chantier,
                "position_modifiee",
                sujet,
                securite=True,
                changement=changement,
                lien=url(f"/chantiers/{chantier.id}/position"),
            )
            for d in proprietaires(db, chantier.id)
        ],
    )


# --- Mot de passe ---------------------------------------------------------------------------------


def message_reinitialisation(user: User, lien: str) -> Message:
    """Sécurité : toujours envoyé ; contenu non conservé en base (il contient le jeton en clair)."""
    return message(
        TypeEmail.MOT_DE_PASSE,
        user,
        None,
        "mot_de_passe",
        "Suivi Chantier — réinitialisation de votre mot de passe",
        securite=True,
        sensible=True,
        lien=lien,
    )
