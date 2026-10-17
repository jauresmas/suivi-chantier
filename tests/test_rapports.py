import re
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import taches
from app.config import get_settings
from app.dates import balise_date, date_longue
from app.models import (
    Chantier,
    Etape,
    HistoriqueAvancement,
    MembreChantier,
    Photo,
    Rapport,
    RoleMembre,
    StatutRapport,
    TypeEtape,
)
from app.photos import stockage
from tests.outils import (
    creer_utilisateur,
    envoyer_photo,
    jeton_csrf,
    position_confirmee,
    poster,
    se_connecter,
)


@pytest.fixture(autouse=True)
def dossier_photos(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "stockage_photos_dir", tmp_path)
    return tmp_path


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
    fondations = Etape(chantier=chantier, type=TypeEtape.FONDATIONS, ordre=1, avancement_pct=30)
    elevation = Etape(chantier=chantier, type=TypeEtape.ELEVATION, ordre=2, avancement_pct=0)
    session.add_all([chantier, fondations, elevation])
    session.flush()
    return SimpleNamespace(
        chantier=chantier, proprio=proprio, chef=chef, autre=autre, fondations=fondations
    )


@pytest.fixture
def chef(navigateurs, chantier):
    client = navigateurs()
    se_connecter(client, chantier.chef.email)
    return client


def nouveau_rapport(client, chantier) -> str:
    reponse = client.post(
        f"/chantiers/{chantier.chantier.id}/rapports",
        headers={"X-CSRF-Token": jeton_csrf(client, "/chantiers")},
    )
    assert reponse.status_code in (200, 201)
    return reponse.json()["id"]


def publier(client, rapport_id, **champs):
    return poster(
        client,
        f"/rapports/{rapport_id}/publier",
        {"texte": "", "etape_id": "", **champs},
        page="/chantiers",
    )


def supprimer_photo(client, rapport_id, photo_id):
    return client.delete(
        f"/rapports/{rapport_id}/photos/{photo_id}",
        headers={"X-CSRF-Token": jeton_csrf(client, "/chantiers")},
    )


# --- Publication ----------------------------------------------------------------------------------


def test_publication_avec_texte_et_photos(chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)
    for _ in range(2):
        assert envoyer_photo(chef, rapport_id).status_code == 201

    reponse = publier(chef, rapport_id, texte="Semelles coulées.\nFerraillage vérifié.")

    assert reponse.status_code == 303
    assert reponse.headers["location"] == f"/rapports/{rapport_id}"
    rapport = session.get(Rapport, rapport_id)
    session.refresh(rapport)
    assert rapport.statut == StatutRapport.PUBLIE
    assert rapport.publie_le is not None
    assert rapport.texte == "Semelles coulées.\nFerraillage vérifié."
    page = chef.get(f"/rapports/{rapport_id}").text
    assert "Ferraillage vérifié." in page
    assert page.count('class="ouvrir-photo') == 2


def test_publication_d_un_rapport_vide_refusee(chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)

    reponse = publier(chef, rapport_id, texte="   ")

    assert reponse.status_code == 400
    assert "au moins une photo ou un commentaire" in reponse.text
    assert session.get(Rapport, rapport_id).statut == StatutRapport.BROUILLON


def test_texte_seul_suffit(chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)

    assert (
        publier(chef, rapport_id, texte="Livraison de ciment reportée à lundi.").status_code == 303
    )


def test_changement_d_avancement_mis_a_jour_avec_historique(chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)
    envoyer_photo(chef, rapport_id)

    publier(chef, rapport_id, etape_id=str(chantier.fondations.id), avancement_pct="40")

    session.refresh(chantier.fondations)
    assert chantier.fondations.avancement_pct == 40
    historique = session.scalars(select(HistoriqueAvancement)).all()
    assert len(historique) == 1
    ligne = historique[0]
    assert (ligne.ancien_pct, ligne.nouveau_pct) == (30, 40)
    assert ligne.etape_id == chantier.fondations.id
    assert str(ligne.rapport_id) == rapport_id and ligne.auteur_id == chantier.chef.id
    assert re.search(
        r"Avancement de l'étape.*?30 %.*?40 %", chef.get(f"/rapports/{rapport_id}").text, re.S
    )


