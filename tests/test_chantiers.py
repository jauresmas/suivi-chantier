import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select

from app.geo.nominatim import nominatim
from app.models import Chantier, MembreChantier, RoleMembre, StatutChantier
from tests.outils import creer_utilisateur, lieu_nominatim, position_confirmee, poster, se_connecter

FORMULAIRE = {
    "nom": "Villa test",
    "adresse": "Derrière la pharmacie",
    "ville": "",
    "pays": "BJ",
    "budget": "45 000 000",
    "devise": "XOF",
    "statut": "en_cours",
    "rayon_verification_m": "150",
    "latitude": "",
    "longitude": "",
    "coordonnees": "",
}


@pytest.fixture
def proprio(navigateur, session):
    user = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    se_connecter(navigateur, user.email)
    return user


def creer(navigateur, **champs):
    return poster(
        navigateur, "/chantiers/nouveau", {**FORMULAIRE, **champs}, page="/chantiers/nouveau"
    )


def id_depuis_redirection(reponse) -> uuid.UUID:
    return uuid.UUID(reponse.headers["location"].split("?")[0].removeprefix("/chantiers/"))


def lon_lat_wkt(session, chantier_id):
    geometrie = cast(Chantier.position, Geometry)
    return session.execute(
        select(func.ST_X(geometrie), func.ST_Y(geometrie), func.ST_AsText(Chantier.position)).where(
            Chantier.id == chantier_id
        )
    ).one()


def nombre_de_chantiers(session, user) -> int:
    return session.scalar(
        select(func.count()).select_from(MembreChantier).where(MembreChantier.user_id == user.id)
    )


# --- Création -------------------------------------------------------------------------------------


def test_creation_par_clic_position_relue_dans_le_bon_ordre(navigateur, session, proprio):
    reponse = creer(navigateur, latitude="6.4512", longitude="2.3501")

    assert reponse.status_code == 303
    chantier_id = id_depuis_redirection(reponse)
    lon, lat, wkt = lon_lat_wkt(session, chantier_id)
    assert (lon, lat) == (2.3501, 6.4512)
    assert wkt == "POINT(2.3501 6.4512)"  # longitude d'abord
    chantier = session.get(Chantier, chantier_id)
    assert chantier.budget_prevu == Decimal("45000000.00")
    assert chantier.rayon_verification_m == 150
    assert chantier.statut == StatutChantier.EN_COURS


def test_createur_devient_proprietaire(navigateur, session, proprio):
    chantier_id = id_depuis_redirection(creer(navigateur, latitude="6.4512", longitude="2.3501"))

    membre = session.get(MembreChantier, (chantier_id, proprio.id))
    assert membre.role == RoleMembre.PROPRIETAIRE
    assert navigateur.get(f"/chantiers/{chantier_id}").status_code == 200


def test_creation_par_lien_whatsapp_colle(navigateur, session, proprio):
    reponse = creer(
        navigateur, coordonnees="https://maps.google.com/maps?q=6.4512%2C2.3501&z=17&hl=fr"
    )

    lon, lat, _ = lon_lat_wkt(session, id_depuis_redirection(reponse))
    assert (lon, lat) == (2.3501, 6.4512)


def test_texte_colle_prioritaire_sur_les_champs_de_la_carte(navigateur, session, proprio):
    reponse = creer(navigateur, coordonnees="6.4512, 2.3501", latitude="9.0", longitude="2.0")

    lon, lat, _ = lon_lat_wkt(session, id_depuis_redirection(reponse))
    assert (lon, lat) == (2.3501, 6.4512)


def test_coordonnees_arrondies_a_6_decimales(navigateur, session, proprio):
    reponse = creer(navigateur, latitude="6.123456789", longitude="2.987654321")

    lon, lat, _ = lon_lat_wkt(session, id_depuis_redirection(reponse))
    assert (lon, lat) == (2.987654, 6.123457)


