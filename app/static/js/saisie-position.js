// Page de création / modification : placer le chantier par clic, coordonnées collées ou recherche de lieu.
// Le serveur revalide tout (app/geo/coordonnees.py) ; ce script n'en fait qu'un aperçu.
import { creerCarte, dessinerRayon, localiser, nouveauMarqueur, quandChargee, zoomPourRayon, ZOOM_CHANTIER } from "./carte.js";

const $ = (id) => document.getElementById(id);
const champLatitude = $("latitude");
const champLongitude = $("longitude");
const champCoordonnees = $("coordonnees");
const apercu = $("apercu-coordonnees");
const affichage = $("position-choisie");
const curseur = $("rayon");
const valeurRayon = $("valeur-rayon");
const champRecherche = $("recherche-lieu");
const listeResultats = $("resultats-recherche");
const etatRecherche = $("etat-recherche");
const boutonPosition = $("ma-position");

// --- Aperçu du parseur de coordonnées (mêmes règles que le serveur) ------------------------------

const NOMBRE = "[-+]?\\d{1,3}(?:\\.\\d+)?";
const MOTIF_TEXTE = new RegExp(`^\\s*(${NOMBRE})\\s*(?:,\\s*|\\s+)(${NOMBRE})\\s*$`);
const MOTIF_REPERE = new RegExp(`!3d(${NOMBRE})!4d(${NOMBRE})`);
const MOTIF_AROBASE = new RegExp(`@(${NOMBRE}),(${NOMBRE})`);

function lireCoordonnees(texte) {
  texte = texte.trim();
  const url = texte.match(/https?:\/\/\S+/i)?.[0];
  let trouve = null;
  if (url) {
    let lien;
    try {
      lien = new URL(url);
    } catch {
      return { erreur: "Lien illisible." };
    }
    if (["goo.gl", "maps.app.goo.gl", "g.co"].includes(lien.hostname)) {
      return { erreur: "Lien raccourci : ouvrez-le dans Google Maps, puis copiez le lien complet." };
    }
    let decode = url;
    try {
      decode = decodeURIComponent(url);
    } catch {
      // encodage invalide : on cherche dans le lien tel quel
    }
    trouve = decode.match(MOTIF_REPERE);
    for (const nom of ["q", "query", "ll"]) {
      trouve ??= (lien.searchParams.get(nom) ?? "").replace(/^loc:/, "").match(MOTIF_TEXTE);
    }
    trouve ??= decode.match(MOTIF_AROBASE);
    if (!trouve) return { erreur: "Ce lien ne contient pas de coordonnées." };
  } else {
    trouve = texte.match(MOTIF_TEXTE);
    if (!trouve) return { erreur: "Format non reconnu. Exemple : 6.4512, 2.3501" };
    if (!trouve[1].includes(".") && !trouve[2].includes(".")) return { erreur: "Précisez les décimales avec un point : 6.4512, 2.3501" };
  }
  const lat = Number(trouve[1]);
  const lon = Number(trouve[2]);
  if (Math.abs(lat) > 90) return { erreur: "Latitude impossible (entre -90 et 90). Latitude et longitude sont peut-être inversées." };
  if (Math.abs(lon) > 180) return { erreur: "Longitude impossible (entre -180 et 180)." };
  return { lat, lon };
}

// --- Carte et marqueur ------------------------------------------------------------------------------

const arrondi = (x) => Math.round(x * 1e6) / 1e6;
const depart = Number.isFinite(parseFloat(champLatitude.value)) && Number.isFinite(parseFloat(champLongitude.value))
  ? { lat: parseFloat(champLatitude.value), lon: parseFloat(champLongitude.value) }
  : null;

const carte = creerCarte($("carte"), {
  satellite: true,
  ...(depart && { centre: [depart.lon, depart.lat], zoom: zoomPourRayon(Number(curseur.value)) }),
});
let marqueur = null;

function dessinerCercle() {
  if (!marqueur) return;
  const { lng, lat } = marqueur.getLngLat();
  quandChargee(carte).then(() => dessinerRayon(carte, [lng, lat], Number(curseur.value)));
}

function placer(lon, lat, { zoomer = false, depuisTexte = false } = {}) {
  lon = arrondi(lon);
  lat = arrondi(lat);
  if (!marqueur) {
    marqueur = nouveauMarqueur({ deplacable: true }).setLngLat([lon, lat]).addTo(carte);
    marqueur.on("drag", dessinerCercle);
    marqueur.on("dragend", () => {
      const p = marqueur.getLngLat().wrap();
      placer(p.lng, p.lat);
    });
  } else {
    marqueur.setLngLat([lon, lat]);
  }
  champLatitude.value = lat.toFixed(6);
  champLongitude.value = lon.toFixed(6);
  affichage.textContent = `Latitude ${lat.toFixed(6)} · Longitude ${lon.toFixed(6)}`;
  if (!depuisTexte) {
    // Le champ texte est ce que le serveur lit en priorité : on le garde synchronisé avec le marqueur.
    champCoordonnees.value = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
    apercu.textContent = "Mis à jour depuis la carte.";
  }
  dessinerCercle();
  if (zoomer) carte.flyTo({ center: [lon, lat], zoom: Math.max(carte.getZoom(), ZOOM_CHANTIER) });
}

