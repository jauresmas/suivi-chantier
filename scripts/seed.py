"""Insère des données de démonstration : un chantier près d'Abomey-Calavi (Bénin)
et un second chantier à Cotonou, appartenant à un autre propriétaire.

Usage, depuis la racine du projet, après `alembic upgrade head` :

    python scripts/seed.py

Les comptes de démonstration ont pour mot de passe la variable SEED_PASSWORD ; à défaut,
en développement uniquement, une valeur par défaut affichée dans la console.

Le script peut être relancé : les identifiants sont fixes, les lignes existantes
sont mises à jour au lieu d'être dupliquées.
"""

import hashlib
import math
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

# Permet `python scripts/seed.py` depuis la racine sans installer le projet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geoalchemy2.elements import WKTElement  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth.securite import hacher_mot_de_passe  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.geo.releve import Mesure, calculer  # noqa: E402
from app.geo.verification import badge_photo, verifier_photos  # noqa: E402
from app.models import (  # noqa: E402
    Chantier,
    Etape,
    HistoriquePositionChantier,
    MembreChantier,
    Photo,
    Rapport,
    RelevePosition,
    RoleMembre,
    SourcePosition,
    SourcePositionChantier,
    StatutChantier,
    StatutPosition,
    StatutRapport,
    StatutReleve,
    TypeEtape,
    TypeReleveur,
    User,
)
from app.photos import stockage  # noqa: E402
from app.photos.traitement import traiter_image  # noqa: E402

# Espace de noms des identifiants de démonstration (UUID v5 : même nom, même UUID).
ESPACE_DEMO = uuid.UUID("6f1c2d4e-8a3b-4c5d-9e7f-0a1b2c3d4e5f")

LAT_CHANTIER, LON_CHANTIER = 6.4500, 2.3500
RAYON_TERRE_M = 6_371_008.8

MOT_DE_PASSE_DEMO = "chantier-demo-2026"  # développement uniquement


def ident(nom: str) -> uuid.UUID:
    return uuid.uuid5(ESPACE_DEMO, nom)


def point(lat: float, lon: float) -> WKTElement:
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


