from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from geoalchemy2 import Geometry
from PIL import ExifTags, Image
from sqlalchemy import cast, func, select

from app.config import get_settings
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
from tests.outils import creer_utilisateur, jeton_csrf, position_confirmee, se_connecter

# --- Images de test, générées à la volée (aucun fichier binaire dans le dépôt)
# ----------------------


def image(
    format_="JPEG", largeur=640, hauteur=480, couleur="orange", exif=None, mode="RGB"
) -> bytes:
    tampon = BytesIO()
    options = {"exif": exif} if exif is not None else {}
    Image.new(mode, (largeur, hauteur), couleur).save(tampon, format_, **options)
    return tampon.getvalue()


def exif_avec_gps() -> Image.Exif:
    """EXIF d'un téléphone : marque, date et position à Abomey-Calavi (6°27'04.32"N,
    2°21'00.36"E)."""
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TelephoneTest"
    exif[ExifTags.Base.DateTime] = "2026:10:12 09:05:00"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (6.0, 27.0, 4.32)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (2.0, 21.0, 0.36)
    return exif


def portrait_pivote() -> bytes:
    """Image stockée en paysage (moitié gauche rouge, droite bleue) avec orientation EXIF 6.

    Affichée correctement (rotation de 90° dans le sens horaire), elle est en portrait :
    rouge en haut, bleu en bas.
    """
    brute = Image.new("RGB", (400, 200), "blue")
    brute.paste((255, 0, 0), (0, 0, 200, 200))
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    tampon = BytesIO()
    brute.save(tampon, "JPEG", exif=exif, quality=95)
    return tampon.getvalue()


# --- Données et outils ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stockage(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "stockage_photos_dir", tmp_path)
    return tmp_path


@pytest.fixture
def chantier(session):
    proprio = creer_utilisateur(session, "proprio@exemple.test")
    chef = creer_utilisateur(session, "chef@exemple.test")
    autre = creer_utilisateur(session, "autre@exemple.test")
    chantier = Chantier(nom="Villa Calavi", **position_confirmee(2.3501, 6.4512))
    chantier.membres += [
        MembreChantier(user_id=proprio.id, role=RoleMembre.PROPRIETAIRE),
        MembreChantier(user_id=chef.id, role=RoleMembre.CHEF_CHANTIER),
    ]
    rapport = Rapport(chantier=chantier, auteur_id=chef.id)
    session.add_all([chantier, rapport])
    session.flush()
    return SimpleNamespace(
        proprio=proprio, chef=chef, autre=autre, chantier=chantier, rapport=rapport
    )


@pytest.fixture
def chef(navigateur, chantier):
    se_connecter(navigateur, chantier.chef.email)
    return navigateur


POSITION_GPS = {
    "latitude": "6.451712",
    "longitude": "2.350311",
    "precision_m": "8.5",
    "source_position": "navigateur",
}


def envoyer(client, rapport_id, contenu: bytes, nom="photo.jpg", **champs):
    return client.post(
        f"/rapports/{rapport_id}/photos",
        files={"fichier": (nom, contenu, "image/jpeg")},
        data=champs,
        headers={"X-CSRF-Token": jeton_csrf(client, "/chantiers")},
    )


def photo_en_base(session, reponse) -> Photo:
    return session.get(Photo, reponse.json()["id"])


def lon_lat(session, photo):
    geometrie = cast(Photo.position, Geometry)
    return session.execute(
        select(func.ST_X(geometrie), func.ST_Y(geometrie)).where(Photo.id == photo.id)
    ).one()


def nombre_de_photos(session) -> int:
    return session.scalar(select(func.count()).select_from(Photo))


# --- Envoi --------------------------------------------------------------------------------------


def test_envoi_jpeg_avec_position(chef, session, chantier, stockage):
    obtenue = datetime.now(UTC) - timedelta(seconds=30)

    reponse = envoyer(
        chef, chantier.rapport.id, image(), position_obtenue_le=obtenue.isoformat(), **POSITION_GPS
    )

    assert reponse.status_code == 201, reponse.text
    photo = photo_en_base(session, reponse)
    assert lon_lat(session, photo) == (2.350311, 6.451712)  # POINT(lon lat)
    assert photo.source_position == SourcePosition.NAVIGATEUR
    assert photo.precision_m == pytest.approx(8.5)
    assert photo.position_obtenue_le == obtenue
    assert (photo.largeur_px, photo.hauteur_px) == (640, 480)
    # Vérifiée dans la même requête que l'envoi : à une soixantaine de mètres du chantier.
    assert photo.statut_verification == StatutVerification.VERIFIEE
    assert 50 < photo.distance_chantier_m < 70
    assert reponse.json()["badge"]["texte"] == "Sur le chantier"
    maintenant = datetime.now(UTC)
    assert (
        photo.chemin_fichier
        == f"{chantier.chantier.id}/{maintenant:%Y}/{maintenant:%m}/{photo.id}.jpg"
    )
    assert photo.chemin_miniature == photo.chemin_fichier.replace(".jpg", "_mini.jpg")
    fichier = stockage / photo.chemin_fichier
    assert fichier.is_file() and fichier.stat().st_size == photo.taille_octets
    with Image.open(stockage / photo.chemin_miniature) as miniature:
        assert max(miniature.size) == 400


