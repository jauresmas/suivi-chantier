"""Niveau de fiabilité de la position d'un chantier, selon son statut et sa source.

Règle de confiance : le point de référence ne doit pas dépendre de la personne dont on vérifie
le travail. Un relevé du chef de chantier vaut donc moins qu'un relevé fait par un proche.
"""

from dataclasses import dataclass

from app.models import Chantier, SourcePositionChantier, StatutPosition


@dataclass(frozen=True)
class Fiabilite:
    libelle: str
    niveau: str | None  # « Faible » … « Très élevé » ; None quand il n'y a rien à évaluer
    rang: int  # 0 à 5 : nombre de barres pleines de la jauge
    ton: str  # couleur de la charte
    icone: str  # icône Lucide : la couleur ne porte jamais seule l'information


A_DEFINIR = Fiabilite("Position du chantier à définir", None, 0, "alerte", "map-pin-off")
A_VALIDER = Fiabilite("Relevé reçu — à valider", None, 0, "primaire", "map-pin")
ESTIMEE = Fiabilite("Position estimée sur la carte", "Faible", 1, "alerte", "map-pin")

CONFIRMEES = {
    SourcePositionChantier.RELEVE_CHEF: Fiabilite(
        "Relevé par le chef de chantier", "Moyen", 2, "neutre", "map-pin-check"
    ),
    SourcePositionChantier.RELEVE_PROCHE: Fiabilite(
        "Relevé sur place par un proche", "Bon", 3, "succes", "map-pin-check"
    ),
    SourcePositionChantier.BORNES_DOCUMENT: Fiabilite(
        "Bornes du plan foncier", "Élevé", 4, "succes", "map-pin-check"
    ),
    SourcePositionChantier.GEOMETRE: Fiabilite(
        "Levé par un géomètre", "Très élevé", 5, "succes", "map-pin-check"
    ),
}

LIBELLES_STATUT_POSITION = {
    StatutPosition.NON_DEFINIE: "À définir",
    StatutPosition.ESTIMEE: "Estimée",
    StatutPosition.A_VALIDER: "À valider",
    StatutPosition.CONFIRMEE: "Confirmée",
}

LIBELLES_SOURCE_POSITION = {
    SourcePositionChantier.CARTE_PROPRIETAIRE: "Placée sur la carte par le propriétaire",
    SourcePositionChantier.RELEVE_PROCHE: "Relevé sur place par un proche",
    SourcePositionChantier.RELEVE_CHEF: "Relevé par le chef de chantier",
    SourcePositionChantier.BORNES_DOCUMENT: "Bornes du plan foncier",
    SourcePositionChantier.GEOMETRE: "Levé par un géomètre",
}


def fiabilite(statut: StatutPosition, source: SourcePositionChantier | None) -> Fiabilite:
    if statut == StatutPosition.NON_DEFINIE:
        return A_DEFINIR
    if statut == StatutPosition.A_VALIDER:
        return A_VALIDER
    if statut == StatutPosition.CONFIRMEE and source in CONFIRMEES:
        return CONFIRMEES[source]
    # Estimée, ou combinaison inattendue (point placé sur la carte marqué « confirmé ») : au plus
    # bas.
    return ESTIMEE


def niveau_fiabilite(chantier: Chantier) -> Fiabilite:
    return fiabilite(chantier.statut_position, chantier.source_position)
