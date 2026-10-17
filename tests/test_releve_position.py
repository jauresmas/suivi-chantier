"""Position du chantier : calcul du relevé, lien du proche, validation, verrouillage et
historique."""

import html
import math
import random
import re
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import cast, func, select

from app.auth.securite import empreinte
from app.geo.fiabilite import fiabilite, niveau_fiabilite
from app.geo.releve import MESURES_MIN, ReleveInvalide, calculer, lire_mesures
from app.geo.verification import formater_distance, verifier_photos
from app.models import (
    Chantier,
    HistoriquePositionChantier,
    MembreChantier,
    Photo,
    Rapport,
    RelevePosition,
    RoleMembre,
    SourcePosition,
    SourcePositionChantier,
    StatutPosition,
    StatutRapport,
    StatutReleve,
    StatutVerification,
    TypeReleveur,
)
from app.services.position import DUREE_LIEN, creer_releve
from tests.outils import (
    creer_utilisateur,
    image_jpeg,
    jeton_csrf,
    position_estimee,
    poster,
    se_connecter,
)

# Position estimée sur la carte par le propriétaire, et vraie position du terrain, 1 km plus au
# nord.
LON_ESTIME, LAT_ESTIME = 2.35, 6.45
LON_TERRAIN, LAT_TERRAIN = 2.35, 6.459
METRES_PAR_DEGRE = 111_195  # sur la sphère de rayon moyen terrestre
MOTIF_LIEN_RELEVE = re.compile(r"/releve/([A-Za-z0-9_\-]{43})")


# Les routes annulent leur transaction quand un lien est refusé (db.rollback()) : dans la session
# de test partagée, cela effacerait aussi des données seulement « flushées ». Les données préparées
# sont donc validées (commit) ; la transaction englobante est tout de même annulée en fin de test.


@pytest.fixture
def chantier(session):
    proprio = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    chef = creer_utilisateur(session, "chef@exemple.test", "Rodrigue Hounkpatin")
    autre = creer_utilisateur(session, "autre@exemple.test")
    chantier = Chantier(
        nom="Villa Calavi", **position_estimee(LON_ESTIME, LAT_ESTIME), rayon_verification_m=100
    )
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    session.add(chantier)
    session.commit()
    return SimpleNamespace(chantier=chantier, proprio=proprio, chef=chef, autre=autre)


def nouveau_lien(
    session, chantier, type_releveur=TypeReleveur.PROCHE
) -> tuple[RelevePosition, str]:
    releve, jeton = creer_releve(
        session, chantier.chantier, chantier.proprio, "Tante Afi", "tante", type_releveur
    )
    # Lien envoyé il y a une heure : les mesures simulées (la dernière minute) sont postérieures.
    releve.cree_le -= timedelta(hours=1)
    releve.expire_le -= timedelta(hours=1)
    session.commit()
    return releve, jeton


def decaler(lat: float, lon: float, nord_m: float, est_m: float) -> tuple[float, float]:
    return lat + nord_m / METRES_PAR_DEGRE, lon + est_m / (
        METRES_PAR_DEGRE * math.cos(math.radians(lat))
    )


def mesures_simulees(
    lat=LAT_TERRAIN, lon=LON_TERRAIN, n=60, ecart_m=4.0, aberrantes=0, imprecises=0, graine=1
) -> list[dict]:
    """Mesures d'un téléphone immobile : dispersion gaussienne autour du point, une par seconde,
    plus des valeurs aberrantes (à 150 m, annoncées à ±15 m) et des mesures imprécises (±80 m)."""
    hasard = random.Random(graine)
    total = n + aberrantes + imprecises
    debut_ms = time.time() * 1000 - (total + 2) * 1000
    mesures = []
    for i in range(total):
        if i < n:
            nord, est, precision = (
                hasard.gauss(0, ecart_m),
                hasard.gauss(0, ecart_m),
                hasard.uniform(4, 12),
            )
        elif i < n + aberrantes:
            angle = hasard.uniform(0, 2 * math.pi)
            nord, est, precision = 150 * math.cos(angle), 150 * math.sin(angle), 15.0
        else:
            nord, est, precision = 200.0, 0.0, 80.0
        lat_m, lon_m = decaler(lat, lon, nord, est)
        mesures.append(
            {"lat": lat_m, "lon": lon_m, "precision": precision, "t": debut_ms + i * 1000}
        )
    hasard.shuffle(mesures)  # l'ordre d'arrivée ne doit rien changer
    return mesures


def lire(mesures: list[dict], cree_le: datetime | None = None):
    maintenant = datetime.now(UTC)
    return lire_mesures(
        [(m["lat"], m["lon"], m["precision"], m["t"]) for m in mesures],
        maintenant.timestamp() * 1000,
        maintenant,
        cree_le or maintenant - timedelta(hours=1),
    )


def distance_m(session, lat1, lon1, lat2, lon2) -> float:
    return session.scalar(
        select(
            func.ST_Distance(
                func.ST_GeogFromText(f"SRID=4326;POINT({lon1} {lat1})"),
                func.ST_GeogFromText(f"SRID=4326;POINT({lon2} {lat2})"),
            )
        )
    )


