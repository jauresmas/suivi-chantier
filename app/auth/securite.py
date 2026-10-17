"""Mots de passe, jetons aléatoires et redirections sûres."""

import hashlib
import secrets
from functools import cache

from pwdlib import PasswordHash

from app.config import get_settings

hacheur = PasswordHash.recommended()  # Argon2id


def hacher_mot_de_passe(mot_de_passe: str) -> str:
    return hacheur.hash(mot_de_passe)


@cache
def _hash_factice() -> str:
    return hacheur.hash(secrets.token_urlsafe(16))


def verifier_mot_de_passe(mot_de_passe: str, hash_enregistre: str | None) -> bool:
    if hash_enregistre is None:
        # E-mail inconnu ou compte sans mot de passe : on calcule quand même un hash,
        # pour que le temps de réponse ne révèle pas si le compte existe.
        hacheur.verify(mot_de_passe, _hash_factice())
        return False
    return hacheur.verify(mot_de_passe, hash_enregistre)


def nouveau_jeton() -> str:
    return secrets.token_urlsafe(32)


def empreinte(jeton: str) -> str:
    """SHA-256 du jeton : c'est tout ce que la base en conserve."""
    return hashlib.sha256(jeton.encode()).hexdigest()


def cookies_securises() -> bool:
    return get_settings().app_env == "production"


def chemin_interne(suivant: str | None, defaut: str = "/chantiers") -> str:
    """Renvoie `suivant` s'il désigne une page de ce site, sinon `defaut` (pas de redirection
    ouverte)."""
    if (
        suivant
        and suivant.startswith("/")
        and not suivant.startswith("//")
        # Les navigateurs lisent « /\ » comme « // » et ignorent tabulations et retours à la ligne.
        and "\\" not in suivant
        and suivant.isprintable()
    ):
        return suivant
    return defaut
