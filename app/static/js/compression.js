// Compression des photos avant envoi, partagée par l'envoi du chef de chantier et le relevé du proche.
// JPEG de 1920 px au plus sur le côté le plus long, visé sous 500 Ko : sur une connexion 3G,
// une photo de téléphone de 4 Mo mettrait plusieurs minutes à partir. Le canvas efface l'EXIF.

const COTE_MAX_PX = 1920;
const QUALITE_JPEG = 0.8;
const TAILLE_CIBLE_OCTETS = 500 * 1024;

async function decoder(fichier) {
  try {
    // Applique l'orientation EXIF : une photo portrait reste à l'endroit après compression.
    return await createImageBitmap(fichier, { imageOrientation: "from-image" });
  } catch {
    // Navigateurs sans cette option : une <img> applique elle-même l'orientation.
    const url = URL.createObjectURL(fichier);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      return image;
    } finally {
      URL.revokeObjectURL(url);
    }
  }
}

const versBlob = (canvas, qualite) =>
  new Promise((ok, echec) => canvas.toBlob((blob) => (blob ? ok(blob) : echec(new Error("compression"))), "image/jpeg", qualite));

export async function compresser(fichier) {
  const image = await decoder(fichier);
  const echelle = Math.min(1, COTE_MAX_PX / Math.max(image.width, image.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.width * echelle);
  canvas.height = Math.round(image.height * echelle);
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close?.();
  let qualite = QUALITE_JPEG;
  let blob = await versBlob(canvas, qualite);
  while (blob.size > TAILLE_CIBLE_OCTETS && qualite > 0.55) {
    qualite -= 0.1;
    blob = await versBlob(canvas, qualite);
  }
  return blob;
}