def envoyer_mesures(client, jeton: str, mesures: list[dict], forcer: bool = False):
    """Envoi comme le fait releve.js : JSON, en-tête X-CSRF-Token, heure du téléphone."""
    return client.post(
        f"/releve/{jeton}/mesures",
        json={"mesures": mesures, "horloge": time.time() * 1000, "forcer": forcer},
        headers={
            "X-CSRF-Token": jeton_csrf(client),
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) Test",
        },
    )


def lat_lon(session, chantier_id) -> tuple[float, float]:
    geometrie = cast(Chantier.position, Geometry)
    return session.execute(
        select(func.ST_Y(geometrie), func.ST_X(geometrie)).where(Chantier.id == chantier_id)
    ).one()


def texte_visible(page: str) -> str:
    """Texte lu à l'écran : sans balises, scripts ni styles, espaces regroupées."""
    sans_scripts = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", sans_scripts)).split())


# --- Calcul côté serveur
# ---------------------------------------------------------------------------


def test_calcul_retrouve_le_point_a_moins_de_3_m_malgre_deux_valeurs_aberrantes(session):
    resultat = calculer(session, lire(mesures_simulees(n=60, aberrantes=2)))

    assert distance_m(session, resultat.latitude, resultat.longitude, LAT_TERRAIN, LON_TERRAIN) < 3
    assert resultat.nb_aberrantes == 2
    assert len(resultat.retenues) == 60
    assert 2 < resultat.precision_estimee_m < 8  # écart-type des positions, ~4 m × √2
    assert not resultat.signal_faible


def test_sans_filtre_les_valeurs_aberrantes_decaleraient_le_point(session):
    """Contre-épreuve : la moyenne simple, valeurs aberrantes comprises, s'écarte bien plus."""
    mesures = mesures_simulees(n=20, aberrantes=2)
    lat = sum(m["lat"] for m in mesures) / len(mesures)
    lon = sum(m["lon"] for m in mesures) / len(mesures)
    resultat = calculer(session, lire(mesures))

    assert distance_m(session, lat, lon, LAT_TERRAIN, LON_TERRAIN) > distance_m(
        session, resultat.latitude, resultat.longitude, LAT_TERRAIN, LON_TERRAIN
    )


def test_mesures_annoncees_a_plus_de_50_m_ignorees(session):
    resultat = calculer(session, lire(mesures_simulees(n=30, imprecises=10)))

    assert resultat.nb_imprecises == 10
    assert len(resultat.retenues) == 30
    assert all(m.precision_m <= 50 for m in resultat.retenues)
    assert distance_m(session, resultat.latitude, resultat.longitude, LAT_TERRAIN, LON_TERRAIN) < 3


def test_aucune_mesure_exploitable(session):
    assert calculer(session, lire(mesures_simulees(n=0, imprecises=12))) is None


def test_moins_de_10_bonnes_mesures_signal_faible(session):
    resultat = calculer(session, lire(mesures_simulees(n=MESURES_MIN - 2, imprecises=20)))

    assert len(resultat.retenues) == MESURES_MIN - 2
    assert resultat.signal_faible


def test_precision_finale_au_dela_de_25_m_signal_faible(session):
    resultat = calculer(session, lire(mesures_simulees(n=40, ecart_m=30, graine=3)))

    assert resultat.precision_estimee_m > 25
    assert resultat.signal_faible


def test_moyenne_ponderee_par_l_inverse_du_carre_de_la_precision(session):
    lat_a, lon_a = LAT_TERRAIN, LON_TERRAIN
    lat_b, lon_b = decaler(LAT_TERRAIN, LON_TERRAIN, 0, 1.5)  # 1,5 m plus à l'est : pas aberrant
    t = time.time() * 1000 - 20_000
    mesures = [{"lat": lat_a, "lon": lon_a, "precision": 5.0, "t": t + i * 1000} for i in range(4)]
    mesures += [
        {"lat": lat_b, "lon": lon_b, "precision": 10.0, "t": t + (4 + i) * 1000} for i in range(4)
    ]

    resultat = calculer(session, lire(mesures))

    # Poids 1/25 contre 1/100 : le point est aux 4/5 du chemin vers A.
    assert distance_m(
        session, resultat.latitude, resultat.longitude, lat_a, lon_a
    ) == pytest.approx(0.3, abs=0.01)


@pytest.mark.parametrize(
    "modifier, message",
    [
        (lambda m: m.update(lat=95), "hors des limites"),
        (lambda m: m.update(lon=-181), "hors des limites"),
        (lambda m: m.update(precision=0), "impossible"),
        (lambda m: m.update(t=time.time() * 1000 + 60_000), "après l'envoi"),
        (lambda m: m.update(t=time.time() * 1000 - 3 * 3600_000), "avant la création du lien"),
    ],
)
def test_mesures_incoherentes_refusees(modifier, message):
    mesures = mesures_simulees(n=20)
    modifier(mesures[0])

    with pytest.raises(ReleveInvalide, match=message):
        lire(mesures)