@pytest.mark.parametrize(
    "champs, message",
    [
        ({"latitude": "95", "longitude": "2.35"}, "Latitude 95 impossible"),
        ({"latitude": "6.45", "longitude": "-181"}, "Longitude -181 impossible"),
        ({"coordonnees": "95, 2.3"}, "Latitude 95 impossible"),
        ({"latitude": "abc", "longitude": "2.35"}, "doivent être des nombres"),
    ],
)
def test_position_invalide_formulaire_renvoye_rien_en_base(
    navigateur, session, proprio, champs, message
):
    reponse = creer(navigateur, **champs)

    assert reponse.status_code == 400
    assert message in reponse.text
    assert 'value="Villa test"' in reponse.text  # la saisie est conservée
    assert nombre_de_chantiers(session, proprio) == 0


def test_avertissement_si_le_point_est_loin_de_la_ville(
    navigateur, session, proprio, faux_nominatim
):
    faux_nominatim.reponses["cotonou"] = [
        lieu_nominatim("Cotonou, Littoral, Bénin", 6.3654, 2.4183)
    ]

    # Point placé à Parakou (~330 km) alors que la ville saisie est Cotonou.
    reponse = creer(navigateur, ville="Cotonou", latitude="9.3372", longitude="2.6303")

    assert reponse.status_code == 303
    assert "alerte_distance_km=" in reponse.headers["location"]
    assert nombre_de_chantiers(session, proprio) == 1, "avertissement non bloquant"
    assert "331 km de Cotonou" in navigateur.get(reponse.headers["location"]).text
    assert faux_nominatim.appels[0]["user_agent"].startswith("suivi-chantier/")


def test_pas_d_avertissement_si_le_point_est_dans_la_ville(navigateur, proprio, faux_nominatim):
    faux_nominatim.reponses["cotonou"] = [
        lieu_nominatim("Cotonou, Littoral, Bénin", 6.3654, 2.4183)
    ]

    reponse = creer(navigateur, ville="Cotonou", latitude="6.3700", longitude="2.4300")

    assert "alerte" not in reponse.headers["location"]


def test_page_de_creation_charge_maplibre_en_version_epinglee(navigateur, proprio):
    page = navigateur.get("/chantiers/nouveau").text

    assert "maplibre-gl@5.24.0/dist/maplibre-gl.js" in page
    assert 'integrity="sha384-' in page
    assert "@latest" not in page


# --- Modification et permissions
# -------------------------------------------------------------------


@pytest.fixture
def chantier_partage(session):
    """Chantier avec un propriétaire et un chef, plus un utilisateur étranger au chantier."""
    proprio = creer_utilisateur(session, "proprio@exemple.test")
    chef = creer_utilisateur(session, "chef@exemple.test")
    autre = creer_utilisateur(session, "autre@exemple.test")
    chantier = Chantier(nom="Villa Calavi", **position_confirmee(2.3501, 6.4512))
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    session.add(chantier)
    session.flush()
    return SimpleNamespace(proprio=proprio, chef=chef, autre=autre, chantier=chantier)


def test_chef_de_chantier_ne_peut_pas_modifier(navigateur, session, chantier_partage):
    url = f"/chantiers/{chantier_partage.chantier.id}/modifier"
    se_connecter(navigateur, chantier_partage.chef.email)

    page = navigateur.get(url)
    envoi = poster(
        navigateur,
        url,
        {**FORMULAIRE, "nom": "Piraté", "latitude": "9", "longitude": "2"},
        page="/chantiers",
    )

    assert page.status_code == 403
    assert envoi.status_code == 403
    session.refresh(chantier_partage.chantier)
    assert chantier_partage.chantier.nom == "Villa Calavi"


def test_non_membre_recoit_404_sur_la_modification(navigateur, chantier_partage):
    se_connecter(navigateur, chantier_partage.autre.email)

    assert navigateur.get(f"/chantiers/{chantier_partage.chantier.id}/modifier").status_code == 404


def test_proprietaire_modifie_nom_et_position(navigateur, session, chantier_partage):
    chantier_id = chantier_partage.chantier.id
    se_connecter(navigateur, chantier_partage.proprio.email)
    formulaire = navigateur.get(f"/chantiers/{chantier_id}/modifier").text
    assert 'value="6.451200, 2.350100"' in formulaire  # position actuelle préremplie

    reponse = poster(
        navigateur,
        f"/chantiers/{chantier_id}/modifier",
        {
            **FORMULAIRE,
            "nom": "Villa Calavi (R+2)",
            "coordonnees": "6.4600, 2.3600",
            "confirmer_deplacement": "1",
        },
        page="/chantiers",
    )

    assert reponse.status_code == 303
    session.refresh(chantier_partage.chantier)
    assert chantier_partage.chantier.nom == "Villa Calavi (R+2)"
    assert lon_lat_wkt(session, chantier_id)[:2] == (2.36, 6.46)


