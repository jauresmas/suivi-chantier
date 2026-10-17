"""Libellés français des valeurs du modèle, partagés par les pages, l'API et les services."""

from app.models import Etape, RoleMembre, StatutChantier, TypeEtape

LIBELLES_ROLE = {
    RoleMembre.PROPRIETAIRE: "Propriétaire",
    RoleMembre.CHEF_CHANTIER: "Chef de chantier",
}

LIBELLES_STATUT = {
    StatutChantier.EN_PREPARATION: "En préparation",
    StatutChantier.EN_COURS: "En cours",
    StatutChantier.EN_PAUSE: "En pause",
    StatutChantier.TERMINE: "Terminé",
}


LIBELLES_TYPE_ETAPE = {
    TypeEtape.TERRASSEMENT: "Terrassement",
    TypeEtape.FONDATIONS: "Fondations",
    TypeEtape.ELEVATION: "Élévation",
    TypeEtape.DALLE: "Dalle",
    TypeEtape.TOITURE: "Toiture",
    TypeEtape.MENUISERIES: "Menuiseries",
    TypeEtape.ELECTRICITE_PLOMBERIE: "Électricité et plomberie",
    TypeEtape.FINITIONS: "Finitions",
    TypeEtape.AUTRE: "Autre",
}


def libelle_etape(etape: Etape | None) -> str:
    if etape is None:
        return ""
    if etape.type == TypeEtape.AUTRE and etape.libelle:
        return etape.libelle
    return LIBELLES_TYPE_ETAPE[etape.type]


# Teinte de badge et icône Lucide de chaque statut de chantier.
APPARENCE_STATUT = {
    StatutChantier.EN_PREPARATION: ("neutre", "clock"),
    StatutChantier.EN_COURS: ("primaire", "construction"),
    StatutChantier.EN_PAUSE: ("alerte", "clock-alert"),
    StatutChantier.TERMINE: ("succes", "circle-check"),
}


def initiales(nom: str | None) -> str:
    """« Rodrigue Hounkpatin » → « RH » ; compte supprimé → « ? »."""
    mots = (nom or "").split()
    return "".join(mot[0] for mot in mots[:2]).upper() or "?"