def test_trop_de_mesures_pour_la_duree_refusees():
    t = time.time() * 1000 - 5_000
    mesures = [
        {"lat": LAT_TERRAIN, "lon": LON_TERRAIN + i * 1e-7, "precision": 5.0, "t": t + i * 10}
        for i in range(100)
    ]

    with pytest.raises(ReleveInvalide, match="Trop de mesures pour la durée"):
        lire(mesures)


def test_horloge_du_telephone_dereglee_recalee():
    """Téléphone en retard de 3 jours : les horodatages sont recalés sur l'heure du serveur."""
    retard_ms = 3 * 24 * 3600_000
    mesures = [{**m, "t": m["t"] - retard_ms} for m in mesures_simulees(n=20)]
    maintenant = datetime.now(UTC)

    lues = lire_mesures(
        [(m["lat"], m["lon"], m["precision"], m["t"]) for m in mesures],
        maintenant.timestamp() * 1000 - retard_ms,
        maintenant,
        maintenant - timedelta(hours=1),
    )

    assert maintenant - timedelta(minutes=1) < lues[0].horodatage < maintenant


# --- Niveau de fiabilité
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statut, source, libelle, niveau",
    [
        (StatutPosition.NON_DEFINIE, None, "Position du chantier à définir", None),
        (
            StatutPosition.ESTIMEE,
            SourcePositionChantier.CARTE_PROPRIETAIRE,
            "Position estimée sur la carte",
            "Faible",
        ),
        (
            StatutPosition.A_VALIDER,
            SourcePositionChantier.CARTE_PROPRIETAIRE,
            "Relevé reçu — à valider",
            None,
        ),
        (StatutPosition.A_VALIDER, None, "Relevé reçu — à valider", None),
        (
            StatutPosition.CONFIRMEE,
            SourcePositionChantier.RELEVE_CHEF,
            "Relevé par le chef de chantier",
            "Moyen",
        ),
        (
            StatutPosition.CONFIRMEE,
            SourcePositionChantier.RELEVE_PROCHE,
            "Relevé sur place par un proche",
            "Bon",
        ),
        (
            StatutPosition.CONFIRMEE,
            SourcePositionChantier.BORNES_DOCUMENT,
            "Bornes du plan foncier",
            "Élevé",
        ),
        (
            StatutPosition.CONFIRMEE,
            SourcePositionChantier.GEOMETRE,
            "Levé par un géomètre",
            "Très élevé",
        ),
        # Combinaison inattendue : jamais mieux que « Faible ».
        (
            StatutPosition.CONFIRMEE,
            SourcePositionChantier.CARTE_PROPRIETAIRE,
            "Position estimée sur la carte",
            "Faible",
        ),
    ],
)
def test_niveau_de_fiabilite(statut, source, libelle, niveau):
    f = fiabilite(statut, source)

    assert (f.libelle, f.niveau) == (libelle, niveau)
    assert niveau_fiabilite(SimpleNamespace(statut_position=statut, source_position=source)) == f


def test_niveaux_ordonnes_du_moins_au_plus_fiable():
    rangs = [
        fiabilite(StatutPosition.CONFIRMEE, s).rang
        for s in (
            SourcePositionChantier.CARTE_PROPRIETAIRE,
            SourcePositionChantier.RELEVE_CHEF,
            SourcePositionChantier.RELEVE_PROCHE,
            SourcePositionChantier.BORNES_DOCUMENT,
            SourcePositionChantier.GEOMETRE,
        )
    ]

    assert rangs == sorted(rangs) == [1, 2, 3, 4, 5]


# --- Chantier sans position et vérification des photos (#6)
# -----------------------------------------


def test_creation_sans_position(navigateur, session):
    user = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    se_connecter(navigateur, user.email)

    reponse = poster(
        navigateur,
        "/chantiers/nouveau",
        {
            "nom": "Terrain de Ouidah",
            "pays": "BJ",
            "devise": "XOF",
            "statut": "en_preparation",
            "rayon_verification_m": "100",
        },
        page="/chantiers/nouveau",
    )

    assert reponse.status_code == 303
    chantier = session.scalar(select(Chantier).where(Chantier.nom == "Terrain de Ouidah"))
    assert chantier.position is None
    assert chantier.statut_position == StatutPosition.NON_DEFINIE
    assert session.scalar(select(func.count()).select_from(HistoriquePositionChantier)) == 0
    page = navigateur.get(reponse.headers["location"])
    assert page.status_code == 200
    assert "Position du chantier à définir" in page.text
    assert "Demander un relevé à un proche" in page.text
    assert navigateur.get("/api/chantiers.geojson").json()["features"] == []