if (depart) placer(depart.lon, depart.lat, { depuisTexte: true });

carte.on("click", (evenement) => {
  const p = evenement.lngLat.wrap();
  placer(p.lng, p.lat);
});

curseur.addEventListener("input", () => {
  valeurRayon.textContent = curseur.value;
  dessinerCercle();
});

// --- Coordonnées collées ------------------------------------------------------------------------------

function appliquerTexte() {
  const texte = champCoordonnees.value;
  if (!texte.trim()) {
    apercu.textContent = "Latitude puis longitude, avec un point décimal.";
    return;
  }
  const resultat = lireCoordonnees(texte);
  if (resultat.erreur) {
    apercu.textContent = resultat.erreur;
    apercu.classList.add("erreur-champ");
    return;
  }
  apercu.classList.remove("erreur-champ");
  apercu.textContent = `Reconnu : ${resultat.lat.toFixed(6)}, ${resultat.lon.toFixed(6)}`;
  placer(resultat.lon, resultat.lat, { zoomer: true, depuisTexte: true });
}

let minuterieTexte;
champCoordonnees.addEventListener("input", () => {
  clearTimeout(minuterieTexte);
  minuterieTexte = setTimeout(appliquerTexte, 400);
});
champCoordonnees.addEventListener("keydown", (evenement) => {
  if (evenement.key === "Enter") {
    evenement.preventDefault(); // ne pas envoyer le formulaire
    clearTimeout(minuterieTexte);
    appliquerTexte();
  }
});

// --- Recherche de lieu (Nominatim via le serveur) : zoome sur la zone, ne place pas le marqueur -----

let minuterieRecherche;
let numeroRecherche = 0;

function viderResultats() {
  listeResultats.replaceChildren();
  listeResultats.hidden = true;
}

function afficherResultats(resultats) {
  viderResultats();
  if (!resultats.length) {
    etatRecherche.textContent = "Aucun lieu trouvé.";
    return;
  }
  etatRecherche.textContent = "";
  for (const lieu of resultats) {
    const element = document.createElement("li");
    const bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "min-h-11 w-full rounded-lg border border-bordure bg-surface px-3 py-2 text-left text-corps hover:border-primaire/40 hover:bg-fond";
    bouton.textContent = lieu.nom;
    bouton.addEventListener("click", () => {
      const [ouest, sud, est, nord] = lieu.bbox;
      carte.fitBounds([[ouest, sud], [est, nord]], { padding: 30, maxZoom: 16 });
      viderResultats();
      etatRecherche.textContent = "Zone affichée : touchez maintenant la carte à l'emplacement exact du chantier.";
    });
    element.append(bouton);
    listeResultats.append(element);
  }
  listeResultats.hidden = false;
}

async function rechercher(texte) {
  const numero = ++numeroRecherche;
  etatRecherche.textContent = "Recherche…";
  const pays = document.querySelector("[name=pays]").value.trim();
  try {
    const reponse = await fetch(`/api/recherche-lieu?${new URLSearchParams({ q: texte, pays })}`, { headers: { Accept: "application/json" } });
    if (!reponse.ok) throw new Error();
    const { resultats } = await reponse.json();
    if (numero === numeroRecherche) afficherResultats(resultats); // ignore les réponses dépassées
  } catch {
    if (numero === numeroRecherche) etatRecherche.textContent = "Recherche indisponible. Collez des coordonnées ou touchez la carte.";
  }
}

champRecherche.addEventListener("input", () => {
  clearTimeout(minuterieRecherche);
  const texte = champRecherche.value.trim();
  if (texte.length < 3) {
    numeroRecherche++;
    viderResultats();
    etatRecherche.textContent = "";
    return;
  }
  minuterieRecherche = setTimeout(() => rechercher(texte), 500);
});
champRecherche.addEventListener("keydown", (evenement) => {
  if (evenement.key === "Enter") {
    evenement.preventDefault();
    clearTimeout(minuterieRecherche);
    if (champRecherche.value.trim().length >= 3) rechercher(champRecherche.value.trim());
  }
});

// --- Centrer sur ma position (sur le terrain) ---------------------------------------------------

boutonPosition.addEventListener("click", async () => {
  const texteBouton = boutonPosition.querySelector("span") ?? boutonPosition; // garde l'icône du bouton
  const libelle = texteBouton.textContent;
  boutonPosition.disabled = true;
  texteBouton.textContent = "Localisation…";
  try {
    const { lon, lat, precision } = await localiser();
    carte.flyTo({ center: [lon, lat], zoom: 18 });
    if (!marqueur) {
      // Premier placement : on pose le marqueur sur la position GPS, à ajuster si besoin.
      placer(lon, lat);
      affichage.textContent += ` (GPS, précision ± ${Math.round(precision)} m)`;
    } else {
      etatRecherche.textContent = "Carte centrée sur votre position : touchez-la pour y déplacer le chantier.";
    }
  } catch (erreur) {
    etatRecherche.textContent = erreur.message;
  } finally {
    boutonPosition.disabled = false;
    texteBouton.textContent = libelle;
  }
});
