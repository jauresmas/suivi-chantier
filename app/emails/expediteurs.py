"""Expéditeurs d'e-mails, interchangeables : choisis par la variable EMAIL_BACKEND.

- « console » (défaut en développement) : l'e-mail est affiché dans les journaux ;
- « smtp » : envoi réel (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_EXPEDITEUR) ;
- ExpediteurMemoire : garde les e-mails dans une liste, pour les tests.
"""

import logging
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Protocol

from app.config import get_settings
from app.models import TypeEmail

journal = logging.getLogger("app.emails")
DELAI_SMTP_S = 15


@dataclass
class Message:
    type: TypeEmail
    destinataire: str
    sujet: str
    texte: str
    html: str
    user_id: uuid.UUID | None = None
    chantier_id: uuid.UUID | None = None
    url_desinscription: str | None = None  # absent des e-mails de sécurité
    sensible: bool = (
        False  # contenu non conservé en base (lien de réinitialisation du mot de passe)
    )
    # Instant de référence des commandes (résumé, inactivité) : le suivant repart exactement de là.
    cree_le: datetime | None = None


def construire(message: Message, expediteur: str) -> EmailMessage:
    """E-mail MIME en deux versions (texte brut et HTML), avec désinscription en un clic (RFC
    8058)."""
    mime = EmailMessage()
    mime["From"] = expediteur
    mime["To"] = message.destinataire
    mime["Subject"] = message.sujet
    mime["Date"] = formatdate(localtime=False)
    domaine = parseaddr(expediteur)[1].rpartition("@")[2] or "localhost"
    mime["Message-ID"] = make_msgid(domain=domaine)
    if message.url_desinscription:
        mime["List-Unsubscribe"] = f"<{message.url_desinscription}>"
        mime["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    mime.set_content(message.texte)
    mime.add_alternative(message.html, subtype="html")
    return mime


class ExpediteurEmail(Protocol):
    def envoyer(self, message: Message) -> None:
        """Envoie l'e-mail ; lève une exception en cas d'échec."""


class ExpediteurConsole:
    """Développement : aucun envoi, l'e-mail (version texte) est écrit dans les journaux."""

    def __init__(self):
        if not journal.handlers:
            gestionnaire = logging.StreamHandler()
            gestionnaire.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
            journal.addHandler(gestionnaire)
            journal.setLevel(logging.INFO)
            journal.propagate = False

    def envoyer(self, message: Message) -> None:
        cadre = (
            "=" * 72
        )  # caractères ASCII : la console Windows (cp1252) ne sait pas afficher les filets
        journal.info(
            "E-mail (console, non envoyé)\n%s\nÀ : %s\nObjet : %s\n\n%s\n%s",
            cadre,
            message.destinataire,
            message.sujet,
            message.texte.strip(),
            cadre,
        )


class ExpediteurSmtp:
    def __init__(
        self,
        hote: str,
        port: int,
        utilisateur: str | None,
        mot_de_passe: str | None,
        expediteur: str,
    ):
        self.hote, self.port = hote, port
        self.utilisateur, self.mot_de_passe = utilisateur, mot_de_passe
        self.expediteur = expediteur

    def envoyer(self, message: Message) -> None:
        contexte = ssl.create_default_context()
        if self.port == 465:
            connexion = smtplib.SMTP_SSL(
                self.hote, self.port, timeout=DELAI_SMTP_S, context=contexte
            )
        else:
            connexion = smtplib.SMTP(self.hote, self.port, timeout=DELAI_SMTP_S)
        with connexion:
            if self.port != 465:
                connexion.ehlo()
                if connexion.has_extn("starttls"):
                    connexion.starttls(context=contexte)
                elif self.utilisateur:
                    # Jamais d'identifiants en clair. (Sans identifiants : serveur de test local,
                    # type Mailpit.)
                    raise smtplib.SMTPNotSupportedError("Le serveur SMTP ne propose pas STARTTLS.")
            if self.utilisateur:
                connexion.login(self.utilisateur, self.mot_de_passe or "")
            connexion.send_message(construire(message, self.expediteur))


class ExpediteurMemoire:
    """Tests : garde les e-mails envoyés ; `panne` simule un serveur SMTP indisponible."""

    def __init__(self):
        self.envoyes: list[Message] = []
        self.panne: Exception | None = None

    def envoyer(self, message: Message) -> None:
        if self.panne is not None:
            raise self.panne
        construire(message, "Tests <tests@exemple.test>")  # l'e-mail doit être constructible
        self.envoyes.append(message)

    def pour(self, destinataire: str) -> list[Message]:
        return [m for m in self.envoyes if m.destinataire == destinataire]


def obtenir_expediteur() -> ExpediteurEmail:
    settings = get_settings()
    if settings.email_backend == "smtp":
        return ExpediteurSmtp(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_user,
            settings.smtp_password,
            settings.email_expediteur,
        )
    return ExpediteurConsole()
