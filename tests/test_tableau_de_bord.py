import html
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import event

from app.dates import date_relative
from app.geo.verification import resume_chantier
from app.libelles import initiales
from app.models import (
    Chantier,
    Etape,
    MembreChantier,
    Photo,
    Rapport,
    RoleMembre,
    SourcePosition,
    StatutRapport,
    StatutVerification,
    TypeEtape,
)
from tests.outils import creer_utilisateur, position_confirmee, se_connecter

RACINE = Path(__file__).resolve().parent.parent
MAINTENANT = datetime.now(UTC)


@pytest.fixture
def chantier(session):
    proprio = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    chef = creer_utilisateur(session, "chef@exemple.test", "Rodrigue Hounkpatin")
    autre = creer_utilisateur(session, "autre@exemple.test")
    chantier = Chantier(
        nom="Villa Calavi",
        **position_confirmee(2.35, 6.45),
        cree_le=MAINTENANT - timedelta(days=90),
    )
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    fondations = Etape(chantier=chantier, type=TypeEtape.FONDATIONS, ordre=1, poids=15)
    session.add_all([chantier, fondations])
    session.flush()
    return SimpleNamespace(
        chantier=chantier, proprio=proprio, chef=chef, autre=autre, fondations=fondations
    )


def rapport(
    session, chantier, il_y_a: timedelta, statut=StatutRapport.PUBLIE, etape=None, texte="Rapport"
) -> Rapport:
    date = MAINTENANT - il_y_a
    nouveau = Rapport(
        chantier_id=chantier.chantier.id,
        auteur_id=chantier.chef.id,
        statut=statut,
        texte=texte,
        cree_le=date,
        publie_le=date if statut == StatutRapport.PUBLIE else None,
        etape_id=etape.id if etape else None,
    )
    session.add(nouveau)
    session.flush()
    return nouveau


def photo(
    session,
    rapport_,
    lon=2.3501,
    lat=6.4501,
    statut=StatutVerification.VERIFIEE,
    alertes=(),
    avec_position=True,
):
    nouvelle = Photo(
        rapport_id=rapport_.id,
        chemin_fichier="x.jpg",
        envoyee_le=rapport_.cree_le,
        position=WKTElement(f"POINT({lon} {lat})", srid=4326) if avec_position else None,
        source_position=SourcePosition.NAVIGATEUR if avec_position else SourcePosition.AUCUNE,
        statut_verification=statut,
        alertes=list(alertes),
    )
    session.add(nouvelle)
    session.flush()
    return nouvelle


@pytest.fixture
def proprio(navigateur, chantier):
    se_connecter(navigateur, chantier.proprio.email)
    return navigateur


def geojson(client, chantier, requete=""):
    return client.get(f"/api/chantiers/{chantier.chantier.id}/photos.geojson{requete}")


def ids(collection) -> set[str]:
    return {f["properties"]["id"] for f in collection["features"]}


# --- GeoJSON des photos ---------------------------------------------------------------------------


def test_geojson_exclut_les_brouillons_et_les_photos_sans_position(proprio, session, chantier):
    publie = rapport(session, chantier, timedelta(days=1))
    brouillon = rapport(session, chantier, timedelta(hours=1), StatutRapport.BROUILLON)
    visible = photo(session, publie)
    photo(session, publie, avec_position=False)
    photo(session, brouillon)

    reponse = geojson(proprio, chantier)

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("application/geo+json")
    assert ids(reponse.json()) == {str(visible.id)}


def test_geojson_coordonnees_longitude_puis_latitude(proprio, session, chantier):
    photo(session, rapport(session, chantier, timedelta(days=1)), lon=2.351234, lat=6.451234)

    entite = geojson(proprio, chantier).json()["features"][0]

    assert entite["geometry"] == {"type": "Point", "coordinates": [2.351234, 6.451234]}
    assert (
        entite["properties"]["ton"] == "succes"
        and entite["properties"]["badge"] == "Sur le chantier"
    )


