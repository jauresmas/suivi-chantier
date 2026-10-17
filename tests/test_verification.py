from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select

from app import taches
from app.geo.verification import (
    LIBELLES_ALERTES,
    LIBELLES_STATUT,
    badge_verification,
    formater_distance,
    verifier_photos,
)
from app.models import (
    Chantier,
    MembreChantier,
    Photo,
    Rapport,
    RoleMembre,
    SourcePosition,
    StatutRapport,
    StatutVerification,
)
from tests.outils import creer_utilisateur, position_confirmee, poster, se_connecter

CHANTIER_WKT = "POINT(2.35 6.45)"
MAINTENANT = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


@pytest.fixture
def chantier(session):
    proprio = creer_utilisateur(session, "proprio@exemple.test")
    chef = creer_utilisateur(session, "chef@exemple.test")
    chantier = Chantier(
        nom="Villa Calavi", **position_confirmee(2.35, 6.45), rayon_verification_m=100
    )
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    # Rapport publié : ses photos sont visibles de tous les membres.
    rapport = Rapport(
        chantier=chantier, auteur_id=chef.id, statut=StatutRapport.PUBLIE, publie_le=MAINTENANT
    )
    session.add_all([chantier, rapport])
    session.flush()
    return SimpleNamespace(chantier=chantier, rapport=rapport, proprio=proprio, chef=chef)


def projeter(
    session, distance_m: float, azimut_deg: float = 45, depuis: str = CHANTIER_WKT
) -> WKTElement:
    """Point situé exactement à `distance_m` mètres de `depuis`, dans la direction `azimut_deg`."""
    wkt = session.scalar(
        select(
            func.ST_AsText(
                func.ST_Project(
                    func.ST_GeogFromText(f"SRID=4326;{depuis}"),
                    distance_m,
                    func.radians(azimut_deg),
                )
            )
        )
    )
    return WKTElement(wkt, srid=4326)


def ajouter_photo(session, chantier, distance_m=None, precision_m=None, **champs) -> Photo:
    photo = Photo(
        rapport_id=chantier.rapport.id,
        chemin_fichier="test.jpg",
        position=projeter(session, distance_m) if distance_m is not None else None,
        source_position=champs.pop(
            "source_position",
            SourcePosition.NAVIGATEUR if distance_m is not None else SourcePosition.AUCUNE,
        ),
        precision_m=precision_m,
        envoyee_le=champs.pop("envoyee_le", MAINTENANT),
        **champs,
    )
    session.add(photo)
    session.flush()
    return photo


def verifier(session, photo) -> Photo:
    verifier_photos(session, photo_id=photo.id)
    session.refresh(photo)
    return photo


# --- Statut ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "distance_m, precision_m, statut_attendu",
    [
        (30, None, StatutVerification.VERIFIEE),
        (140, 50, StatutVerification.VERIFIEE),  # tolérance = min(50, 50)
        (160, 50, StatutVerification.HORS_ZONE),
        (140, None, StatutVerification.HORS_ZONE),  # précision inconnue : aucune tolérance
        (140, 80, StatutVerification.VERIFIEE),  # tolérance plafonnée à 50 m : 140 <= 150
        (160, 80, StatutVerification.HORS_ZONE),
        (30, 250, StatutVerification.NON_VERIFIABLE),  # signal trop faible pour conclure
    ],
)
def test_statut_selon_distance_et_precision(
    session, chantier, distance_m, precision_m, statut_attendu
):
    photo = verifier(session, ajouter_photo(session, chantier, distance_m, precision_m))

    assert photo.statut_verification == statut_attendu
    assert photo.distance_chantier_m == distance_m  # arrondie au mètre
    assert photo.verifiee_le is not None


def test_sans_position_non_verifiable(session, chantier):
    photo = verifier(session, ajouter_photo(session, chantier))

    assert photo.statut_verification == StatutVerification.NON_VERIFIABLE
    assert photo.distance_chantier_m is None