# --- GeoJSON --------------------------------------------------------------------------------------


def test_geojson_ne_contient_que_les_chantiers_de_l_utilisateur(
    navigateur, session, chantier_partage
):
    etranger = Chantier(nom="Chantier d'un autre", **position_confirmee(2.4, 6.37))
    etranger.membres.append(
        MembreChantier(user_id=chantier_partage.autre.id, role=RoleMembre.PROPRIETAIRE)
    )
    session.add(etranger)
    session.flush()
    se_connecter(navigateur, chantier_partage.chef.email)

    reponse = navigateur.get("/api/chantiers.geojson")

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("application/geo+json")
    collection = reponse.json()
    assert collection["type"] == "FeatureCollection"
    assert [f["properties"]["nom"] for f in collection["features"]] == ["Villa Calavi"]
    chantier = collection["features"][0]
    assert chantier["geometry"] == {"type": "Point", "coordinates": [2.3501, 6.4512]}  # [lon, lat]
    assert chantier["properties"]["role"] == "chef_chantier"
    assert chantier["properties"]["id"] == str(chantier_partage.chantier.id)


def test_geojson_exige_une_connexion(navigateur):
    assert navigateur.get("/api/chantiers.geojson").status_code == 401


# --- Recherche de lieu (Nominatim simulé)
# ----------------------------------------------------------


def test_recherche_de_lieu_simulee_et_mise_en_cache(navigateur, proprio, faux_nominatim):
    faux_nominatim.reponses["abomey-calavi"] = [
        lieu_nominatim("Abomey-Calavi, Atlantique, Bénin", 6.4485, 2.3557)
    ]

    premiere = navigateur.get("/api/recherche-lieu", params={"q": "Abomey-Calavi", "pays": "BJ"})
    seconde = navigateur.get("/api/recherche-lieu", params={"q": "  abomey-calavi ", "pays": "bj"})

    assert premiere.status_code == 200
    lieu = premiere.json()["resultats"][0]
    assert lieu["nom"] == "Abomey-Calavi, Atlantique, Bénin"
    assert (lieu["lat"], lieu["lon"]) == (6.4485, 2.3557)
    assert lieu["bbox"] == pytest.approx(
        [2.3057, 6.3985, 2.4057, 6.4985]
    )  # [ouest, sud, est, nord]
    assert seconde.json() == premiere.json()
    assert len(faux_nominatim.appels) == 1, "seconde réponse servie par le cache"
    appel = faux_nominatim.appels[0]
    assert appel["user_agent"].startswith("suivi-chantier/")
    assert "countrycodes=bj" in appel["url"]


def test_recherche_trop_courte_sans_appel(navigateur, proprio, faux_nominatim):
    reponse = navigateur.get("/api/recherche-lieu", params={"q": "ab"})

    assert reponse.json() == {"resultats": []}
    assert faux_nominatim.appels == []


def test_recherche_exige_une_connexion(navigateur, faux_nominatim):
    assert navigateur.get("/api/recherche-lieu", params={"q": "Cotonou"}).status_code == 401
    assert faux_nominatim.appels == []


def test_recherche_indisponible_renvoie_502(navigateur, proprio, faux_nominatim):
    faux_nominatim.panne = TimeoutError("délai dépassé")

    assert navigateur.get("/api/recherche-lieu", params={"q": "Cotonou"}).status_code == 502


def test_une_requete_par_seconde_au_maximum(faux_nominatim):
    nominatim.rechercher("Cotonou")
    nominatim.rechercher("Porto-Novo")

    assert len(faux_nominatim.appels) == 2
    assert len(faux_nominatim.attentes) == 1
    assert 0.9 < faux_nominatim.attentes[0] <= 1.0