def test_geojson_respecte_chaque_filtre(proprio, session, chantier):
    recent_fondations = rapport(session, chantier, timedelta(days=2), etape=chantier.fondations)
    ancien = rapport(session, chantier, timedelta(days=20))
    tres_ancien = rapport(session, chantier, timedelta(days=60))
    p_fondations = photo(session, recent_fondations)
    p_hors_zone = photo(session, ancien, statut=StatutVerification.HORS_ZONE)
    p_doublon = photo(session, ancien, alertes=["doublon"])
    p_exif = photo(
        session, tres_ancien, alertes=["position_exif"]
    )  # simple information : pas « à vérifier »

    assert ids(geojson(proprio, chantier).json()) == {
        str(p.id) for p in (p_fondations, p_hors_zone, p_doublon, p_exif)
    }
    assert ids(geojson(proprio, chantier, f"?etape={chantier.fondations.id}").json()) == {
        str(p_fondations.id)
    }
    assert ids(geojson(proprio, chantier, "?periode=7j").json()) == {str(p_fondations.id)}
    assert ids(geojson(proprio, chantier, "?periode=30j").json()) == {
        str(p.id) for p in (p_fondations, p_hors_zone, p_doublon)
    }
    assert ids(geojson(proprio, chantier, "?alertes=1").json()) == {
        str(p_hors_zone.id),
        str(p_doublon.id),
    }
    du, au = (MAINTENANT - timedelta(days=25)).date(), (MAINTENANT - timedelta(days=15)).date()
    assert ids(geojson(proprio, chantier, f"?periode=perso&du={du}&au={au}").json()) == {
        str(p_hors_zone.id),
        str(p_doublon.id),
    }
    # Filtres combinés
    assert ids(geojson(proprio, chantier, "?periode=30j&alertes=1").json()) == {
        str(p_hors_zone.id),
        str(p_doublon.id),
    }


def test_geojson_reserve_aux_membres(navigateurs, session, chantier):
    photo(session, rapport(session, chantier, timedelta(days=1)))
    etranger = navigateurs()
    se_connecter(etranger, chantier.autre.email)

    assert geojson(etranger, chantier).status_code == 404
    assert geojson(navigateurs(), chantier).status_code == 401


# --- Synthèse -------------------------------------------------------------------------------------


@pytest.mark.parametrize("jours, alerte_attendue", [(8, True), (2, False)])
def test_alerte_d_inactivite(proprio, session, chantier, jours, alerte_attendue):
    rapport(session, chantier, timedelta(days=jours, hours=1))

    page = proprio.get(f"/chantiers/{chantier.chantier.id}").text

    assert (f"Aucune nouvelle depuis {jours} jours" in page) is alerte_attendue


def test_pourcentage_de_photos_verifiees_exclut_les_non_verifiables(proprio, session, chantier):
    recent = rapport(session, chantier, timedelta(days=3))
    for statut in (
        [StatutVerification.VERIFIEE] * 2
        + [StatutVerification.HORS_ZONE]
        + [StatutVerification.NON_VERIFIABLE] * 3
    ):
        photo(session, recent, statut=statut)
    photo(
        session, rapport(session, chantier, timedelta(days=45)), statut=StatutVerification.HORS_ZONE
    )  # hors des 30 jours

    resume = resume_chantier(session, chantier.chantier.id)

    # 2 vérifiées sur 3 vérifiables (et non 2 sur 6) : les non vérifiables sont exclues du
    # dénominateur.
    assert (resume.verifiees, resume.verifiables, resume.total, resume.pourcentage) == (2, 3, 6, 67)
    page = proprio.get(f"/chantiers/{chantier.chantier.id}").text
    assert ">67 %</p>" in page and "2 sur 3 photos vérifiables · 30 j" in page


def test_quatre_indicateurs_et_equipe(proprio, session, chantier):
    page = html.unescape(proprio.get(f"/chantiers/{chantier.chantier.id}").text)

    for libelle in ("Avancement global", "Dernier rapport", "Photos vérifiées", "Étapes en retard"):
        assert libelle in page
    assert "Rodrigue Hounkpatin" in page and "Mireille Adjovi" in page
    assert "Aucun rapport pour l'instant" in page  # état vide de la timeline


# --- Timeline -------------------------------------------------------------------------------------


def test_le_deuxieme_bloc_renvoie_les_rapports_11_a_20(proprio, session, chantier):
    for numero in range(1, 26):  # le n° 1 est le plus récent
        rapport(session, chantier, timedelta(hours=numero), texte=f"Rapport n°{numero:02d}")

    premier = proprio.get(f"/chantiers/{chantier.chantier.id}").text
    second = proprio.get(f"/chantiers/{chantier.chantier.id}/timeline?page=2").text

    assert re.findall(r"Rapport n°(\d+)", premier) == [f"{n:02d}" for n in range(1, 11)]
    assert re.findall(r"Rapport n°(\d+)", second) == [f"{n:02d}" for n in range(11, 21)]
    assert 'hx-trigger="revealed"' in second and "page=3" in second