def test_creation_avec_position_estimee_et_historisee(navigateur, session):
    user = creer_utilisateur(session, "proprio@exemple.test")
    se_connecter(navigateur, user.email)

    poster(
        navigateur,
        "/chantiers/nouveau",
        {
            "nom": "Villa",
            "pays": "BJ",
            "devise": "XOF",
            "statut": "en_cours",
            "rayon_verification_m": "100",
            "coordonnees": "6.45, 2.35",
        },
        page="/chantiers/nouveau",
    )

    chantier = session.scalar(select(Chantier).where(Chantier.nom == "Villa"))
    assert (chantier.statut_position, chantier.source_position) == (
        StatutPosition.ESTIMEE,
        SourcePositionChantier.CARTE_PROPRIETAIRE,
    )
    ligne = session.scalar(
        select(HistoriquePositionChantier).where(
            HistoriquePositionChantier.chantier_id == chantier.id
        )
    )
    assert (ligne.ancien_statut, ligne.nouveau_statut, ligne.ancienne_position) == (
        StatutPosition.NON_DEFINIE,
        StatutPosition.ESTIMEE,
        None,
    )
    assert ligne.modifie_par == user.id


def ajouter_photo_publiee(session, chantier, lat, lon) -> Photo:
    rapport = Rapport(
        chantier_id=chantier.chantier.id,
        auteur_id=chantier.chef.id,
        statut=StatutRapport.PUBLIE,
        publie_le=datetime.now(UTC),
    )
    photo = Photo(
        rapport=rapport,
        chemin_fichier="test.jpg",
        position=WKTElement(f"POINT({lon} {lat})", srid=4326),
        source_position=SourcePosition.NAVIGATEUR,
        precision_m=8,
    )
    session.add_all([rapport, photo])
    session.flush()
    verifier_photos(session, photo_id=photo.id)
    session.commit()
    return photo


def test_position_non_definie_toutes_les_photos_non_verifiables(session):
    chantier = Chantier(nom="Sans position")
    chef = creer_utilisateur(session, "chef@exemple.test")
    session.add(chantier)
    session.flush()

    photo = ajouter_photo_publiee(
        session, SimpleNamespace(chantier=chantier, chef=chef), LAT_TERRAIN, LON_TERRAIN
    )

    assert photo.statut_verification == StatutVerification.NON_VERIFIABLE
    assert photo.distance_chantier_m is None


def test_position_estimee_calcul_fait_avec_mention_non_confirmee(navigateur, session, chantier):
    photo = ajouter_photo_publiee(session, chantier, LAT_ESTIME, LON_ESTIME)
    se_connecter(navigateur, chantier.chef.email)

    proprietes = navigateur.get(f"/api/chantiers/{chantier.chantier.id}/photos.geojson").json()[
        "features"
    ][0]["properties"]

    assert photo.statut_verification == StatutVerification.VERIFIEE
    assert proprietes["mention"] == "Position du chantier non confirmée"
    assert (
        "Position du chantier non confirmée" in navigateur.get(f"/rapports/{photo.rapport_id}").text
    )


# --- Lien du proche
# --------------------------------------------------------------------------------


def test_parcours_proprietaire_lien_whatsapp_hache_en_base(navigateur, session, chantier):
    se_connecter(navigateur, chantier.proprio.email)
    page = navigateur.get(f"/chantiers/{chantier.chantier.id}/position/demander")
    assert "pas à votre chef de chantier" in html.unescape(page.text)

    reponse = poster(
        navigateur,
        f"/chantiers/{chantier.chantier.id}/position/releves",
        {"nom_releveur": "Tante Afi", "lien_releveur": "tante"},
        page="/chantiers",
    )

    assert reponse.status_code == 200
    jeton = MOTIF_LIEN_RELEVE.search(reponse.text).group(1)
    assert "https://wa.me/?text=Bonjour%2C%20peux-tu%20m%27aider" in reponse.text
    assert "%C3%A7a%20prend%202%20minutes" in reponse.text  # « ça prend 2 minutes »
    releve = session.scalar(
        select(RelevePosition).where(RelevePosition.chantier_id == chantier.chantier.id)
    )
    assert releve.empreinte_jeton == empreinte(jeton)
    assert jeton not in releve.empreinte_jeton
    assert releve.expire_le - releve.cree_le == DUREE_LIEN == timedelta(hours=72)
    assert (releve.nom_releveur, releve.lien_releveur, releve.statut) == (
        "Tante Afi",
        "tante",
        StatutReleve.EN_ATTENTE,
    )


def test_un_seul_releve_en_attente_par_chantier(navigateur, session, chantier):
    nouveau_lien(session, chantier)
    se_connecter(navigateur, chantier.proprio.email)

    reponse = poster(
        navigateur,
        f"/chantiers/{chantier.chantier.id}/position/releves",
        {"nom_releveur": "Cousin Koffi"},
        page="/chantiers",
    )

    assert reponse.status_code == 409
    assert "déjà en attente" in html.unescape(reponse.text)


