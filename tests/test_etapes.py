import re
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.modeles_etapes import MAISON_INDIVIDUELLE
from app.models import (
    Chantier,
    Etape,
    HistoriqueAvancement,
    MembreChantier,
    Rapport,
    RoleMembre,
    StatutRapport,
    TypeEtape,
)
from app.services.avancement import (
    StatutEtape,
    aujourd_hui,
    avancement_global,
    changer_avancement,
    etat_etape,
)
from tests.outils import creer_utilisateur, position_confirmee, poster, se_connecter

JOUR = date(2026, 10, 17)


def etape(type_=TypeEtape.AUTRE, poids=10, pct=0, **champs) -> Etape:
    return Etape(type=type_, poids=poids, avancement_pct=pct, **champs)


@pytest.fixture
def chantier(session):
    proprio = creer_utilisateur(session, "proprio@exemple.test", "Mireille Adjovi")
    chef = creer_utilisateur(session, "chef@exemple.test", "Rodrigue Hounkpatin")
    autre = creer_utilisateur(session, "autre@exemple.test")
    chantier = Chantier(nom="Villa Calavi", **position_confirmee(2.35, 6.45))
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    chantier.etapes += [
        Etape(type=TypeEtape.TERRASSEMENT, poids=5, ordre=1, avancement_pct=100),
        Etape(type=TypeEtape.FONDATIONS, poids=15, ordre=2, avancement_pct=40),
        Etape(type=TypeEtape.ELEVATION, poids=20, ordre=3, avancement_pct=0),
    ]
    session.add(chantier)
    session.flush()
    return SimpleNamespace(
        chantier=chantier, proprio=proprio, chef=chef, autre=autre, etapes=list(chantier.etapes)
    )


def url(chantier, suite=""):
    return f"/chantiers/{chantier.chantier.id}/etapes{suite}"


def ordre_des_types(session, chantier) -> list[str]:
    return [
        e.type.value
        for e in session.scalars(
            select(Etape).where(Etape.chantier_id == chantier.chantier.id).order_by(Etape.ordre)
        )
    ]


# --- Modèle « Maison individuelle » ---------------------------------------------------------------


def test_creation_d_un_chantier_avec_le_modele(navigateur, session):
    user = creer_utilisateur(session, "nouveau@exemple.test")
    se_connecter(navigateur, user.email)

    reponse = poster(
        navigateur,
        "/chantiers/nouveau",
        {
            "nom": "Maison test",
            "pays": "BJ",
            "devise": "XOF",
            "statut": "en_preparation",
            "rayon_verification_m": "100",
            "coordonnees": "6.45, 2.35",
            "modele_etapes": "maison_individuelle",
        },
        page="/chantiers/nouveau",
    )

    chantier_id = uuid.UUID(reponse.headers["location"].removeprefix("/chantiers/").split("?")[0])
    etapes = session.scalars(
        select(Etape).where(Etape.chantier_id == chantier_id).order_by(Etape.ordre)
    ).all()
    assert len(etapes) == 8
    assert sum(e.poids for e in etapes) == 100 == MAISON_INDIVIDUELLE.poids_total
    assert [e.type for e in etapes] == [type_ for type_, _ in MAISON_INDIVIDUELLE.etapes]
    assert [e.ordre for e in etapes] == list(range(1, 9))


def test_case_decochee_aucune_etape(navigateur, session):
    user = creer_utilisateur(session, "nouveau@exemple.test")
    se_connecter(navigateur, user.email)

    reponse = poster(
        navigateur,
        "/chantiers/nouveau",
        {
            "nom": "Clôture",
            "pays": "BJ",
            "devise": "XOF",
            "statut": "en_preparation",
            "rayon_verification_m": "100",
            "coordonnees": "6.45, 2.35",
        },
        page="/chantiers/nouveau",
    )

    chantier_id = uuid.UUID(reponse.headers["location"].removeprefix("/chantiers/").split("?")[0])
    assert (
        session.scalar(
            select(func.count()).select_from(Etape).where(Etape.chantier_id == chantier_id)
        )
        == 0
    )


