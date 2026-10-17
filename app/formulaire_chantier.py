"""Formulaire de création et de modification d'un chantier, et sa validation côté serveur."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from app.geo.coordonnees import CoordonneesInvalides, lire_coordonnees, valider_position
from app.models import Chantier, StatutChantier

BUDGET_MAX = Decimal("999999999999.99")  # numeric(14, 2)
RAYON_MIN, RAYON_MAX = 10, 1000


class FormulaireChantier(BaseModel):
    """Valeurs brutes, telles que saisies : on les renvoie telles quelles si le formulaire contient
    des erreurs."""

    nom: str = ""
    adresse: str = ""
    ville: str = ""
    pays: str = "BJ"
    budget: str = ""
    devise: str = "XOF"
    statut: str = StatutChantier.EN_PREPARATION.value
    rayon_verification_m: str = "100"
    # Remplis par la carte (clic, marqueur déplacé)…
    latitude: str = ""
    longitude: str = ""
    # …ou saisis : « 6.4512, 2.3501 », lien Google Maps ou WhatsApp. Prioritaire s'il est rempli.
    # Tout vide : position à définir plus tard (relevé par un proche).
    coordonnees: str = ""
    # Case cochée pour déplacer une position confirmée (relevée sur place).
    confirmer_deplacement: str = ""
    # Création seulement : modèle d'étapes à appliquer (case décochée : champ absent, donc vide).
    modele_etapes: str = ""

    @classmethod
    def depuis_chantier(
        cls, chantier: Chantier, position: tuple[float, float] | None
    ) -> "FormulaireChantier":
        latitude, longitude = (f"{x:.6f}" for x in position) if position else ("", "")
        return cls(
            nom=chantier.nom,
            adresse=chantier.adresse or "",
            ville=chantier.ville or "",
            pays=chantier.pays or "",
            budget="" if chantier.budget_prevu is None else f"{chantier.budget_prevu:f}",
            devise=chantier.devise,
            statut=chantier.statut.value,
            rayon_verification_m=str(chantier.rayon_verification_m),
            latitude=latitude,
            longitude=longitude,
            coordonnees=f"{latitude}, {longitude}" if position else "",
        )


@dataclass
class DonneesChantier:
    nom: str
    adresse: str | None
    ville: str | None
    pays: str | None
    budget_prevu: Decimal | None
    devise: str
    statut: StatutChantier
    rayon_verification_m: int
    position: tuple[float, float] | None  # (latitude, longitude)

    def colonnes(self) -> dict:
        """Valeurs à recopier dans le modèle Chantier (la position est construite à part)."""
        return {
            "nom": self.nom,
            "adresse": self.adresse,
            "ville": self.ville,
            "pays": self.pays,
            "budget_prevu": self.budget_prevu,
            "devise": self.devise,
            "statut": self.statut,
            "rayon_verification_m": self.rayon_verification_m,
        }


def valider(formulaire: FormulaireChantier) -> tuple[DonneesChantier | None, dict[str, str]]:
    erreurs: dict[str, str] = {}

    nom = formulaire.nom.strip()
    if not nom:
        erreurs["nom"] = "Donnez un nom au chantier."
    elif len(nom) > 150:
        erreurs["nom"] = "150 caractères au maximum."

    ville = formulaire.ville.strip()
    if len(ville) > 100:
        erreurs["ville"] = "100 caractères au maximum."

    pays = formulaire.pays.strip().upper()
    if pays and not re.fullmatch(r"[A-Z]{2}", pays):
        erreurs["pays"] = "Code pays à 2 lettres, ex. BJ."

    devise = formulaire.devise.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", devise):
        erreurs["devise"] = "Code devise à 3 lettres, ex. XOF."

    budget = None
    if texte_budget := re.sub(r"[\s  ]", "", formulaire.budget).replace(",", "."):
        try:
            budget = Decimal(texte_budget).quantize(Decimal("0.01"))
            if not 0 <= budget <= BUDGET_MAX:
                raise InvalidOperation
        except InvalidOperation:
            erreurs["budget"] = "Montant invalide, ex. 45 000 000."

    try:
        statut = StatutChantier(formulaire.statut)
    except ValueError:
        statut = None
        erreurs["statut"] = "Statut inconnu."

    try:
        rayon = int(formulaire.rayon_verification_m)
        if not RAYON_MIN <= rayon <= RAYON_MAX:
            raise ValueError
    except ValueError:
        rayon = None
        erreurs["rayon_verification_m"] = f"Rayon entre {RAYON_MIN} et {RAYON_MAX} m."

    position = None
    try:
        position = lire_position(formulaire)
    except CoordonneesInvalides as exc:
        erreurs["position"] = str(exc)

    if erreurs:
        return None, erreurs
    return (
        DonneesChantier(
            nom=nom,
            adresse=formulaire.adresse.strip() or None,
            ville=ville or None,
            pays=pays or None,
            budget_prevu=budget,
            devise=devise,
            statut=statut,
            rayon_verification_m=rayon,
            position=position,
        ),
        {},
    )


def lire_position(formulaire: FormulaireChantier) -> tuple[float, float] | None:
    """Le champ texte prime : c'est le parseur Python qui fait foi, la carte le tient à jour.
    Rien de saisi : None, la position sera définie plus tard."""
    if formulaire.coordonnees.strip():
        return lire_coordonnees(formulaire.coordonnees)
    if formulaire.latitude.strip() or formulaire.longitude.strip():
        try:
            latitude, longitude = float(formulaire.latitude), float(formulaire.longitude)
        except ValueError:
            raise CoordonneesInvalides(
                "Latitude et longitude doivent être des nombres (point décimal)."
            ) from None
        return valider_position(latitude, longitude)
    return None