def test_avancement_inchange_sans_historique(chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)

    publier(
        chef, rapport_id, texte="RAS", etape_id=str(chantier.fondations.id), avancement_pct="30"
    )

    assert session.scalar(select(func.count()).select_from(HistoriqueAvancement)) == 0
    assert str(session.get(Rapport, rapport_id).etape_id) == str(chantier.fondations.id)


def test_etape_d_un_autre_chantier_refusee(chef, session, chantier):
    ailleurs = Chantier(nom="Autre", **position_confirmee(2.4, 6.37))
    etape_ailleurs = Etape(chantier=ailleurs, type=TypeEtape.DALLE, ordre=1)
    session.add_all([ailleurs, etape_ailleurs])
    session.flush()
    rapport_id = nouveau_rapport(chef, chantier)

    reponse = publier(
        chef, rapport_id, texte="Test", etape_id=str(etape_ailleurs.id), avancement_pct="90"
    )

    assert reponse.status_code == 400
    session.refresh(etape_ailleurs)
    assert etape_ailleurs.avancement_pct == 0


# --- Un rapport publié est définitif --------------------------------------------------------------


def test_rapport_publie_ne_peut_plus_etre_modifie(chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)
    photo_id = envoyer_photo(chef, rapport_id).json()["id"]
    publier(chef, rapport_id, texte="Version d'origine")

    republication = publier(chef, rapport_id, texte="Texte réécrit")
    suppression = supprimer_photo(chef, rapport_id, photo_id)
    ajout = envoyer_photo(chef, rapport_id)

    assert republication.status_code == 409
    assert suppression.status_code == 409
    assert ajout.status_code == 409
    rapport = session.get(Rapport, rapport_id)
    session.refresh(rapport)
    assert rapport.texte == "Version d'origine"
    assert session.get(Photo, photo_id) is not None


def test_seul_l_auteur_publie(navigateurs, chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)
    publier(chef, rapport_id, texte="Publié par le chef")
    proprio = navigateurs()
    se_connecter(proprio, chantier.proprio.email)

    assert publier(proprio, rapport_id, texte="Réécrit").status_code == 403


# --- Brouillons
# ------------------------------------------------------------------------------------


def test_brouillon_d_un_autre_utilisateur_invisible(navigateurs, chef, session, chantier):
    rapport_id = nouveau_rapport(chef, chantier)
    photo_id = envoyer_photo(chef, rapport_id).json()["id"]
    proprio = navigateurs()
    se_connecter(proprio, chantier.proprio.email)

    assert proprio.get(f"/rapports/{rapport_id}").status_code == 404
    assert publier(proprio, rapport_id, texte="x").status_code == 404
    assert supprimer_photo(proprio, rapport_id, photo_id).status_code == 404
    assert envoyer_photo(proprio, rapport_id).status_code == 404
    assert proprio.get(f"/photos/{photo_id}").status_code == 404
    assert chef.get(f"/rapports/{rapport_id}").status_code == 200  # l'auteur, lui, le voit


def test_proprietaire_voit_le_rapport_publie_mais_pas_les_brouillons(
    navigateurs, chef, session, chantier
):
    publie = nouveau_rapport(chef, chantier)
    publier(chef, publie, texte="Murs du rez-de-chaussée montés.")
    brouillon = nouveau_rapport(chef, chantier)  # nouveau brouillon, après publication du précédent
    assert brouillon != publie
    envoyer_photo(chef, brouillon)
    proprio = navigateurs()
    se_connecter(proprio, chantier.proprio.email)

    page = proprio.get(f"/chantiers/{chantier.chantier.id}").text

    assert "Murs du rez-de-chaussée montés." in page
    assert f"/rapports/{publie}" in page
    assert f"/rapports/{brouillon}" not in page
    assert (
        "Vous avez un rapport en brouillon" in chef.get(f"/chantiers/{chantier.chantier.id}").text
    )
    assert "Vous avez un rapport en brouillon" not in page