def test_case_cochee_par_defaut(navigateur, session):
    user = creer_utilisateur(session, "nouveau@exemple.test")
    se_connecter(navigateur, user.email)

    assert re.search(
        r'name="modele_etapes" value="maison_individuelle"[^>]*\bchecked',
        navigateur.get("/chantiers/nouveau").text,
    )


# --- Calculs --------------------------------------------------------------------------------------


def test_avancement_global_pondere():
    etapes = [etape(type_, poids) for type_, poids in MAISON_INDIVIDUELLE.etapes]
    etapes[1].avancement_pct = 100  # fondations, poids 15
    etapes[2].avancement_pct = 50  # élévation, poids 20

    assert avancement_global(etapes) == 25  # (15 × 100 + 20 × 50) / 100


def test_avancement_global_normalise_si_les_poids_ne_font_pas_100():
    assert avancement_global([etape(poids=30, pct=100), etape(poids=10, pct=0)]) == 75
    # 5, 15, 20, 15 (total 55) : (500 + 1500 + 1200) / 55 = 58,2
    assert (
        avancement_global(
            [
                etape(poids=5, pct=100),
                etape(poids=15, pct=100),
                etape(poids=20, pct=60),
                etape(poids=15, pct=0),
            ]
        )
        == 58
    )
    assert avancement_global([]) == 0
    # Arrondi habituel : 26,5 donne 27 (round() de Python donnerait 26).
    assert avancement_global([etape(poids=1, pct=53), etape(poids=1, pct=0)]) == 27


@pytest.mark.parametrize(
    "pct, fin_prevue, statut, jours",
    [
        (0, None, StatutEtape.A_VENIR, 0),
        (40, None, StatutEtape.EN_COURS, 0),
        (100, None, StatutEtape.TERMINEE, 0),
        (40, JOUR - timedelta(days=12), StatutEtape.EN_RETARD, 12),
        (0, JOUR - timedelta(days=1), StatutEtape.EN_RETARD, 1),
        (100, JOUR - timedelta(days=30), StatutEtape.TERMINEE, 0),  # terminée, même en retard
        (40, JOUR, StatutEtape.EN_COURS, 0),  # échéance aujourd'hui : pas encore dépassée
    ],
)
def test_statut_des_etapes_et_retard(pct, fin_prevue, statut, jours):
    etat = etat_etape(etape(pct=pct, date_fin_prevue=fin_prevue), JOUR)

    assert etat.statut == statut
    assert etat.jours_de_retard == jours


def test_libelle_du_retard():
    assert (
        etat_etape(etape(pct=40, date_fin_prevue=JOUR - timedelta(days=12)), JOUR).libelle
        == "En retard de 12 jours"
    )
    assert (
        etat_etape(etape(pct=40, date_fin_prevue=JOUR - timedelta(days=1)), JOUR).libelle
        == "En retard de 1 jour"
    )


def test_dates_reelles_remplies_automatiquement(session, chantier):
    elevation = chantier.etapes[2]

    changer_avancement(session, elevation, 30, chantier.proprio.id, jour=JOUR)
    assert (elevation.date_debut, elevation.date_fin) == (JOUR, None)

    changer_avancement(session, elevation, 100, chantier.proprio.id, jour=JOUR + timedelta(days=20))
    assert (elevation.date_debut, elevation.date_fin) == (JOUR, JOUR + timedelta(days=20))

    # Une étape qui redescend sous 100 % n'est plus terminée.
    changer_avancement(session, elevation, 90, chantier.proprio.id, jour=JOUR + timedelta(days=21))
    assert (elevation.date_debut, elevation.date_fin) == (JOUR, None)
    session.flush()
    assert session.scalar(select(func.count()).select_from(HistoriqueAvancement)) == 3


def test_avancement_inchange_sans_historique(session, chantier):
    assert changer_avancement(session, chantier.etapes[1], 40, chantier.proprio.id) is False
    session.flush()
    assert session.scalar(select(func.count()).select_from(HistoriqueAvancement)) == 0


# --- Gestion par le propriétaire ------------------------------------------------------------------


