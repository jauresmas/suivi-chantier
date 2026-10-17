"""Modèles de la base suivi_chantier.

Tous les modèles sont importés ici pour qu'Alembic les trouve dans Base.metadata.
"""

from app.models.base import Base
from app.models.chantier import Chantier, SourcePositionChantier, StatutChantier, StatutPosition
from app.models.email_envoye import EmailEnvoye, StatutEmail, TypeEmail
from app.models.etape import Etape, TypeEtape
from app.models.historique_avancement import HistoriqueAvancement
from app.models.historique_position import HistoriquePositionChantier
from app.models.invitation import Invitation
from app.models.jeton_reinitialisation import JetonReinitialisation
from app.models.membre_chantier import MembreChantier, RoleMembre
from app.models.photo import Photo, SourcePosition, StatutVerification
from app.models.preference_notification import ModeNotification, PreferenceNotification
from app.models.rapport import Rapport, StatutRapport
from app.models.releve_position import PhotoReleve, RelevePosition, StatutReleve, TypeReleveur
from app.models.session_utilisateur import SessionUtilisateur
from app.models.user import User

__all__ = [
    "Base",
    "Chantier",
    "EmailEnvoye",
    "Etape",
    "HistoriqueAvancement",
    "HistoriquePositionChantier",
    "Invitation",
    "JetonReinitialisation",
    "MembreChantier",
    "ModeNotification",
    "Photo",
    "PhotoReleve",
    "PreferenceNotification",
    "Rapport",
    "RelevePosition",
    "RoleMembre",
    "SessionUtilisateur",
    "SourcePosition",
    "SourcePositionChantier",
    "StatutChantier",
    "StatutEmail",
    "StatutPosition",
    "StatutRapport",
    "StatutReleve",
    "StatutVerification",
    "TypeEmail",
    "TypeEtape",
    "TypeReleveur",
    "User",
]