def test_filtre_hors_zone_dans_la_timeline(proprio, session, chantier):
    avec_probleme = rapport(
        session, chantier, timedelta(days=1), texte="Rapport avec une photo hors zone"
    )
    sans_probleme = rapport(session, chantier, timedelta(days=2), texte="Rapport sans souci")
    hors_zone = photo(session, avec_probleme, statut=StatutVerification.HORS_ZONE)
    autre_photo_du_rapport = photo(session, avec_probleme)
    photo(session, sans_probleme)

    page = proprio.get(f"/chantiers/{chantier.chantier.id}/timeline?alertes=1").text

    assert "Rapport avec une photo hors zone" in page and "Rapport sans souci" not in page
    assert f'data-photo="{hors_zone.id}"' in page
    assert (
        f'data-photo="{autre_photo_du_rapport.id}"' not in page
    )  # seule la photo à vérifier est montrée
    assert (
        'data-geojson="/api/chantiers/' in page and "alertes=1" in page
    )  # la carte suit le même filtre


def test_nombre_de_requetes_constant_quel_que_soit_le_nombre_de_rapports(
    proprio, session, chantier, moteur_test
):
    def compter() -> int:
        requetes = []
        ecouteur = lambda *args: requetes.append(args[2])  # noqa: E731
        event.listen(moteur_test, "before_cursor_execute", ecouteur)
        try:
            assert proprio.get(f"/chantiers/{chantier.chantier.id}").status_code == 200
        finally:
            event.remove(moteur_test, "before_cursor_execute", ecouteur)
        return len(requetes)

    for numero in range(3):
        photo(
            session,
            rapport(session, chantier, timedelta(hours=numero + 1), etape=chantier.fondations),
        )
    compter()  # premier passage : mise à jour de la session de connexion
    avec_3 = compter()
    for numero in range(3, 10):
        nouveau = rapport(session, chantier, timedelta(hours=numero + 1), etape=chantier.fondations)
        for _ in range(3):
            photo(session, nouveau)
    avec_10 = compter()

    assert avec_3 == avec_10


# --- Onglets et accessibilité ------------------------------------------------------------------


def test_onglet_photos_et_navigation(proprio, session, chantier):
    p = photo(session, rapport(session, chantier, timedelta(days=1), etape=chantier.fondations))

    page = html.unescape(proprio.get(f"/chantiers/{chantier.chantier.id}/photos").text)

    assert f'data-photo="{p.id}"' in page
    assert 'alt="Photo — Fondations — ' in page  # texte alternatif : étape et date
    assert 'aria-current="page"' in page and "Vue d'ensemble" in page
    assert 'loading="lazy"' in page and 'width="200"' in page


def test_formats_de_date_et_initiales():
    maintenant = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert date_relative(maintenant - timedelta(days=3), maintenant) == "il y a 3 jours"
    assert date_relative(maintenant - timedelta(hours=30), maintenant) == "hier"
    assert date_relative(maintenant - timedelta(minutes=5), maintenant) == "il y a 5 min"
    assert initiales("Rodrigue Hounkpatin") == "RH" and initiales(None) == "?"


# --- Feuille de style ---------------------------------------------------------------------------


def executable_tailwind() -> str | None:
    if candidat := os.environ.get("TAILWINDCSS"):
        return candidat
    for nom in ("tailwindcss.exe", "tailwindcss"):
        if (RACINE / "outils" / nom).is_file():
            return str(RACINE / "outils" / nom)
    return shutil.which("tailwindcss")


def test_app_css_versionne_est_a_jour(tmp_path):
    executable = executable_tailwind()
    if executable is None:
        pytest.skip("Exécutable Tailwind introuvable (voir README) : comparaison faite par la CI.")
    sortie = tmp_path / "app.css"
    subprocess.run(
        [executable, "-i", "app/static/css/source.css", "-o", str(sortie), "--minify"],
        cwd=RACINE,
        check=True,
        capture_output=True,
    )

    assert sortie.read_bytes() == (RACINE / "app/static/css/app.css").read_bytes(), (
        "app/static/css/app.css n'est pas à jour : recompiler avec la commande du README."
    )
    assert len(sortie.read_bytes()) < 50 * 1024, "feuille de style au-delà de 50 Ko"


def test_aucune_couleur_codee_en_dur_dans_les_templates():
    motif = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|\bstyle=\"[^\"]*(color|background)")
    fautifs = [
        str(p.relative_to(RACINE))
        for p in (RACINE / "app/templates").rglob("*.html")
        if motif.search(p.read_text(encoding="utf-8"))
    ]
    assert not fautifs, f"couleurs codées en dur : {fautifs}"
