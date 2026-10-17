"""Validation et ré-encodage des photos avec Pillow.

Le fichier reçu n'est jamais stocké tel quel : il est décodé puis ré-encodé en JPEG. Cela
supprime toutes les métadonnées (la position du chef de chantier ne doit pas fuiter par
l'EXIF) et neutralise un fichier piégé. Le type MIME et l'extension du client sont ignorés.
"""

import math
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

TAILLE_MAX_OCTETS = 15 * 1024 * 1024
FORMATS_ACCEPTES = {"JPEG", "PNG", "WEBP"}
COTE_MAX_PX = 1920
COTE_MINIATURE_PX = 400
QUALITE_JPEG = 82
QUALITE_MINIATURE = 80
# Le JPEG se décode à échelle réduite (draft) : on accepte les capteurs de 200 Mpx.
# PNG et WebP se décodent en entier : on limite leur taille pour borner la mémoire.
Image.MAX_IMAGE_PIXELS = 105_000_000  # erreur au-delà du double
PIXELS_MAX_HORS_JPEG = 40_000_000
# Signatures « ftyp » des fichiers HEIF/HEIC (photos d'iPhone).
MARQUES_HEIC = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


class ImageRefusee(ValueError):
    """Le message est destiné à l'utilisateur."""


@dataclass
class ImageTraitee:
    contenu: bytes
    miniature: bytes
    largeur_px: int
    hauteur_px: int


def est_heic(donnees: bytes) -> bool:
    return donnees[4:8] == b"ftyp" and donnees[8:12] in MARQUES_HEIC


def traiter_image(donnees: bytes) -> ImageTraitee:
    if est_heic(donnees):
        raise ImageRefusee(
            "Format HEIC non pris en charge. Sur iPhone : "
            "Réglages > Appareil photo > Formats > « Le plus compatible »."
        )
    try:
        with Image.open(BytesIO(donnees)) as image:
            image.verify()  # structure du fichier ; l'image doit ensuite être rouverte
        with Image.open(BytesIO(donnees)) as image:
            if image.format not in FORMATS_ACCEPTES:
                raise ImageRefusee(
                    f"Format {image.format} non accepté : envoyez une photo JPEG, PNG ou WebP."
                )
            if image.format == "JPEG":
                echelle = COTE_MAX_PX / max(image.size)
                if echelle < 1:
                    image.draft(
                        "RGB", (math.ceil(image.width * echelle), math.ceil(image.height * echelle))
                    )
            elif image.width * image.height > PIXELS_MAX_HORS_JPEG:
                raise ImageRefusee("Image trop grande.")
            image = ImageOps.exif_transpose(image)  # photo portrait remise à l'endroit
            image = _en_rvb(image)
    except ImageRefusee:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise ImageRefusee("Ce fichier n'est pas une image valide.") from exc

    image.thumbnail((COTE_MAX_PX, COTE_MAX_PX), Image.Resampling.LANCZOS)
    miniature = image.copy()
    miniature.thumbnail((COTE_MINIATURE_PX, COTE_MINIATURE_PX), Image.Resampling.LANCZOS)
    return ImageTraitee(
        contenu=_encoder_sans_metadonnees(image, QUALITE_JPEG),
        miniature=_encoder_sans_metadonnees(miniature, QUALITE_MINIATURE),
        largeur_px=image.width,
        hauteur_px=image.height,
    )


def _en_rvb(image: Image.Image) -> Image.Image:
    """RVB pour le JPEG ; la transparence d'un PNG ou d'un WebP devient du blanc."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        fond = Image.new("RGB", image.size, "white")
        fond.paste(image, mask=image.getchannel("A"))
        return fond
    return image.convert("RGB")


def _encoder_sans_metadonnees(image: Image.Image, qualite: int) -> bytes:
    # Image reconstruite à partir des seuls pixels : aucun EXIF, profil ou commentaire ne peut
    # subsister.
    propre = Image.frombytes(image.mode, image.size, image.tobytes())
    sortie = BytesIO()
    propre.save(sortie, "JPEG", quality=qualite, optimize=True, progressive=True)
    return sortie.getvalue()
