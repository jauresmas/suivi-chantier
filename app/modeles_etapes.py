"""Modèles d'étapes proposés à la création d'un chantier, et poids par défaut de chaque type.

Le poids est la part de l'étape dans l'avancement global (voir app/services/avancement.py).
Pour ajouter un modèle (immeuble, clôture…), il suffit de compléter MODELES.
"""

from dataclasses import dataclass

from app.models import TypeEtape


@dataclass(frozen=True)
class ModeleEtapes:
    nom: str
    etapes: tuple[tuple[TypeEtape, int], ...]  # (type, poids), dans l'ordre du chantier

    @property
    def poids_total(self) -> int:
        return sum(poids for _, poids in self.etapes)


MAISON_INDIVIDUELLE = ModeleEtapes(
    nom="Maison individuelle",
    etapes=(
        (TypeEtape.TERRASSEMENT, 5),
        (TypeEtape.FONDATIONS, 15),
        (TypeEtape.ELEVATION, 20),
        (TypeEtape.DALLE, 15),
        (TypeEtape.TOITURE, 10),
        (TypeEtape.MENUISERIES, 10),
        (TypeEtape.ELECTRICITE_PLOMBERIE, 10),
        (TypeEtape.FINITIONS, 15),
    ),
)

MODELES = {"maison_individuelle": MAISON_INDIVIDUELLE}

# Poids proposé quand on ajoute une étape isolée.
POIDS_PAR_DEFAUT = {type_: poids for type_, poids in MAISON_INDIVIDUELLE.etapes} | {
    TypeEtape.AUTRE: 10
}