def test_photo_dans_l_emprise_du_chantier_a_distance_nulle(session, chantier):
    # Parcelle d'environ 220 m de côté centrée sur le chantier ; la photo est à 90 m du point, dans
    # le polygone.
    chantier.chantier.emprise = WKTElement(
        "POLYGON((2.349 6.449, 2.351 6.449, 2.351 6.451, 2.349 6.451, 2.349 6.449))", srid=4326
    )
    photo_dedans = ajouter_photo(session, chantier, 90)
    photo_dehors = ajouter_photo(session, chantier, 400)

    verifier_photos(session, chantier_id=chantier.chantier.id)
    session.refresh(photo_dedans)
    session.refresh(photo_dehors)

    assert photo_dedans.distance_chantier_m == 0
    assert photo_dedans.statut_verification == StatutVerification.VERIFIEE
    # Mesurée jusqu'au bord de la parcelle, et non plus jusqu'au point : nettement moins de 400 m.
    assert 200 < photo_dehors.distance_chantier_m < 400


def test_changement_du_rayon_reverifie_les_photos(navigateur, session, chantier):
    photo = verifier(session, ajouter_photo(session, chantier, 160, 50))
    assert photo.statut_verification == StatutVerification.HORS_ZONE
    se_connecter(navigateur, chantier.proprio.email)

    reponse = poster(
        navigateur,
        f"/chantiers/{chantier.chantier.id}/modifier",
        {
            "nom": "Villa Calavi",
            "pays": "BJ",
            "devise": "XOF",
            "statut": "en_cours",
            "rayon_verification_m": "200",
            "coordonnees": "6.45, 2.35",
        },
        page="/chantiers",
    )

    assert reponse.status_code == 303
    session.refresh(photo)
    assert photo.statut_verification == StatutVerification.VERIFIEE
    assert "Sur le chantier" in navigateur.get(f"/chantiers/{chantier.chantier.id}").text


def test_deplacement_du_chantier_reverifie_les_photos(navigateur, session, chantier):
    photo = verifier(session, ajouter_photo(session, chantier, 30))
    se_connecter(navigateur, chantier.proprio.email)

    # Le chantier est déplacé d'environ 1 km vers le nord.
    poster(
        navigateur,
        f"/chantiers/{chantier.chantier.id}/modifier",
        {
            "nom": "Villa Calavi",
            "pays": "BJ",
            "devise": "XOF",
            "statut": "en_cours",
            "rayon_verification_m": "100",
            "coordonnees": "6.459, 2.35",
            "confirmer_deplacement": "1",
        },
        page="/chantiers",
    )

    session.refresh(photo)
    assert photo.statut_verification == StatutVerification.HORS_ZONE
    assert photo.distance_chantier_m > 900


# --- Signaux de confiance -------------------------------------------------------------------------


def test_doublon_signale_sur_la_seconde_photo(session, chantier):
    empreinte = "a" * 64
    premiere = ajouter_photo(
        session, chantier, 30, empreinte_originale=empreinte, envoyee_le=MAINTENANT
    )
    seconde = ajouter_photo(
        session,
        chantier,
        30,
        empreinte_originale=empreinte,
        envoyee_le=MAINTENANT + timedelta(hours=1),
    )

    verifier_photos(session, chantier_id=chantier.chantier.id)
    session.refresh(premiere)
    session.refresh(seconde)

    assert "doublon" not in premiere.alertes
    assert "doublon" in seconde.alertes


def test_deplacement_impossible_50_km_en_10_minutes(session, chantier):
    ajouter_photo(session, chantier, 30, position_obtenue_le=MAINTENANT)
    loin = ajouter_photo(
        session, chantier, 50_000, position_obtenue_le=MAINTENANT + timedelta(minutes=10)
    )

    assert "deplacement_impossible" in verifier(session, loin).alertes


def test_deplacement_plausible_non_signale(session, chantier):
    ajouter_photo(session, chantier, 30, position_obtenue_le=MAINTENANT)
    # 50 km en 1 h : 50 km/h.
    loin = ajouter_photo(
        session, chantier, 50_000, position_obtenue_le=MAINTENANT + timedelta(hours=1)
    )

    assert "deplacement_impossible" not in verifier(session, loin).alertes


def test_ecart_inferieur_a_une_minute_ignore(session, chantier):
    ajouter_photo(session, chantier, 30, position_obtenue_le=MAINTENANT)
    loin = ajouter_photo(
        session, chantier, 5_000, position_obtenue_le=MAINTENANT + timedelta(seconds=40)
    )

    assert "deplacement_impossible" not in verifier(session, loin).alertes