def test_la_page_du_proche_fonctionne_sans_etre_connecte(navigateur, session, chantier):
    _, jeton = nouveau_lien(session, chantier)

    page = navigateur.get(f"/releve/{jeton}")

    assert page.status_code == 200
    texte = texte_visible(page.text)
    assert "Mireille Adjovi vous demande de l'aider à localiser son terrain" in texte
    assert "Villa Calavi" in texte
    assert "Je suis sur le terrain" in texte
    assert "Se connecter" not in texte  # aucun lien vers un compte
    assert '<meta name="csrf-token" content="' in page.text  # pour l'envoi des mesures (releve.js)
    for jargon in ("GPS", "précision", "coordonnées"):
        assert jargon.lower() not in texte.lower(), jargon


def test_releve_par_le_proche_sans_compte(navigateur, session, chantier):
    releve, jeton = nouveau_lien(session, chantier)

    reponse = envoyer_mesures(navigateur, jeton, mesures_simulees(aberrantes=2))

    assert reponse.status_code == 201
    session.refresh(releve)
    session.refresh(chantier.chantier)
    assert releve.statut == StatutReleve.RECU
    assert releve.realise_par is None and releve.alertes == []
    assert releve.nb_mesures == 60
    assert "Android" in releve.appareil
    assert chantier.chantier.statut_position == StatutPosition.A_VALIDER
    # La position du chantier ne change qu'à la validation.
    assert lat_lon(session, chantier.chantier.id) == pytest.approx((LAT_ESTIME, LON_ESTIME))


def test_points_bruts_et_position_resultat_coherents(navigateur, session, chantier):
    """Ce que montrera QGIS : le résultat est au cœur du nuage des mesures retenues."""
    releve, jeton = nouveau_lien(session, chantier)
    envoyer_mesures(navigateur, jeton, mesures_simulees(aberrantes=2))

    nombre, ecart_centroide, distance_max = session.execute(
        select(
            func.ST_NumGeometries(cast(RelevePosition.points_bruts, Geometry)),
            func.ST_Distance(
                RelevePosition.position_resultat, func.ST_Centroid(RelevePosition.points_bruts)
            ),
            func.ST_MaxDistance(
                cast(RelevePosition.position_resultat, Geometry),
                cast(RelevePosition.points_bruts, Geometry),
            ),
        ).where(RelevePosition.id == releve.id)
    ).one()

    assert nombre == releve.nb_mesures == 60
    assert ecart_centroide < 2
    assert distance_max * METRES_PAR_DEGRE < 50  # les deux valeurs à 150 m ont été écartées


def test_lien_a_usage_unique(navigateur, session, chantier):
    _, jeton = nouveau_lien(session, chantier)
    assert envoyer_mesures(navigateur, jeton, mesures_simulees()).status_code == 201

    second_envoi = envoyer_mesures(navigateur, jeton, mesures_simulees(graine=2))
    page = navigateur.get(f"/releve/{jeton}")

    assert second_envoi.status_code == 410
    assert page.status_code == 410
    assert "Ce lien a déjà servi" in html.unescape(page.text)


def test_lien_expire_apres_72_h(navigateur, session, chantier):
    releve, jeton = nouveau_lien(session, chantier)
    releve.expire_le = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    page = navigateur.get(f"/releve/{jeton}")
    envoi = envoyer_mesures(navigateur, jeton, mesures_simulees())

    assert page.status_code == 410
    assert "Ce lien a expiré" in html.unescape(page.text)
    assert envoi.status_code == 410
    # Le propriétaire peut en créer un nouveau : l'ancien est alors marqué « expiré ».
    nouveau, _ = nouveau_lien(session, chantier)
    session.refresh(releve)
    assert releve.statut == StatutReleve.EXPIRE
    assert nouveau.statut == StatutReleve.EN_ATTENTE


def test_annulation_du_lien(navigateur, session, chantier):
    releve, jeton = nouveau_lien(session, chantier)
    se_connecter(navigateur, chantier.proprio.email)

    reponse = poster(
        navigateur,
        f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/annuler",
        page="/chantiers",
    )
    proche = navigateur.get(f"/releve/{jeton}")

    assert reponse.status_code == 303
    session.refresh(releve)
    assert releve.statut == StatutReleve.ANNULE
    assert proche.status_code == 410
    assert "Ce lien n'est plus valable" in html.unescape(proche.text)
    assert "Mireille Adjovi l'a annulé" in html.unescape(proche.text)


def test_lien_inconnu(navigateur):
    page = navigateur.get("/releve/" + "x" * 43)

    assert page.status_code == 404
    assert "Ce lien ne fonctionne pas" in page.text


def test_signal_faible_rien_enregistre_puis_envoi_force(navigateur, session, chantier):
    releve, jeton = nouveau_lien(session, chantier)
    faibles = mesures_simulees(n=6, imprecises=20)

    premier = envoyer_mesures(navigateur, jeton, faibles)
    assert premier.status_code == 200
    assert premier.json() == {"statut": "signal_faible", "exploitable": True}
    session.refresh(releve)
    assert releve.statut == StatutReleve.EN_ATTENTE  # le proche peut recommencer

    force = envoyer_mesures(navigateur, jeton, faibles, forcer=True)

    assert force.status_code == 201
    session.refresh(releve)
    assert releve.statut == StatutReleve.RECU
    assert releve.signal_faible