def test_envoi_sans_coordonnees(chef, session, chantier):
    reponse = envoyer(chef, chantier.rapport.id, image(), source_position="aucune")

    photo = photo_en_base(session, reponse)
    assert photo.source_position == SourcePosition.AUCUNE
    assert photo.position is None


def test_coordonnees_invalides_photo_gardee_sans_position(chef, session, chantier):
    reponse = envoyer(chef, chantier.rapport.id, image(), **{**POSITION_GPS, "latitude": "95"})

    assert reponse.status_code == 201
    photo = photo_en_base(session, reponse)
    assert photo.position is None and photo.source_position == SourcePosition.AUCUNE
    assert photo.precision_m is None


def test_precision_hors_limites_ignoree(chef, session, chantier):
    reponse = envoyer(
        chef, chantier.rapport.id, image(), **{**POSITION_GPS, "precision_m": "25000"}
    )

    photo = photo_en_base(session, reponse)
    assert photo.position is not None and photo.precision_m is None


def test_fichier_stocke_sans_aucune_donnee_exif(chef, session, chantier, stockage):
    source = image(exif=exif_avec_gps())
    with Image.open(BytesIO(source)) as originale:
        assert originale.getexif().get_ifd(ExifTags.IFD.GPSInfo), (
            "l'image source contient bien un GPS"
        )

    photo = photo_en_base(session, envoyer(chef, chantier.rapport.id, source))

    for relatif in (photo.chemin_fichier, photo.chemin_miniature):
        contenu = (stockage / relatif).read_bytes()
        with Image.open(BytesIO(contenu)) as stockee:
            assert dict(stockee.getexif()) == {}
            assert "exif" not in stockee.info
        assert b"Exif" not in contenu and b"TelephoneTest" not in contenu


def test_portrait_avec_orientation_exif_6_stocke_a_l_endroit(chef, session, chantier, stockage):
    photo = photo_en_base(session, envoyer(chef, chantier.rapport.id, portrait_pivote()))

    assert (photo.largeur_px, photo.hauteur_px) == (200, 400)
    with Image.open(stockage / photo.chemin_fichier) as stockee:
        rouge, _, bleu = stockee.getpixel((100, 50))
        assert rouge > 200 and bleu < 60, "rouge en haut"
        rouge, _, bleu = stockee.getpixel((100, 350))
        assert bleu > 200 and rouge < 60, "bleu en bas"


def test_image_de_4000_px_reduite_a_1920(chef, session, chantier, stockage):
    photo = photo_en_base(
        session, envoyer(chef, chantier.rapport.id, image(largeur=4000, hauteur=3000))
    )

    assert (photo.largeur_px, photo.hauteur_px) == (1920, 1440)
    with Image.open(stockage / photo.chemin_fichier) as stockee:
        assert stockee.size == (1920, 1440)


@pytest.mark.parametrize("format_, mode", [("PNG", "RGBA"), ("WEBP", "RGB")])
def test_png_et_webp_acceptes_et_convertis_en_jpeg(
    chef, session, chantier, stockage, format_, mode
):
    reponse = envoyer(chef, chantier.rapport.id, image(format_, mode=mode), nom="capture.png")

    assert reponse.status_code == 201
    with Image.open(stockage / photo_en_base(session, reponse).chemin_fichier) as stockee:
        assert stockee.format == "JPEG"


def test_nom_de_fichier_du_client_jamais_reutilise(chef, session, chantier, stockage):
    photo = photo_en_base(
        session, envoyer(chef, chantier.rapport.id, image(), nom="IMG_maison_secrete.jpg")
    )

    assert "maison_secrete" not in photo.chemin_fichier
    assert not [f for f in stockage.rglob("*") if "maison_secrete" in f.name]


# --- Refus ----------------------------------------------------------------------------------------


def test_fichier_texte_renomme_en_jpg_refuse(chef, session, chantier, stockage):
    reponse = envoyer(
        chef, chantier.rapport.id, b"Ceci n'est pas une photo.\n" * 50, nom="faux.jpg"
    )

    assert reponse.status_code == 415
    assert "pas une image valide" in reponse.json()["detail"]
    assert nombre_de_photos(session) == 0
    assert not list(stockage.rglob("*.jpg"))


def test_heic_refuse_avec_message_clair(chef, session, chantier):
    entete_heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 200

    reponse = envoyer(chef, chantier.rapport.id, entete_heic, nom="IMG_0001.HEIC")

    assert reponse.status_code == 415
    assert "HEIC" in reponse.json()["detail"]