def test_photo_ancienne_exif_de_3_jours(session, chantier):
    photo = verifier(
        session, ajouter_photo(session, chantier, 30, prise_le=MAINTENANT - timedelta(days=3))
    )

    assert "photo_ancienne" in photo.alertes


def test_position_ancienne(session, chantier):
    photo = ajouter_photo(
        session,
        chantier,
        30,
        prise_le=MAINTENANT,
        position_obtenue_le=MAINTENANT - timedelta(minutes=10),
    )

    assert "position_ancienne" in verifier(session, photo).alertes


def test_precision_faible_et_position_exif(session, chantier):
    photo = ajouter_photo(session, chantier, 30, 150, source_position=SourcePosition.EXIF)

    alertes = verifier(session, photo).alertes

    assert "precision_faible" in alertes
    assert "position_exif" in alertes


def test_photo_ordinaire_sans_alerte(session, chantier):
    photo = ajouter_photo(
        session,
        chantier,
        30,
        8,
        prise_le=MAINTENANT - timedelta(minutes=2),
        position_obtenue_le=MAINTENANT - timedelta(minutes=3),
    )

    assert verifier(session, photo).alertes == []


# --- Déclenchement --------------------------------------------------------------------------------


def test_commande_recalculer_verifications(session, chantier, monkeypatch, capsys):
    photo = ajouter_photo(session, chantier, 30)
    assert photo.verifiee_le is None
    monkeypatch.setattr(taches, "SessionLocal", lambda: nullcontext(session))

    taches.main(["recalculer_verifications", "--chantier", str(chantier.chantier.id)])

    session.refresh(photo)
    assert photo.statut_verification == StatutVerification.VERIFIEE
    assert "1 photo vérifiée." in capsys.readouterr().out


# --- Affichage ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metres, texte",
    [
        (35, "35 m"),
        (999.4, "999 m"),
        (999.6, "1,0 km"),
        (1830, "1,8 km"),
        (12_345, "12,3 km"),
        (99_960, "100 km"),
        (4_653_259, "4 653 km"),
    ],
)
def test_distance_au_format_francais(metres, texte):
    assert formater_distance(metres) == texte


def test_badges():
    assert badge_verification(StatutVerification.VERIFIEE, 35, []).texte == "Sur le chantier"
    assert (
        badge_verification(StatutVerification.HORS_ZONE, 1830, []).texte == "Hors zone — à 1,8 km"
    )
    assert badge_verification(StatutVerification.NON_VERIFIABLE, None, []).texte == "Non vérifiable"
    # position_exif est une information : pas d'icône d'avertissement.
    assert not badge_verification(StatutVerification.VERIFIEE, 35, ["position_exif"]).avertissement
    assert badge_verification(StatutVerification.VERIFIEE, 35, ["doublon"]).avertissement


def test_page_du_chantier_affiche_badges_et_indicateur(navigateur, session, chantier):
    for distance in (30, 40, 2000):
        ajouter_photo(session, chantier, distance, envoyee_le=datetime.now(UTC) - timedelta(days=1))
    ajouter_photo(
        session, chantier, 30, envoyee_le=datetime.now(UTC) - timedelta(days=45)
    )  # trop ancienne
    verifier_photos(session, chantier_id=chantier.chantier.id)
    se_connecter(navigateur, chantier.chef.email)

    page = navigateur.get(f"/chantiers/{chantier.chantier.id}").text

    assert "Photos vérifiées" in page
    assert ">67 %</p>" in page
    assert "2 sur 3 photos vérifiables · 30 j" in page
    assert "Sur le chantier" in page
    assert "Hors zone — à 2,0 km" in page


def test_aucun_mot_de_garantie_dans_l_interface():
    # Une position GPS peut être falsifiée : l'interface ne promet jamais de preuve.
    dossier_app = Path(__file__).resolve().parent.parent / "app"
    textes = {
        **{
            str(p): p.read_text(encoding="utf-8")
            for p in (dossier_app / "templates").rglob("*.html")
        },
        **{str(p): p.read_text(encoding="utf-8") for p in (dossier_app / "static").rglob("*.js")},
        **{code: libelle for code, libelle in LIBELLES_ALERTES.items()},
        **{statut.value: libelle for statut, libelle in LIBELLES_STATUT.items()},
    }
    for origine, contenu in textes.items():
        for mot in ("garanti", "certifi"):
            assert mot not in contenu.lower(), f"« {mot} » dans {origine}"