def test_aucune_mesure_exploitable_meme_en_forcant(navigateur, session, chantier):
    _, jeton = nouveau_lien(session, chantier)

    reponse = envoyer_mesures(navigateur, jeton, mesures_simulees(n=0, imprecises=15), forcer=True)

    assert reponse.json() == {"statut": "signal_faible", "exploitable": False}


def test_mesures_incoherentes_400(navigateur, session, chantier):
    _, jeton = nouveau_lien(session, chantier)
    mesures = mesures_simulees()
    mesures[0]["t"] = time.time() * 1000 + 3600_000

    assert envoyer_mesures(navigateur, jeton, mesures).status_code == 400


def test_photos_du_proche_visibles_par_le_proprietaire_seulement(navigateurs, session, chantier):
    proche, proprio, chef = navigateurs(), navigateurs(), navigateurs()
    releve, jeton = nouveau_lien(session, chantier)
    envoyer_mesures(proche, jeton, mesures_simulees())
    csrf = jeton_csrf(proche)

    envois = [
        proche.post(
            f"/releve/{jeton}/photos",
            files={"fichier": ("p.jpg", image_jpeg(), "image/jpeg")},
            headers={"X-CSRF-Token": csrf},
        )
        for _ in range(4)
    ]

    assert [e.status_code for e in envois] == [201, 201, 201, 409]  # 3 photos au plus
    session.refresh(releve)
    photo = releve.photos[0]
    url = f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/photos/{photo.id}"
    se_connecter(proprio, chantier.proprio.email)
    se_connecter(chef, chantier.chef.email)
    assert (
        url in proprio.get(f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}").text
    )
    assert proprio.get(url).headers["content-type"] == "image/jpeg"
    assert proprio.get(url + "?miniature=true").status_code == 200
    assert chef.get(url).status_code == 403
    assert (
        proche.get(url, follow_redirects=False).status_code == 303
    )  # pas de compte : connexion demandée


# --- Validation, rejet et verrouillage
# ----------------------------------------------------------------


def releve_recu(navigateur, session, chantier, **options) -> RelevePosition:
    releve, jeton = nouveau_lien(session, chantier)
    assert envoyer_mesures(navigateur, jeton, mesures_simulees(**options)).status_code == 201
    session.refresh(releve)
    return releve


def test_validation_position_confirmee_historique_et_photos_recalculees(
    navigateurs, session, chantier
):
    proche, proprio = navigateurs(), navigateurs()
    photo = ajouter_photo_publiee(session, chantier, *decaler(LAT_TERRAIN, LON_TERRAIN, 20, 0))
    assert photo.statut_verification == StatutVerification.HORS_ZONE  # 1 km de la position estimée
    releve = releve_recu(proche, session, chantier)
    se_connecter(proprio, chantier.proprio.email)

    page = proprio.get(f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}")
    assert page.status_code == 200
    assert "Valider cette position" in page.text
    reponse = poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/valider",
        page="/chantiers",
    )

    assert reponse.status_code == 303
    session.refresh(chantier.chantier)
    session.refresh(releve)
    session.refresh(photo)
    c = chantier.chantier
    assert (c.statut_position, c.source_position) == (
        StatutPosition.CONFIRMEE,
        SourcePositionChantier.RELEVE_PROCHE,
    )
    assert c.position_confirmee_le is not None
    assert distance_m(session, *lat_lon(session, c.id), LAT_TERRAIN, LON_TERRAIN) < 3
    assert (releve.statut, releve.traite_par) == (StatutReleve.VALIDE, chantier.proprio.id)
    ligne = session.scalar(
        select(HistoriquePositionChantier).where(HistoriquePositionChantier.releve_id == releve.id)
    )
    assert (ligne.ancien_statut, ligne.nouveau_statut, ligne.source) == (
        StatutPosition.ESTIMEE,
        StatutPosition.CONFIRMEE,
        SourcePositionChantier.RELEVE_PROCHE,
    )
    deplacement = session.scalar(
        select(
            func.ST_Distance(
                HistoriquePositionChantier.ancienne_position,
                HistoriquePositionChantier.nouvelle_position,
            )
        ).where(HistoriquePositionChantier.id == ligne.id)
    )
    assert deplacement == pytest.approx(
        distance_m(session, LAT_ESTIME, LON_ESTIME, LAT_TERRAIN, LON_TERRAIN), abs=5
    )
    assert (
        photo.statut_verification == StatutVerification.VERIFIEE
    )  # recalculée avec la nouvelle référence
    assert niveau_fiabilite(c).libelle == "Relevé sur place par un proche"
    tableau = proprio.get(reponse.headers["location"]).text
    assert "Relevé sur place par un proche" in tableau
    historique = html.unescape(proprio.get(f"/chantiers/{c.id}/position").text)
    assert "Estimée → Confirmée" in historique
    assert "relevé de Tante Afi" in historique
    assert f"déplacée de {formater_distance(deplacement)}" in texte_visible(historique)


