"""Envoi des e-mails : journal en base (emails_envoyes), envoi en tâche de fond, nouvelles
tentatives.

Une requête utilisateur ne doit jamais attendre ni échouer à cause du serveur SMTP : la page
répond d'abord, puis la tâche de fond envoie l'e-mail et note le résultat (envoyé ou échec).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.emails import expediteurs
from app.emails.expediteurs import Message
from app.models import EmailEnvoye, StatutEmail

journal = logging.getLogger("app.emails")

TENTATIVES_MAX = 3
# Un e-mail resté « en attente » plus longtemps : le processus s'est arrêté avant l'envoi.
EN_ATTENTE_ABANDONNE = timedelta(minutes=15)


def ouvrir_session() -> Session:
    """Session propre à la tâche de fond : celle de la requête est fermée quand elle s'exécute."""
    return SessionLocal()


def programmer(
    db: Session, taches: BackgroundTasks | None, messages: list[Message]
) -> list[uuid.UUID]:
    """Inscrit les e-mails au journal, valide, puis les envoie en tâche de fond.

    Sans `taches` (commandes en ligne de commande), l'envoi est fait tout de suite.
    """
    if not messages:
        return []
    lignes = [
        EmailEnvoye(
            destinataire=m.destinataire,
            type=m.type,
            sujet=m.sujet[:300],
            corps_texte=None if m.sensible else m.texte,
            corps_html=None if m.sensible else m.html,
            url_desinscription=m.url_desinscription,
            user_id=m.user_id,
            chantier_id=m.chantier_id,
            **({"cree_le": m.cree_le} if m.cree_le else {}),
        )
        for m in messages
    ]
    db.add_all(lignes)
    db.commit()
    for ligne, message in zip(lignes, messages, strict=True):
        if taches is not None:
            taches.add_task(envoyer, ligne.id, message)
        else:
            envoyer(ligne.id, message)
    return [ligne.id for ligne in lignes]


def envoyer(email_id: uuid.UUID, message: Message) -> bool:
    """Envoie un e-mail et note le résultat. Ne lève jamais d'exception (tâche de fond)."""
    erreur = None
    try:
        expediteurs.obtenir_expediteur().envoyer(message)
    except Exception as exc:  # serveur injoignable, identifiants refusés, adresse rejetée…
        erreur = f"{type(exc).__name__} : {exc}"[:1000]
        journal.warning(
            "Échec de l'envoi de l'e-mail %s à %s : %s", email_id, message.destinataire, erreur
        )
    try:
        with ouvrir_session() as db:
            email = db.get(EmailEnvoye, email_id)
            email.tentatives += 1
            if erreur is None:
                email.statut, email.envoye_le, email.derniere_erreur = (
                    StatutEmail.ENVOYE,
                    datetime.now(UTC),
                    None,
                )
            else:
                email.statut, email.derniere_erreur = StatutEmail.ECHEC, erreur
            db.commit()
    except Exception:
        journal.exception("Impossible de noter le résultat de l'e-mail %s", email_id)
    return erreur is None


def renvoyer_echoues(db: Session, maintenant: datetime | None = None) -> tuple[int, int]:
    """Nouvelle tentative pour les e-mails en échec (3 au total) ; renvoie (envoyés, encore en
    échec).

    Un e-mail sans contenu conservé (réinitialisation du mot de passe) n'est pas renvoyé :
    son lien, valable une heure, serait de toute façon expiré. L'utilisateur en redemande un.
    """
    maintenant = maintenant or datetime.now(UTC)
    emails = db.scalars(
        select(EmailEnvoye)
        .where(
            EmailEnvoye.tentatives < TENTATIVES_MAX,
            EmailEnvoye.corps_texte.is_not(None),
            or_(
                EmailEnvoye.statut == StatutEmail.ECHEC,
                (EmailEnvoye.statut == StatutEmail.EN_ATTENTE)
                & (EmailEnvoye.cree_le < maintenant - EN_ATTENTE_ABANDONNE),
            ),
        )
        .order_by(EmailEnvoye.cree_le)
    ).all()
    a_renvoyer = [
        (
            email.id,
            Message(
                type=email.type,
                destinataire=email.destinataire,
                sujet=email.sujet,
                texte=email.corps_texte,
                html=email.corps_html,
                user_id=email.user_id,
                chantier_id=email.chantier_id,
                url_desinscription=email.url_desinscription,
            ),
        )
        for email in emails
    ]
    db.commit()  # libère la session avant les envois, qui notent leur résultat dans la leur
    envoyes = sum(envoyer(email_id, message) for email_id, message in a_renvoyer)
    return envoyes, len(a_renvoyer) - envoyes