def test_reprise_d_un_brouillon_recent(chef, session, chantier):
    premier = nouveau_rapport(chef, chantier)
    envoyer_photo(chef, premier)

    assert nouveau_rapport(chef, chantier) == premier
    page = chef.get(f"/chantiers/{chantier.chantier.id}/photos/envoyer").text
    assert f'data-rapport="{premier}"' in page
    assert "Retirer cette photo" in page


def test_brouillon_de_plus_de_24_h_non_repris(chef, session, chantier):
    ancien = Rapport(
        chantier_id=chantier.chantier.id,
        auteur_id=chantier.chef.id,
        cree_le=datetime.now(UTC) - timedelta(hours=30),
    )
    session.add(ancien)
    session.flush()

    assert nouveau_rapport(chef, chantier) != str(ancien.id)


def test_l_auteur_retire_une_photo_de_son_brouillon(chef, session, chantier, dossier_photos):
    rapport_id = nouveau_rapport(chef, chantier)
    photo_id = envoyer_photo(chef, rapport_id).json()["id"]
    photo = session.get(Photo, photo_id)
    fichiers = [dossier_photos / photo.chemin_fichier, dossier_photos / photo.chemin_miniature]
    assert all(f.is_file() for f in fichiers)

    reponse = supprimer_photo(chef, rapport_id, photo_id)

    assert reponse.status_code == 204
    session.expire_all()
    assert session.get(Photo, photo_id) is None
    assert not any(f.exists() for f in fichiers)


# --- Nettoyage ------------------------------------------------------------------------------------


def brouillon_avec_photo(
    session, chantier, age: timedelta, statut=StatutRapport.BROUILLON
) -> tuple[Rapport, list]:
    date = datetime.now(UTC) - age
    rapport = Rapport(
        chantier_id=chantier.chantier.id,
        auteur_id=chantier.chef.id,
        cree_le=date,
        statut=statut,
        publie_le=date if statut == StatutRapport.PUBLIE else None,
        texte="x",
    )
    session.add(rapport)
    session.flush()
    chemins = stockage.chemins_photo(chantier.chantier.id, rapport.id, date)
    for chemin in chemins:
        stockage.ecrire(chemin, b"jpeg")
    session.add(
        Photo(
            rapport_id=rapport.id,
            chemin_fichier=chemins[0],
            chemin_miniature=chemins[1],
            envoyee_le=date,
        )
    )
    session.flush()
    return rapport, [stockage.racine() / c for c in chemins]


def test_nettoyage_des_brouillons(session, chantier, monkeypatch, capsys):
    vieux, fichiers_vieux = brouillon_avec_photo(session, chantier, timedelta(days=8))
    recent, fichiers_recent = brouillon_avec_photo(session, chantier, timedelta(days=2))
    publie, fichiers_publie = brouillon_avec_photo(
        session, chantier, timedelta(days=60), StatutRapport.PUBLIE
    )
    monkeypatch.setattr(taches, "SessionLocal", lambda: nullcontext(session))

    taches.main(["nettoyer_brouillons"])

    session.expire_all()
    assert session.get(Rapport, vieux.id) is None
    assert not any(f.exists() for f in fichiers_vieux)
    assert session.get(Rapport, recent.id) is not None and all(f.exists() for f in fichiers_recent)
    assert session.get(Rapport, publie.id) is not None and all(f.exists() for f in fichiers_publie)
    assert "1 brouillon(s) supprimé(s), avec 1 photo(s)." in capsys.readouterr().out


# --- Dates
# ------------------------------------------------------------------------------------------


def test_dates_en_francais_a_l_heure_du_benin():
    date = datetime(2026, 10, 17, 13, 5, tzinfo=UTC)

    assert date_longue(date) == "samedi 17 octobre 2026 à 14 h 05"
    assert date_longue(datetime(2026, 3, 2, 8, 30, tzinfo=UTC)) == "lundi 2 mars 2026 à 9 h 30"
    balise = str(balise_date(date))
    assert 'datetime="2026-10-17T13:05:00+00:00"' in balise
    assert 'data-fuseau="Africa/Porto-Novo"' in balise
    assert ">samedi 17 octobre 2026 à 14 h 05</time>" in balise
