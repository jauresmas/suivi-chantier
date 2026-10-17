"""Fonctions partagées par les tests."""

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from sqlalchemy.orm import Session

from app.auth.securite import hacher_mot_de_passe
from app.models import SourcePositionChantier, StatutPosition, User

MOT_DE_PASSE = "mot-de-passe-test"
# Un seul calcul Argon2 pour tous les comptes de test.
HASH_MOT_DE_PASSE = hacher_mot_de_passe(MOT_DE_PASSE)

MOTIF_CSRF = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')
MOTIF_LIEN_INVITATION = re.compile(r"/invitations/([A-Za-z0-9_\-]{43})")


def position_confirmee(lon: float, lat: float, source=SourcePositionChantier.RELEVE_PROCHE) -> dict:
    """Colonnes d'un chantier dont la position a été relevée sur place et validée (contraintes de la
    table respectées)."""
    return {
        "position": WKTElement(f"POINT({lon} {lat})", srid=4326),
        "statut_position": StatutPosition.CONFIRMEE,
        "source_position": source,
        "position_confirmee_le": datetime.now(UTC),
    }


def position_estimee(lon: float, lat: float) -> dict:
    """Colonnes d'un chantier placé sur la carte par son propriétaire, sans relevé sur place."""
    return {
        "position": WKTElement(f"POINT({lon} {lat})", srid=4326),
        "statut_position": StatutPosition.ESTIMEE,
        "source_position": SourcePositionChantier.CARTE_PROPRIETAIRE,
    }


def creer_utilisateur(session: Session, email: str, nom: str = "Utilisateur de test") -> User:
    user = User(email=email, nom_complet=nom, hash_mot_de_passe=HASH_MOT_DE_PASSE)
    session.add(user)
    session.flush()
    return user


def jeton_csrf(client: TestClient, page: str = "/connexion") -> str:
    """Charge une page comme le ferait un navigateur et renvoie le jeton CSRF de ses formulaires."""
    reponse = client.get(page)
    trouve = MOTIF_CSRF.search(reponse.text)
    assert trouve, f"pas de jeton CSRF dans {page}"
    return trouve.group(1)


def poster(client: TestClient, url: str, donnees: dict | None = None, page: str = "/connexion"):
    """POST d'un formulaire avec son jeton CSRF, sans suivre la redirection."""
    donnees = {"csrf_token": jeton_csrf(client, page), **(donnees or {})}
    return client.post(url, data=donnees, follow_redirects=False)


def se_connecter(
    client: TestClient, email: str, mot_de_passe: str = MOT_DE_PASSE, suivant: str = ""
):
    return poster(
        client, "/connexion", {"email": email, "mot_de_passe": mot_de_passe, "suivant": suivant}
    )


class FauxNominatim:
    """Remplace l'appel HTTP à Nominatim : enregistre les requêtes et renvoie des réponses
    préparées.

    `reponses` associe un texte recherché (en minuscules) à la liste renvoyée par Nominatim.
    Si `panne` est une exception, chaque appel la lève (service indisponible).
    """

    def __init__(self):
        self.appels: list[dict] = []
        self.attentes: list[float] = []  # pauses demandées pour respecter 1 requête par seconde
        self.reponses: dict[str, list[dict]] = {}
        self.panne: Exception | None = None

    def __call__(self, url: str, user_agent: str, delai_s: float) -> list[dict]:
        self.appels.append({"url": url, "user_agent": user_agent})
        if self.panne:
            raise self.panne
        texte = parse_qs(urlparse(url).query)["q"][0]
        return self.reponses.get(texte.lower(), [])


def lieu_nominatim(nom: str, lat: float, lon: float, demi_cote: float = 0.05) -> dict:
    """Résultat au format de l'API Nominatim (jsonv2) : nombres en texte, boundingbox [sud, nord,
    ouest, est]."""
    return {
        "display_name": nom,
        "lat": str(lat),
        "lon": str(lon),
        "boundingbox": [
            str(lat - demi_cote),
            str(lat + demi_cote),
            str(lon - demi_cote),
            str(lon + demi_cote),
        ],
    }


def image_jpeg(largeur: int = 640, hauteur: int = 480, couleur: str = "orange") -> bytes:
    """Petite image JPEG générée à la volée (aucun fichier binaire dans le dépôt)."""
    from io import BytesIO

    from PIL import Image

    tampon = BytesIO()
    Image.new("RGB", (largeur, hauteur), couleur).save(tampon, "JPEG")
    return tampon.getvalue()


def envoyer_photo(client: TestClient, rapport_id, contenu: bytes | None = None, **champs):
    """Envoi d'une photo comme le fait la page (XHR avec en-tête X-CSRF-Token)."""
    return client.post(
        f"/rapports/{rapport_id}/photos",
        files={"fichier": ("photo.jpg", contenu or image_jpeg(), "image/jpeg")},
        data=champs,
        headers={"X-CSRF-Token": jeton_csrf(client, "/chantiers")},
    )