def test_rejet_la_position_ne_change_pas(navigateurs, session, chantier):
    proche, proprio = navigateurs(), navigateurs()
    releve = releve_recu(proche, session, chantier)
    se_connecter(proprio, chantier.proprio.email)

    reponse = poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/rejeter",
        {"motif": "Ce n'est pas mon terrain"},
        page="/chantiers",
    )

    assert reponse.status_code == 303
    session.refresh(chantier.chantier)
    session.refresh(releve)
    assert lat_lon(session, chantier.chantier.id) == pytest.approx((LAT_ESTIME, LON_ESTIME))
    assert chantier.chantier.statut_position == StatutPosition.ESTIMEE
    assert (releve.statut, releve.motif_rejet) == (StatutReleve.REJETE, "Ce n'est pas mon terrain")
    assert session.scalar(select(func.count()).select_from(HistoriquePositionChantier)) == 0
    # Un relevé traité ne peut plus être validé.
    second = poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/valider",
        page="/chantiers",
    )
    assert second.status_code == 409


def test_chef_de_chantier_ne_peut_ni_changer_la_position_ni_valider(navigateurs, session, chantier):
    proche, chef, autre = navigateurs(), navigateurs(), navigateurs()
    releve = releve_recu(proche, session, chantier)
    se_connecter(chef, chantier.chef.email)
    se_connecter(autre, chantier.autre.email)
    base = f"/chantiers/{chantier.chantier.id}"

    assert chef.get(f"{base}/position").status_code == 403
    assert chef.get(f"{base}/position/demander").status_code == 403
    assert chef.get(f"{base}/position/releves/{releve.id}").status_code == 403
    for url in (
        f"{base}/position/releves/{releve.id}/valider",
        f"{base}/position/releves/{releve.id}/rejeter",
        f"{base}/position/releves",
        f"{base}/modifier",
    ):
        assert (
            poster(
                chef,
                url,
                {"nom": "x", "coordonnees": "6.5, 2.4", "nom_releveur": "Moi"},
                page="/chantiers",
            ).status_code
            == 403
        ), url
        assert poster(autre, url, {"nom": "x"}, page="/chantiers").status_code == 404, url
    session.refresh(releve)
    assert releve.statut == StatutReleve.RECU
    assert lat_lon(session, chantier.chantier.id) == pytest.approx((LAT_ESTIME, LON_ESTIME))
    # Le chef voit l'état de la position, sans aucune action.
    tableau = chef.get(base).text
    assert "Position du chantier" in tableau
    assert "/position/demander" not in tableau and "Vérifier le relevé" not in tableau


def test_chef_connecte_sur_un_lien_de_proche_alerte_le_proprietaire(navigateurs, session, chantier):
    chef, proprio = navigateurs(), navigateurs()
    releve, jeton = nouveau_lien(session, chantier)
    se_connecter(chef, chantier.chef.email)

    assert envoyer_mesures(chef, jeton, mesures_simulees()).status_code == 201

    session.refresh(releve)
    assert releve.alertes == ["chef_chantier_connecte"]
    assert releve.realise_par == chantier.chef.id
    se_connecter(proprio, chantier.proprio.email)
    page = html.unescape(
        proprio.get(f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}").text
    )
    assert "depuis le compte du chef de chantier (Rodrigue Hounkpatin)" in page
    # Validé quand même, il ne compte que comme un relevé du chef.
    poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/valider",
        page="/chantiers",
    )
    session.refresh(chantier.chantier)
    assert chantier.chantier.source_position == SourcePositionChantier.RELEVE_CHEF
    assert niveau_fiabilite(chantier.chantier).niveau == "Moyen"


def test_avertissement_si_le_releve_est_a_plus_de_500_m(navigateurs, session, chantier):
    proche, proprio = navigateurs(), navigateurs()
    lat_loin, lon_loin = decaler(LAT_ESTIME, LON_ESTIME, 1800, 1430)  # ~2,3 km
    releve = releve_recu(proche, session, chantier, lat=lat_loin, lon=lon_loin)
    se_connecter(proprio, chantier.proprio.email)

    page = html.unescape(
        proprio.get(f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}").text
    )

    assert (
        "Le relevé est à 2,3 km de la position que vous aviez indiquée. Vérifiez avant de valider."
        in page
    )


def test_pas_d_avertissement_sous_500_m(navigateurs, session, chantier):
    proche, proprio = navigateurs(), navigateurs()
    releve = releve_recu(proche, session, chantier, lat=LAT_ESTIME, lon=LON_ESTIME)
    se_connecter(proprio, chantier.proprio.email)

    page = html.unescape(
        proprio.get(f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}").text
    )

    assert "Vérifiez avant de valider" not in page


def test_notification_du_proprietaire_sur_le_tableau_de_bord(navigateurs, session, chantier):
    proche, proprio, chef = navigateurs(), navigateurs(), navigateurs()
    releve_recu(proche, session, chantier)
    se_connecter(proprio, chantier.proprio.email)
    se_connecter(chef, chantier.chef.email)

    assert "Relevé reçu — à valider" in proprio.get(f"/chantiers/{chantier.chantier.id}").text
    assert "Relevé reçu — à valider" in proprio.get("/chantiers").text
    assert "Tante Afi a relevé" not in chef.get(f"/chantiers/{chantier.chantier.id}").text