def decaler(lat: float, lon: float, nord_m: float = 0, est_m: float = 0) -> tuple[float, float]:
    """Point situé à nord_m et est_m mètres (approximation sphérique, précise à quelques km)."""
    dlat = math.degrees(nord_m / RAYON_TERRE_M)
    dlon = math.degrees(est_m / (RAYON_TERRE_M * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def utc(annee: int, mois: int, jour: int, heure: int, minute: int = 0) -> datetime:
    return datetime(annee, mois, jour, heure, minute, tzinfo=UTC)


def image_demo(titre: str, scene: str) -> bytes:
    """Illustration simple d'un chantier (ciel, sol de latérite, ouvrage), pour que la démo ait de
    vraies images."""
    largeur, hauteur = 1600, 1200
    image = Image.new("RGB", (largeur, hauteur))
    dessin = ImageDraw.Draw(image)
    horizon = int(hauteur * 0.55)
    for y in range(hauteur):
        if y < horizon:
            t = y / horizon
            couleur = (int(135 + 70 * t), int(180 + 45 * t), int(222 + 25 * t))
        else:
            t = (y - horizon) / (hauteur - horizon)
            couleur = (int(178 - 45 * t), int(94 - 32 * t), int(54 - 22 * t))
        dessin.line([(0, y), (largeur, y)], fill=couleur)
    beton, joint, acier = (168, 168, 160), (128, 128, 122), (196, 92, 30)
    if scene == "semelles":
        for x in (260, 700, 1140):
            dessin.rectangle(
                [x, horizon + 160, x + 220, horizon + 300], fill=beton, outline=joint, width=6
            )
            for k in range(5):
                dessin.line(
                    [(x + 30 + k * 40, horizon + 60), (x + 30 + k * 40, horizon + 170)],
                    fill=acier,
                    width=8,
                )
    else:
        bas, haut = horizon + 120, horizon - 260
        for rangee, y in enumerate(range(haut, bas, 60)):
            decalage = 0 if rangee % 2 == 0 else 60
            for x in range(240 + decalage, 1360, 120):
                dessin.rectangle([x, y, x + 116, y + 56], fill=beton, outline=joint, width=3)
        if scene == "chainages":
            dessin.rectangle([240, haut - 40, 1360, haut], fill=(150, 150, 142))
            for k in range(4):
                dessin.line(
                    [(240, haut - 34 + k * 10), (1360, haut - 34 + k * 10)], fill=acier, width=4
                )
    police = ImageFont.load_default(size=46)
    legende = f"Photo de démonstration — {titre}"
    gauche, haut_texte, droite, bas_texte = dessin.textbbox((0, 0), legende, font=police)
    dessin.rounded_rectangle(
        [40, hauteur - 150, 40 + droite + 60, hauteur - 50], radius=18, fill=(15, 23, 42)
    )
    dessin.text(
        (70, hauteur - 100 - (bas_texte - haut_texte) // 2), legende, font=police, fill="white"
    )
    tampon = BytesIO()
    image.save(tampon, "JPEG", quality=90)
    return tampon.getvalue()


def avec_fichier(photo: Photo, chantier_id: uuid.UUID, titre: str, scene: str) -> Photo:
    """Écrit l'image de démonstration (ré-encodée comme une vraie photo) et renseigne ses
    chemins."""
    traitee = traiter_image(image_demo(titre, scene))
    photo.chemin_fichier, photo.chemin_miniature = stockage.chemins_photo(
        chantier_id, photo.id, photo.envoyee_le
    )
    stockage.ecrire(photo.chemin_fichier, traitee.contenu)
    stockage.ecrire(photo.chemin_miniature, traitee.miniature)
    photo.largeur_px, photo.hauteur_px, photo.taille_octets = (
        traitee.largeur_px,
        traitee.hauteur_px,
        len(traitee.contenu),
    )
    return photo


def donnees_demo(hash_mot_de_passe: str) -> list:
    chantier_id = ident("chantier:villa-calavi")
    proprietaire_id = ident("user:proprietaire")
    chef_id = ident("user:chef")
    autre_chantier_id = ident("chantier:immeuble-cotonou")
    autre_proprietaire_id = ident("user:autre-proprietaire")

    users = [
        User(
            id=proprietaire_id,
            email="proprietaire@demo.test",
            nom_complet="Mireille Adjovi",
            telephone="+229 01 90 00 00 01",
            hash_mot_de_passe=hash_mot_de_passe,
        ),
        User(
            id=chef_id,
            email="chef@demo.test",
            nom_complet="Rodrigue Hounkpatin",
            telephone="+229 01 90 00 00 02",
            hash_mot_de_passe=hash_mot_de_passe,
        ),
        # Propriétaire d'un autre chantier : ne doit rien voir de la villa de Calavi.
        User(
            id=autre_proprietaire_id,
            email="autre.proprietaire@demo.test",
            nom_complet="Serge Dossou",
            hash_mot_de_passe=hash_mot_de_passe,
        ),
    ]

    chantier = Chantier(
        id=chantier_id,
        nom="Villa R+1 Calavi",
        adresse="Quartier Tankpè, derrière la pharmacie",
        ville="Abomey-Calavi",
        pays="BJ",
        # Position relevée sur place par un proche puis validée : voir releve_demo().
        position=point(LAT_CHANTIER, LON_CHANTIER),
        statut_position=StatutPosition.CONFIRMEE,
        source_position=SourcePositionChantier.RELEVE_PROCHE,
        position_confirmee_le=utc(2026, 8, 2, 10),
        rayon_verification_m=100,
        budget_prevu=Decimal("45000000.00"),
        devise="XOF",
        statut=StatutChantier.EN_COURS,
    )

    autre_chantier = Chantier(
        id=autre_chantier_id,
        nom="Immeuble R+2 Fidjrossè",
        ville="Cotonou",
        pays="BJ",
        position=point(6.3540, 2.3770),  # placée sur la carte, sans relevé sur place
        statut_position=StatutPosition.ESTIMEE,
        source_position=SourcePositionChantier.CARTE_PROPRIETAIRE,
        devise="XOF",
        statut=StatutChantier.EN_PREPARATION,
    )

    membres = [
        MembreChantier(
            chantier_id=chantier_id, user_id=proprietaire_id, role=RoleMembre.PROPRIETAIRE
        ),
        MembreChantier(chantier_id=chantier_id, user_id=chef_id, role=RoleMembre.CHEF_CHANTIER),
        MembreChantier(
            chantier_id=autre_chantier_id,
            user_id=autre_proprietaire_id,
            role=RoleMembre.PROPRIETAIRE,
        ),
    ]

    # Les 8 étapes du modèle « Maison individuelle » (poids total 100). L'élévation, prévue
    # pour le 10 octobre et réalisée à 60 %, est en retard : elle illustre l'alerte de retard.
    def etape(numero, type_, poids, avancement, fin_prevue, debut=None, fin=None):
        return Etape(
            id=ident(f"etape:{numero}"),
            chantier_id=chantier_id,
            type=type_,
            ordre=numero,
            poids=poids,
            avancement_pct=avancement,
            date_fin_prevue=fin_prevue,
            date_debut=debut,
            date_fin=fin,
        )

    etapes = [
        etape(
            1,
            TypeEtape.TERRASSEMENT,
            5,
            100,
            date(2026, 8, 14),
            date(2026, 8, 3),
            date(2026, 8, 14),
        ),
        etape(
            2,
            TypeEtape.FONDATIONS,
            15,
            100,
            date(2026, 9, 11),
            date(2026, 8, 17),
            date(2026, 9, 11),
        ),
        etape(3, TypeEtape.ELEVATION, 20, 60, date(2026, 10, 10), date(2026, 9, 14)),
        etape(4, TypeEtape.DALLE, 15, 0, date(2026, 11, 13)),
        etape(5, TypeEtape.TOITURE, 10, 0, date(2026, 12, 18)),
        etape(6, TypeEtape.MENUISERIES, 10, 0, date(2027, 1, 29)),
        etape(7, TypeEtape.ELECTRICITE_PLOMBERIE, 10, 0, date(2027, 2, 26)),
        etape(8, TypeEtape.FINITIONS, 15, 0, date(2027, 4, 30)),
    ]

    rapports = [
        Rapport(
            id=ident("rapport:1"),
            chantier_id=chantier_id,
            auteur_id=chef_id,
            etape_id=ident("etape:2"),
            texte="Semelles coulées, ferraillage des longrines vérifié.",
            cree_le=utc(2026, 9, 10, 16),
            statut=StatutRapport.PUBLIE,
            publie_le=utc(2026, 9, 10, 16, 5),
        ),
        Rapport(
            id=ident("rapport:2"),
            chantier_id=chantier_id,
            auteur_id=chef_id,
            etape_id=ident("etape:3"),
            texte="Élévation des murs du rez-de-chaussée à 60 %.",
            cree_le=utc(2026, 10, 12, 17, 30),
            statut=StatutRapport.PUBLIE,
            publie_le=utc(2026, 10, 12, 17, 40),
        ),
    ]

    lat_30m, lon_30m = decaler(LAT_CHANTIER, LON_CHANTIER, nord_m=18, est_m=24)  # 30 m
    lat_2km, lon_2km = decaler(LAT_CHANTIER, LON_CHANTIER, nord_m=1200, est_m=1600)  # 2 km
    photos = [
        # Prise sur place : à l'intérieur du rayon de vérification.
        Photo(
            id=ident("photo:1"),
            rapport_id=ident("rapport:1"),
            chemin_fichier="",
            empreinte_sha256=hashlib.sha256(b"demo/semelles.jpg").hexdigest(),
            position=point(lat_30m, lon_30m),
            source_position=SourcePosition.NAVIGATEUR,
            precision_m=8.0,
            prise_le=utc(2026, 9, 10, 15, 42),
            envoyee_le=utc(2026, 9, 10, 16),
        ),
        # Position à 2 km : photo prise ailleurs ou réutilisée.
        Photo(
            id=ident("photo:2"),
            rapport_id=ident("rapport:2"),
            chemin_fichier="",
            empreinte_sha256=hashlib.sha256(b"demo/murs_rdc.jpg").hexdigest(),
            position=point(lat_2km, lon_2km),
            source_position=SourcePosition.EXIF,
            precision_m=15.0,
            prise_le=utc(2026, 10, 11, 9, 5),
            envoyee_le=utc(2026, 10, 12, 17, 30),
        ),
        # GPS indisponible.
        Photo(
            id=ident("photo:3"),
            rapport_id=ident("rapport:2"),
            chemin_fichier="",
            empreinte_sha256=hashlib.sha256(b"demo/chainages.jpg").hexdigest(),
            position=None,
            source_position=SourcePosition.AUCUNE,
            prise_le=utc(2026, 10, 12, 16, 50),
            envoyee_le=utc(2026, 10, 12, 17, 30),
        ),
    ]
    for photo, (titre, scene) in zip(
        photos,
        [("semelles", "semelles"), ("murs du rez-de-chaussée", "murs"), ("chaînages", "chainages")],
        strict=True,
    ):
        avec_fichier(photo, chantier_id, titre, scene)

    return [*users, chantier, autre_chantier, *membres, *etapes, *rapports, *photos]


def releve_demo(session: Session) -> None:
    """Relevé de la villa par « Tante Afi », validé par la propriétaire, et historique de la
    position.

    Les 58 mesures simulées (un téléphone immobile pendant une minute, dispersion de 4 m, plus
    2 valeurs aberrantes) passent par le même calcul que les vrais relevés : points_bruts et
    position_resultat sont donc cohérents dans QGIS.
    """
    villa, proprietaire = ident("chantier:villa-calavi"), ident("user:proprietaire")
    hasard = random.Random(2026)
    debut = utc(2026, 8, 2, 7, 45)
    mesures = []
    for seconde in range(60):
        nord, est = (
            (hasard.gauss(0, 4), hasard.gauss(0, 4)) if seconde % 30 else (95, -60)
        )  # 2 aberrantes
        lat, lon = decaler(LAT_CHANTIER, LON_CHANTIER, nord_m=nord, est_m=est)
        mesures.append(Mesure(lat, lon, hasard.uniform(4, 12), debut + timedelta(seconds=seconde)))
    resultat = calculer(session, mesures)

    releve_id = ident("releve:villa")
    session.merge(
        RelevePosition(
            id=releve_id,
            chantier_id=villa,
            demande_par=proprietaire,
            nom_releveur="Afi Adjovi",
            lien_releveur="tante",
            type_releveur=TypeReleveur.PROCHE,
            empreinte_jeton=hashlib.sha256(b"demo/releve-villa").hexdigest(),
            cree_le=utc(2026, 8, 1, 18),
            expire_le=utc(2026, 8, 4, 18),
            statut=StatutReleve.VALIDE,
            points_bruts=resultat.multipoint(),
            position_resultat=resultat.point(),
            precision_estimee_m=round(resultat.precision_estimee_m, 1),
            nb_mesures=len(resultat.retenues),
            duree_releve_s=resultat.duree_s,
            appareil="Mozilla/5.0 (Linux; Android 13; TECNO Spark 10) Chrome/129 Mobile",
            realise_le=utc(2026, 8, 2, 7, 46),
            traite_le=utc(2026, 8, 2, 10),
            traite_par=proprietaire,
        )
    )
    estimee = point(
        *decaler(LAT_CHANTIER, LON_CHANTIER, nord_m=-28, est_m=21)
    )  # placée à 35 m près sur la carte
    session.merge(
        HistoriquePositionChantier(
            id=ident("historique-position:villa:1"),
            chantier_id=villa,
            ancienne_position=None,
            nouvelle_position=estimee,
            ancien_statut=StatutPosition.NON_DEFINIE,
            nouveau_statut=StatutPosition.ESTIMEE,
            source=SourcePositionChantier.CARTE_PROPRIETAIRE,
            modifie_par=proprietaire,
            cree_le=utc(2026, 8, 1, 17, 50),
        )
    )
    session.merge(
        HistoriquePositionChantier(
            id=ident("historique-position:villa:2"),
            chantier_id=villa,
            ancienne_position=estimee,
            nouvelle_position=resultat.point(),
            ancien_statut=StatutPosition.ESTIMEE,
            nouveau_statut=StatutPosition.CONFIRMEE,
            source=SourcePositionChantier.RELEVE_PROCHE,
            releve_id=releve_id,
            modifie_par=proprietaire,
            cree_le=utc(2026, 8, 2, 10),
        )
    )
    session.merge(
        HistoriquePositionChantier(
            id=ident("historique-position:immeuble:1"),
            chantier_id=ident("chantier:immeuble-cotonou"),
            ancienne_position=None,
            nouvelle_position=point(6.3540, 2.3770),
            ancien_statut=StatutPosition.NON_DEFINIE,
            nouveau_statut=StatutPosition.ESTIMEE,
            source=SourcePositionChantier.CARTE_PROPRIETAIRE,
            modifie_par=ident("user:autre-proprietaire"),
            cree_le=utc(2026, 9, 20, 12),
        )
    )
    # Comme une validation : la position de la villa devient le point calculé.
    session.get(Chantier, villa).position = resultat.point()


def mot_de_passe_demo() -> tuple[str, bool]:
    """Renvoie (mot de passe, True s'il s'agit de la valeur par défaut)."""
    settings = get_settings()
    if settings.seed_password:
        return settings.seed_password, False
    if settings.app_env != "development":
        sys.exit(
            "Définissez SEED_PASSWORD : la valeur par défaut n'est permise qu'en développement."
        )
    return MOT_DE_PASSE_DEMO, True


def main() -> None:
    if get_settings().app_env == "production":
        sys.exit("Refusé : pas de données de démonstration en production (APP_ENV=production).")

    mot_de_passe, par_defaut = mot_de_passe_demo()
    with Session(engine) as session, session.begin():
        # Ordres temporaires négatifs : les étapes ont pu être réordonnées depuis l'interface, et
        # remettre l'ordre du seed ne doit pas heurter l'unicité (chantier_id, ordre).
        villa = ident("chantier:villa-calavi")
        session.execute(update(Etape).where(Etape.chantier_id == villa).values(ordre=-Etape.ordre))
        for objet in donnees_demo(hacher_mot_de_passe(mot_de_passe)):
            session.merge(objet)
        session.flush()
        # Étapes ajoutées depuis l'interface : placées après celles du seed, dans leur ordre.
        ajoutees = session.scalars(
            select(Etape)
            .where(Etape.chantier_id == villa, Etape.ordre < 0)
            .order_by(Etape.ordre.desc())
        )
        for rang, etape in enumerate(ajoutees, start=9):
            etape.ordre = rang
        releve_demo(session)
        verifier_photos(session)  # distance, statut et alertes de toutes les photos

    with Session(engine) as session:
        print("Données de démonstration en place :")
        for modele in (
            User,
            Chantier,
            MembreChantier,
            Etape,
            Rapport,
            Photo,
            RelevePosition,
            HistoriquePositionChantier,
        ):
            nombre = session.scalar(select(func.count()).select_from(modele))
            print(f"  {modele.__tablename__:<28} {nombre}")

        print("Vérification des photos de démonstration :")
        photos_demo = session.scalars(
            select(Photo)
            .where(Photo.id.in_([ident(f"photo:{n}") for n in (1, 2, 3)]))
            .order_by(Photo.chemin_fichier)
        )
        for photo in photos_demo:
            badge = badge_photo(photo)
            alertes = f" | {', '.join(photo.alertes)}" if photo.alertes else ""
            statut = photo.statut_verification.value
            print(f"  {photo.chemin_fichier:<20} {statut:<15} {badge.texte}{alertes}")

        print("Comptes de démonstration :")
        for user in session.scalars(
            select(User).where(User.email.like("%@demo.test")).order_by(User.email)
        ):
            print(f"  {user.email:<30} {user.nom_complet}")
        if par_defaut:
            print(f"Mot de passe (développement uniquement) : {mot_de_passe}")
        else:
            print("Mot de passe : celui de SEED_PASSWORD")


if __name__ == "__main__":
    main()