@pytest.fixture
def proprio(navigateur, chantier):
    se_connecter(navigateur, chantier.proprio.email)
    return navigateur


def test_reordonnancement_sans_violer_l_unicite(proprio, session, chantier):
    elevation = chantier.etapes[2]

    assert (
        poster(proprio, url(chantier, f"/{elevation.id}/monter"), page="/chantiers").status_code
        == 303
    )
    assert ordre_des_types(session, chantier) == ["terrassement", "elevation", "fondations"]

    poster(proprio, url(chantier, f"/{elevation.id}/monter"), page="/chantiers")
    poster(
        proprio, url(chantier, f"/{elevation.id}/monter"), page="/chantiers"
    )  # déjà en tête : sans effet
    assert ordre_des_types(session, chantier) == ["elevation", "terrassement", "fondations"]

    poster(proprio, url(chantier, f"/{elevation.id}/descendre"), page="/chantiers")
    assert ordre_des_types(session, chantier) == ["terrassement", "elevation", "fondations"]
    ordres = session.scalars(
        select(Etape.ordre).where(Etape.chantier_id == chantier.chantier.id)
    ).all()
    assert sorted(ordres) == [1, 2, 3]


def test_ajout_d_etape(proprio, session, chantier):
    sans_libelle = poster(proprio, url(chantier), {"type": "autre"}, page="/chantiers")
    assert sans_libelle.status_code == 400 and "libellé" in sans_libelle.text

    reponse = poster(
        proprio,
        url(chantier),
        {"type": "autre", "libelle": "Clôture et portail", "date_fin_prevue": "2026-12-15"},
        page="/chantiers",
    )

    assert reponse.status_code == 303
    nouvelle = session.scalar(select(Etape).where(Etape.libelle == "Clôture et portail"))
    assert (nouvelle.ordre, nouvelle.poids, nouvelle.date_fin_prevue) == (4, 10, date(2026, 12, 15))


def test_modification_libelle_poids_et_dates(proprio, session, chantier):
    fondations = chantier.etapes[1]

    poster(
        proprio,
        url(chantier, f"/{fondations.id}"),
        {
            "libelle": "Fondations et longrines",
            "poids": "18",
            "date_fin_prevue": "2026-10-05",
            "date_debut": "2026-08-17",
        },
        page="/chantiers",
    )

    session.refresh(fondations)
    assert (fondations.libelle, fondations.poids) == ("Fondations et longrines", 18)
    assert fondations.date_fin_prevue == date(2026, 10, 5)
    assert etat_etape(fondations, JOUR).statut == StatutEtape.EN_RETARD


def test_poids_hors_limites_refuse(proprio, session, chantier):
    reponse = poster(
        proprio, url(chantier, f"/{chantier.etapes[1].id}"), {"poids": "150"}, page="/chantiers"
    )

    assert reponse.status_code == 400
    session.refresh(chantier.etapes[1])
    assert chantier.etapes[1].poids == 15


def test_suppression_refusee_si_un_rapport_cite_l_etape(proprio, session, chantier):
    fondations, elevation = chantier.etapes[1], chantier.etapes[2]
    session.add(
        Rapport(
            chantier_id=chantier.chantier.id,
            auteur_id=chantier.chef.id,
            etape_id=fondations.id,
            statut=StatutRapport.PUBLIE,
            texte="Coulage",
        )
    )
    session.flush()

    refus = poster(proprio, url(chantier, f"/{fondations.id}/supprimer"), page="/chantiers")
    accord = poster(proprio, url(chantier, f"/{elevation.id}/supprimer"), page="/chantiers")

    assert refus.status_code == 409
    assert "citée dans au moins un rapport" in refus.text
    assert accord.status_code == 303
    session.expire_all()
    assert session.get(Etape, fondations.id) is not None
    assert session.get(Etape, elevation.id) is None