def confirmer_position(session, chantier):
    chantier.chantier.statut_position = StatutPosition.CONFIRMEE
    chantier.chantier.source_position = SourcePositionChantier.RELEVE_PROCHE
    chantier.chantier.position_confirmee_le = datetime.now(UTC)
    session.commit()


FORMULAIRE = {
    "nom": "Villa Calavi",
    "pays": "BJ",
    "devise": "XOF",
    "statut": "en_cours",
    "rayon_verification_m": "100",
}


def test_deplacer_une_position_confirmee_demande_une_confirmation(navigateur, session, chantier):
    confirmer_position(session, chantier)
    se_connecter(navigateur, chantier.proprio.email)
    url = f"/chantiers/{chantier.chantier.id}/modifier"

    sans_confirmation = poster(
        navigateur, url, {**FORMULAIRE, "coordonnees": "6.4509, 2.35"}, page="/chantiers"
    )

    assert sans_confirmation.status_code == 409
    assert "Confirmez le déplacement de la position" in texte_visible(sans_confirmation.text)
    assert "Vous la déplacez de 100 m" in texte_visible(sans_confirmation.text)
    session.refresh(chantier.chantier)
    assert chantier.chantier.statut_position == StatutPosition.CONFIRMEE
    assert lat_lon(session, chantier.chantier.id) == pytest.approx((LAT_ESTIME, LON_ESTIME))

    avec_confirmation = poster(
        navigateur,
        url,
        {**FORMULAIRE, "coordonnees": "6.4509, 2.35", "confirmer_deplacement": "1"},
        page="/chantiers",
    )

    assert avec_confirmation.status_code == 303
    session.refresh(chantier.chantier)
    assert (chantier.chantier.statut_position, chantier.chantier.source_position) == (
        StatutPosition.ESTIMEE,
        SourcePositionChantier.CARTE_PROPRIETAIRE,
    )
    assert chantier.chantier.position_confirmee_le is None
    ligne = session.scalar(select(HistoriquePositionChantier))
    assert (ligne.ancien_statut, ligne.nouveau_statut) == (
        StatutPosition.CONFIRMEE,
        StatutPosition.ESTIMEE,
    )


def test_modifier_le_nom_sans_toucher_la_position_ne_demande_rien(navigateur, session, chantier):
    confirmer_position(session, chantier)
    se_connecter(navigateur, chantier.proprio.email)

    # Position renvoyée telle que préremplie (arrondie à 6 décimales) : elle n'a pas bougé.
    reponse = poster(
        navigateur,
        f"/chantiers/{chantier.chantier.id}/modifier",
        {**FORMULAIRE, "nom": "Villa Calavi (R+2)", "coordonnees": "6.450000, 2.350000"},
        page="/chantiers",
    )

    assert reponse.status_code == 303
    session.refresh(chantier.chantier)
    assert chantier.chantier.statut_position == StatutPosition.CONFIRMEE
    assert session.scalar(select(func.count()).select_from(HistoriquePositionChantier)) == 0


def test_valider_un_releve_sur_une_position_confirmee_demande_une_confirmation(
    navigateurs, session, chantier
):
    proche, proprio = navigateurs(), navigateurs()
    confirmer_position(session, chantier)
    releve = releve_recu(proche, session, chantier)
    session.refresh(chantier.chantier)
    assert (
        chantier.chantier.statut_position == StatutPosition.CONFIRMEE
    )  # reste la référence en attendant
    se_connecter(proprio, chantier.proprio.email)
    url = f"/chantiers/{chantier.chantier.id}/position/releves/{releve.id}/valider"

    assert poster(proprio, url, page="/chantiers").status_code == 400
    assert (
        poster(proprio, url, {"confirmer_remplacement": "1"}, page="/chantiers").status_code == 303
    )
    session.refresh(releve)
    assert releve.statut == StatutReleve.VALIDE


def test_deplacement_sur_la_carte_bloque_pendant_un_releve_a_valider(
    navigateurs, session, chantier
):
    proche, proprio = navigateurs(), navigateurs()
    releve_recu(proche, session, chantier)
    se_connecter(proprio, chantier.proprio.email)

    reponse = poster(
        proprio,
        f"/chantiers/{chantier.chantier.id}/modifier",
        {**FORMULAIRE, "coordonnees": "6.46, 2.36"},
        page="/chantiers",
    )

    assert reponse.status_code == 400
    assert "Un relevé attend votre validation" in html.unescape(reponse.text)


def test_jeton_de_releve_masque_dans_les_journaux():
    from app.journalisation import masquer_jetons

    assert (
        masquer_jetons('"POST /releve/AbC-123_xyz/mesures HTTP/1.1" 201')
        == '"POST /releve/***/mesures HTTP/1.1" 201'
    )
