import pytest

from app.geo.coordonnees import CoordonneesInvalides, distance_m, lire_coordonnees, valider_position

ABOMEY_CALAVI = (6.4512, 2.3501)


@pytest.mark.parametrize(
    "texte",
    [
        # Texte copié depuis un téléphone : latitude, longitude
        "6.4512, 2.3501",
        "6.4512,2.3501",
        "6.4512 2.3501",
        "  6.4512 ,   2.3501  ",
        "+6.4512, +2.3501",
        # Liens Google Maps
        "https://www.google.com/maps/@6.4512,2.3501,17z",
        "https://www.google.com/maps/place/Abomey-Calavi/@6.4512,2.3501,15z/data=!3m1!4b1",
        "https://maps.google.com/?q=6.4512,2.3501",
        "https://www.google.com/maps/search/?api=1&query=6.4512,2.3501",
        # Position partagée sur WhatsApp (virgule encodée), éventuellement précédée d'un texte
        "https://maps.google.com/maps?q=6.4512%2C2.3501&z=17&hl=fr",
        "Ma position : https://maps.google.com/maps?q=6.4512%2C2.3501&z=17&hl=fr",
    ],
)
def test_formats_reconnus(texte):
    assert lire_coordonnees(texte) == ABOMEY_CALAVI


def test_lien_de_lieu_privilegie_le_repere_au_centre_de_la_vue():
    # Dans /place/…, « @ » est le centre de la vue ; « !3d…!4d… » est le lieu lui-même.
    lien = "https://www.google.com/maps/place/Chantier/@6.40,2.30,15z/data=!4m6!3m5!1s0x0:0x0!8m2!3d6.4512!4d2.3501"
    assert lire_coordonnees(lien) == ABOMEY_CALAVI


def test_coordonnees_negatives_hemisphere_sud_et_ouest():
    assert lire_coordonnees("-12.0464, -77.0428") == (-12.0464, -77.0428)


def test_arrondi_a_6_decimales():
    assert lire_coordonnees("6.45123456789, 2.35019999") == (6.451235, 2.3502)


@pytest.mark.parametrize(
    "texte, extrait_du_message",
    [
        ("abc", "Format non reconnu"),
        ("95, 2.3", "Latitude 95 impossible"),
        ("", "Collez des coordonnées"),
        ("   ", "Collez des coordonnées"),
        ("6,4512 2,3501", "Format non reconnu"),  # virgule décimale
        ("6,45", "point"),  # ambigu : 6,45 ou 6.45 ?
        ("6.45, 200", "Longitude 200 impossible"),
        ("6.45; 2.35", "Format non reconnu"),
        ("https://www.google.com/maps/place/Cotonou", "ne contient pas de coordonnées"),
        ("https://maps.app.goo.gl/AbCdEf123", "Lien raccourci"),
    ],
)
def test_cas_invalides(texte, extrait_du_message):
    with pytest.raises(CoordonneesInvalides, match=extrait_du_message):
        lire_coordonnees(texte)


def test_inversion_latitude_longitude_signalee():
    # Longitude d'abord par erreur, avec une « latitude » hors limites : on suggère l'inversion.
    with pytest.raises(CoordonneesInvalides, match="inversées"):
        valider_position(120.5, 6.45)


def test_valeurs_non_finies_refusees():
    with pytest.raises(CoordonneesInvalides):
        valider_position(float("nan"), 2.35)


def test_distance_cotonou_porto_novo():
    # Environ 27 km à vol d'oiseau entre les centres des deux villes.
    assert distance_m(6.3654, 2.4183, 6.4969, 2.6289) == pytest.approx(27_400, rel=0.05)