def test_fichier_de_plus_de_15_mo_refuse(chef, session, chantier, stockage):
    trop_lourd = b"\xff\xd8\xff" + b"\x00" * (15 * 1024 * 1024)

    reponse = envoyer(chef, chantier.rapport.id, trop_lourd)

    assert reponse.status_code == 413
    assert nombre_de_photos(session) == 0
    assert not list(stockage.rglob("*.jpg"))


@pytest.mark.parametrize("champ", ["prise_le", "position_obtenue_le"])
def test_date_dans_le_futur_refusee(chef, session, chantier, champ):
    futur = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()

    reponse = envoyer(chef, chantier.rapport.id, image(), **POSITION_GPS, **{champ: futur})

    assert reponse.status_code == 400
    assert "futur" in reponse.json()["detail"]
    assert nombre_de_photos(session) == 0


def test_date_legerement_en_avance_toleree(chef, session, chantier):
    presque = datetime.now(UTC) + timedelta(minutes=2)

    reponse = envoyer(chef, chantier.rapport.id, image(), prise_le=presque.isoformat())

    assert reponse.status_code == 201
    assert photo_en_base(session, reponse).prise_le == presque


def test_envoi_sans_jeton_csrf_refuse(chef, chantier):
    reponse = chef.post(
        f"/rapports/{chantier.rapport.id}/photos",
        files={"fichier": ("p.jpg", image(), "image/jpeg")},
    )

    assert reponse.status_code == 403


# --- Contrôle d'accès -----------------------------------------------------------------------------


def test_non_membre_404_a_l_envoi_et_a_la_lecture(navigateurs, session, chantier):
    chef = navigateurs()
    se_connecter(chef, chantier.chef.email)
    photo_id = envoyer(chef, chantier.rapport.id, image()).json()["id"]

    etranger = navigateurs()
    se_connecter(etranger, chantier.autre.email)

    assert envoyer(etranger, chantier.rapport.id, image()).status_code == 404
    assert etranger.get(f"/photos/{photo_id}").status_code == 404
    assert etranger.get(f"/photos/{photo_id}/miniature").status_code == 404
    assert nombre_de_photos(session) == 1


def test_lecture_par_un_membre_avec_cache_prive(navigateurs, session, chantier):
    chef = navigateurs()
    se_connecter(chef, chantier.chef.email)
    photo_id = envoyer(chef, chantier.rapport.id, image()).json()["id"]
    proprio = navigateurs()
    se_connecter(proprio, chantier.proprio.email)
    assert proprio.get(f"/photos/{photo_id}").status_code == 404, (
        "photo d'un brouillon : réservée à son auteur"
    )

    chantier.rapport.statut = StatutRapport.PUBLIE
    session.flush()
    for url in (f"/photos/{photo_id}", f"/photos/{photo_id}/miniature"):
        reponse = proprio.get(url)
        assert reponse.status_code == 200
        assert reponse.headers["content-type"] == "image/jpeg"
        assert reponse.headers["cache-control"] == "private, max-age=86400"


def test_photos_jamais_servies_en_statique(chef, session, chantier, stockage):
    photo = photo_en_base(session, envoyer(chef, chantier.rapport.id, image()))

    assert chef.get(f"/static/{photo.chemin_fichier}").status_code == 404
    assert chef.get(f"/stockage/photos/{photo.chemin_fichier}").status_code == 404


def test_lecture_sans_connexion_refusee(navigateur, chantier):
    assert navigateur.get("/photos/00000000-0000-0000-0000-000000000000").status_code == 401


# --- Rapport et page d'envoi ----------------------------------------------------------------------


# Le chef a déjà un brouillon récent sur ce chantier (celui de la fixture) : il est repris (200).
@pytest.mark.parametrize("qui, code_attendu", [("chef", 200), ("proprio", 201), ("autre", 404)])
def test_creation_du_rapport(navigateur, session, chantier, qui, code_attendu):
    se_connecter(navigateur, getattr(chantier, qui).email)

    reponse = navigateur.post(
        f"/chantiers/{chantier.chantier.id}/rapports",
        headers={"X-CSRF-Token": jeton_csrf(navigateur, "/chantiers")},
    )

    assert reponse.status_code == code_attendu
    if code_attendu == 200:
        assert reponse.json()["id"] == str(chantier.rapport.id)
    if code_attendu in (200, 201):
        rapport = session.get(Rapport, reponse.json()["id"])
        assert rapport.chantier_id == chantier.chantier.id
        assert rapport.auteur_id == getattr(chantier, qui).id


@pytest.mark.parametrize("qui, code_attendu", [("chef", 200), ("proprio", 200), ("autre", 404)])
def test_page_d_envoi(navigateur, chantier, qui, code_attendu):
    se_connecter(navigateur, getattr(chantier, qui).email)

    reponse = navigateur.get(f"/chantiers/{chantier.chantier.id}/photos/envoyer")

    assert reponse.status_code == code_attendu
    if code_attendu == 200:
        assert 'capture="environment"' in reponse.text
        assert "exifr@7.1.3/dist/lite.umd.js" in reponse.text