def test_le_proprietaire_met_a_jour_l_avancement_avec_commentaire(proprio, session, chantier):
    elevation = chantier.etapes[2]

    poster(
        proprio,
        url(chantier, f"/{elevation.id}/avancement"),
        {"avancement_pct": "25", "commentaire": "Constaté lors de ma visite"},
        page="/chantiers",
    )

    session.refresh(elevation)
    assert elevation.avancement_pct == 25 and elevation.date_debut == aujourd_hui()
    ligne = session.scalar(
        select(HistoriqueAvancement).where(HistoriqueAvancement.etape_id == elevation.id)
    )
    assert (ligne.ancien_pct, ligne.nouveau_pct, ligne.commentaire) == (
        0,
        25,
        "Constaté lors de ma visite",
    )
    assert ligne.auteur_id == chantier.proprio.id and ligne.rapport_id is None


# --- Droits
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "methode, suite, donnees",
    [
        ("post", "", {"type": "dalle"}),
        ("post", "/{etape}/monter", None),
        ("post", "/{etape}/descendre", None),
        ("post", "/{etape}/supprimer", None),
        ("post", "/{etape}/avancement", {"avancement_pct": "90"}),
        ("post", "/{etape}", {"poids": "50"}),
    ],
)
def test_seul_le_proprietaire_gere_les_etapes(
    navigateurs, session, chantier, methode, suite, donnees
):
    chemin = url(chantier, suite.format(etape=chantier.etapes[1].id))
    codes = {}
    for qui in ("chef", "autre"):
        client = navigateurs()
        se_connecter(client, getattr(chantier, qui).email)
        codes[qui] = poster(client, chemin, donnees, page="/chantiers").status_code

    # Le chef connaît le chantier : 403. Un non-membre ne doit pas savoir qu'il existe : 404.
    assert codes == {"chef": 403, "autre": 404}
    assert ordre_des_types(session, chantier) == ["terrassement", "fondations", "elevation"]
    session.refresh(chantier.etapes[1])
    assert (chantier.etapes[1].avancement_pct, chantier.etapes[1].poids) == (40, 15)


def test_le_chef_consulte_les_etapes_sans_pouvoir_les_gerer(navigateurs, chantier):
    chef, autre = navigateurs(), navigateurs()
    se_connecter(chef, chantier.chef.email)
    se_connecter(autre, chantier.autre.email)

    page = chef.get(url(chantier))

    assert page.status_code == 200  # onglet « Étapes » en lecture seule
    assert "Fondations" in page.text
    assert "/monter" not in page.text and "Ajouter une étape" not in page.text
    assert autre.get(url(chantier)).status_code == 404


def test_tous_les_membres_voient_l_historique(navigateurs, session, chantier):
    fondations = chantier.etapes[1]
    rapport = Rapport(
        chantier_id=chantier.chantier.id,
        auteur_id=chantier.chef.id,
        etape_id=fondations.id,
        statut=StatutRapport.PUBLIE,
        texte="Semelles coulées",
    )
    session.add(rapport)
    session.flush()
    changer_avancement(session, fondations, 70, chantier.chef.id, rapport=rapport)
    session.flush()
    chef = navigateurs()
    se_connecter(chef, chantier.chef.email)

    page = chef.get(url(chantier, f"/{fondations.id}"))

    assert page.status_code == 200
    assert re.search(r"40 %\s*<svg.*?</svg>\s*70 %", page.text, re.S)
    assert "Rodrigue Hounkpatin" in page.text
    assert f"/rapports/{rapport.id}" in page.text


# --- Page du chantier ----------------------------------------------------------------------------


def test_page_du_chantier_barre_globale_et_retard(navigateur, session, chantier):
    chantier.etapes[1].date_fin_prevue = aujourd_hui() - timedelta(days=12)
    session.flush()
    se_connecter(navigateur, chantier.chef.email)

    page = navigateur.get(f"/chantiers/{chantier.chantier.id}").text

    # (5 × 100 + 15 × 40 + 20 × 0) / 40 = 27,5 → 28
    assert ">28 %</p>" in page and 'aria-valuenow="28"' in page
    assert 'title="En retard de 12 jours"' in page
    assert ">1</p>" in page  # indicateur « Étapes en retard »
    assert (
        f"/chantiers/{chantier.chantier.id}/inviter" not in page
    )  # actions réservées au propriétaire
