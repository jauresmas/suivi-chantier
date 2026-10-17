"""Réinitialisation du mot de passe : jeton haché, valable 1 heure, à usage unique.

La page « Mot de passe oublié » répond la même chose que l'adresse ait un compte ou non :
elle ne révèle pas qui est inscrit. Au plus 3 demandes par compte et par heure.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth.securite import empreinte, hacher_mot_de_passe, nouveau_jeton
from app.models import JetonReinitialisation, SessionUtilisateur, User

DUREE_JETON = timedelta(hours=1)
DEMANDES_MAX_PAR_HEURE = 3


def demander(db: Session, email: str) -> tuple[User, str] | None:
    """Crée un jeton et renvoie (utilisateur, jeton en clair), ou None : compte inconnu, ou limite
    atteinte."""
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        return None
    maintenant = datetime.now(UTC)
    recentes = db.scalar(
        select(func.count())
        .select_from(JetonReinitialisation)
        .where(
            JetonReinitialisation.user_id == user.id,
            JetonReinitialisation.cree_le > maintenant - timedelta(hours=1),
        )
    )
    if recentes >= DEMANDES_MAX_PAR_HEURE:
        return None
    jeton = nouveau_jeton()
    db.add(
        JetonReinitialisation(
            user_id=user.id,
            empreinte_jeton=empreinte(jeton),
            cree_le=maintenant,
            expire_le=maintenant + DUREE_JETON,
        )
    )
    db.commit()
    return user, jeton


def jeton_valide(
    db: Session, jeton: str, verrouiller: bool = False
) -> JetonReinitialisation | None:
    """Jeton connu, ni expiré ni déjà utilisé ; sinon None."""
    requete = select(JetonReinitialisation).where(
        JetonReinitialisation.empreinte_jeton == empreinte(jeton)
    )
    if verrouiller:
        requete = requete.with_for_update()  # deux envois simultanés du formulaire : un seul passe
    trouve = db.scalar(requete)
    if trouve is None or trouve.utilise_le is not None or trouve.expire_le <= datetime.now(UTC):
        return None
    return trouve


def reinitialiser(db: Session, jeton: JetonReinitialisation, mot_de_passe: str) -> User:
    """Nouveau mot de passe, jetons restants invalidés et toutes les sessions fermées (sur tous les
    appareils)."""
    maintenant = datetime.now(UTC)
    user = db.get(User, jeton.user_id)
    user.hash_mot_de_passe = hacher_mot_de_passe(mot_de_passe)
    db.execute(
        update(JetonReinitialisation)
        .where(JetonReinitialisation.user_id == user.id, JetonReinitialisation.utilise_le.is_(None))
        .values(utilise_le=maintenant)
    )
    db.execute(delete(SessionUtilisateur).where(SessionUtilisateur.user_id == user.id))
    return user
