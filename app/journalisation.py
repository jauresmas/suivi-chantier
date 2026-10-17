"""Filtre des journaux : les jetons des liens (invitation, relevé, mot de passe, désinscription) n'y
apparaissent jamais."""

import logging
import re

# /invitations/<jeton>, /releve/<jeton>, /reinitialiser/<jeton> et lien de désinscription, en clair
# ou encodés dans un paramètre (suivant=%2Finvitations%2F<jeton>).
MOTIF_JETON = re.compile(
    r"((?:invitations|releve|reinitialiser|desinscription)(?:/|%2F))[A-Za-z0-9_.\-]+", re.IGNORECASE
)


def masquer_jetons(texte: str) -> str:
    return MOTIF_JETON.sub(r"\1***", texte)


class MasquerJetons(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = masquer_jetons(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(masquer_jetons(a) if isinstance(a, str) else a for a in record.args)
        return True
