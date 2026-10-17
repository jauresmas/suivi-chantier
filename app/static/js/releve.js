// Relevé de la position du terrain par un proche, sans compte : consignes, mesure pendant 60 s,
// envoi des mesures brutes, photos facultatives. Le calcul de la position est fait par le serveur
// (app/geo/releve.py) : ce script ne fait que mesurer et afficher un indicateur de signal.
import { compresser } from "./compression.js";

const racine = document.getElementById("releve");
const url = racine.dataset.url;
const jetonCsrf = document.querySelector('meta[name="csrf-token"]').content;
const PRECISION_MAX_M = Number(racine.dataset.precisionMax); // au-delà, la mesure est ignorée
const PHOTOS_MAX = Number(racine.dataset.photosMax);

const DUREE_S = 60;
const ARRET_ANTICIPE_S = 30; // « Terminer maintenant » possible après 30 s…
const MESURES_ANTICIPE = 15; // …s'il y a déjà 15 bonnes mesures
const ATTENTE_PREMIERE_MESURE_S = 45;
const ATTENTE_LOCALISATION_S = 10;
const ECHECS_AVANT_FORCER = 2;
const SEUIL_BON_M = 10; // mêmes seuils que qualite_signal() côté serveur
const SEUIL_MOYEN_M = 25;
const CIRCONFERENCE = 2 * Math.PI * 52;

const $ = (id) => document.getElementById(id);
const ecrans = racine.querySelectorAll("[data-ecran]");

/** Affiche un seul écran et y place le focus (lecteurs d'écran). */
function afficher(nom) {
  for (const ecran of ecrans) ecran.hidden = ecran.dataset.ecran !== nom;
  const titre = racine.querySelector(`[data-ecran="${nom}"] h1`);
  window.scrollTo({ top: 0 });
  titre?.focus({ preventScroll: true });
}

// --- Mesure --------------------------------------------------------------------------------------

let mesures = [];
let suivi = null;
let debut = null;
let minuterie = null;
let verrouEcran = null;
let echecs = 0;
let positionIndisponible = false;

const signal = $("signal");
const CLASSES_SIGNAL = { bon: "text-succes", moyen: "text-alerte", faible: "text-danger" };

function afficherSignal(precision) {
  const niveau = precision <= SEUIL_BON_M ? "bon" : precision <= SEUIL_MOYEN_M ? "moyen" : "faible";
  signal.textContent = `Signal : ${niveau}`;
  signal.className = `flex min-h-8 items-center gap-2 font-semibold ${CLASSES_SIGNAL[niveau]}`;
}

function surPosition(p) {
  mesures.push({ lat: p.coords.latitude, lon: p.coords.longitude, precision: p.coords.accuracy, t: p.timestamp });
  afficherSignal(p.coords.accuracy);
  if (debut === null) {
    debut = performance.now();
    $("consigne-mesure").textContent = "Restez immobile, le téléphone à la main.";
  }
}

function surErreur(erreur) {
  if (erreur.code === erreur.PERMISSION_DENIED) {
    arreterMesure();
    afficher("autorisation");
  } else if (erreur.code === erreur.POSITION_UNAVAILABLE) {
    // Souvent passager (le navigateur en émet entre deux mesures) : la mesure continue. Sans aucune
    // mesure au bout de 10 s, la localisation du téléphone est sans doute désactivée.
    positionIndisponible = true;
  }
  // Délai dépassé : la mesure continue, le compte à rebours gère l'absence de signal.
}

async function garderEcranAllume() {
  try {
    verrouEcran = await navigator.wakeLock?.request("screen");
  } catch {
    verrouEcran = null; // non pris en charge : sans conséquence
  }
}

function arreterMesure() {
  if (suivi !== null) navigator.geolocation.clearWatch(suivi);
  suivi = null;
  clearInterval(minuterie);
  verrouEcran?.release().catch(() => {});
  verrouEcran = null;
}

function demarrerMesure() {
  if (!window.isSecureContext) {
    $("titre-impossible").textContent = "Ce lien ne peut pas fonctionner ainsi";
    $("texte-impossible").textContent = "Demandez à la personne qui vous l'a envoyé de vous le renvoyer.";
    afficher("impossible");
    return;
  }
  if (!("geolocation" in navigator)) {
    afficher("impossible");
    return;
  }
  arreterMesure();
  mesures = [];
  debut = null;
  positionIndisponible = false;
  const lancement = performance.now();
  $("consigne-mesure").textContent = "Recherche de votre position…";
  signal.textContent = "Signal : recherche…";
  signal.className = "flex min-h-8 items-center gap-2 font-semibold text-texte-doux";
  $("terminer").hidden = true;
  majCompteur(0);
  afficher("mesure");
  garderEcranAllume();
  suivi = navigator.geolocation.watchPosition(surPosition, surErreur, { enableHighAccuracy: true, maximumAge: 0 });
  minuterie = setInterval(() => {
    if (debut === null) {
      const attente = (performance.now() - lancement) / 1000;
      if (positionIndisponible && attente > ATTENTE_LOCALISATION_S) {
        arreterMesure();
        afficher("autorisation"); // explique aussi comment activer la localisation
        return;
      }
      // Aucune position après 45 s : signal trop faible à cet endroit.
      if (attente > ATTENTE_PREMIERE_MESURE_S) {
        arreterMesure();
        echecs++;
        afficherFaible(false);
      }
      return;
    }
    const ecoule = (performance.now() - debut) / 1000;
    majCompteur(ecoule);
    const bonnes = mesures.filter((m) => m.precision <= PRECISION_MAX_M).length;
    $("terminer").hidden = !(ecoule >= ARRET_ANTICIPE_S && bonnes >= MESURES_ANTICIPE);
    if (ecoule >= DUREE_S) finirMesure();
  }, 250);
}

function majCompteur(ecoule) {
  const fraction = Math.min(1, ecoule / DUREE_S);
  $("progression").setAttribute("stroke-dashoffset", String(CIRCONFERENCE * (1 - fraction)));
  $("secondes").textContent = String(Math.max(0, Math.ceil(DUREE_S - ecoule)));
}

function finirMesure() {
  arreterMesure();
  navigator.vibrate?.(200);
  envoyerMesures(false);
}

// --- Envoi des mesures ---------------------------------------------------------------------------

let forcerDernier = false;

async function envoyerMesures(forcer) {
  forcerDernier = forcer;
  afficher("envoi");
  let reponse;
  try {
    reponse = await fetch(`${url}/mesures`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": jetonCsrf },
      body: JSON.stringify({ mesures, horloge: Date.now(), forcer }),
    });
  } catch {
    afficher("hors-ligne"); // les mesures restent en mémoire
    return;
  }
  const corps = await reponse.json().catch(() => ({}));
  if (reponse.status === 201) {
    afficher("photos");
    return;
  }
  if (reponse.ok && corps.statut === "signal_faible") {
    echecs++;
    afficherFaible(corps.exploitable);
    return;
  }
  if (reponse.status === 404 || reponse.status === 410) {
    // Lien annulé, expiré ou déjà utilisé entre-temps : le serveur donne un message poli.
    $("titre-impossible").textContent = "Ce lien n'est plus valable";
    $("texte-impossible").textContent = corps.detail ?? "Demandez un nouveau lien à la personne qui vous l'a envoyé.";
    afficher("impossible");
    return;
  }
  if (reponse.status >= 500) {
    afficher("hors-ligne");
    return;
  }
  // Mesures refusées (heure du téléphone incohérente…) : un nouveau relevé règle le problème.
  echecs++;
  afficherFaible(false);
}

function afficherFaible(exploitable) {
  $("envoyer-quand-meme").hidden = !(exploitable && echecs >= ECHECS_AVANT_FORCER);
  afficher("faible");
}

// --- Photos du terrain ---------------------------------------------------------------------------

const champPhoto = $("photo-terrain");
const etatPhotos = $("etat-photos");
const listePhotos = $("photos-envoyees");
let photosEnvoyees = 0;

champPhoto.addEventListener("change", async () => {
  const fichier = champPhoto.files[0];
  champPhoto.value = "";
  if (!fichier) return;
  etatPhotos.textContent = "Envoi de la photo…";
  let blob;
  try {
    blob = await compresser(fichier);
  } catch {
    blob = fichier; // le serveur compressera l'original
  }
  const donnees = new FormData();
  donnees.append("fichier", blob, "photo.jpg");
  let reponse;
  try {
    reponse = await fetch(`${url}/photos`, { method: "POST", headers: { "X-CSRF-Token": jetonCsrf, Accept: "application/json" }, body: donnees });
  } catch {
    etatPhotos.textContent = "La photo n'est pas partie : vérifiez la connexion et reprenez-la.";
    return;
  }
  if (!reponse.ok) {
    etatPhotos.textContent = reponse.status === 409 ? "Merci, c'est suffisant." : "Cette photo n'a pas pu être envoyée. Essayez d'en prendre une autre.";
    if (reponse.status === 409) $("prendre-photo").hidden = true;
    return;
  }
  const { restantes } = await reponse.json();
  photosEnvoyees++;
  const element = document.createElement("li");
  const vignette = document.createElement("img");
  vignette.src = URL.createObjectURL(blob);
  vignette.alt = `Photo ${photosEnvoyees}`;
  vignette.className = "aspect-square w-full rounded-lg bg-neutre-fond object-cover";
  element.append(vignette);
  listePhotos.append(element);
  etatPhotos.textContent = restantes > 0 ? "Photo envoyée. Vous pouvez en prendre une autre." : "Photos envoyées. Merci !";
  $("prendre-photo").hidden = restantes <= 0;
  $("terminer-photos").textContent = "Terminer";
});

// --- Boutons -------------------------------------------------------------------------------------

$("commencer").addEventListener("click", demarrerMesure);
$("terminer").addEventListener("click", finirMesure);
$("envoyer-quand-meme").addEventListener("click", () => envoyerMesures(true));
$("terminer-photos").addEventListener("click", () => afficher("merci"));
for (const bouton of racine.querySelectorAll('[data-action="recommencer"]')) bouton.addEventListener("click", demarrerMesure);
for (const bouton of racine.querySelectorAll('[data-action="renvoyer"]')) bouton.addEventListener("click", () => envoyerMesures(forcerDernier));

// Écran éteint ou application changée pendant la mesure : le verrou d'écran est perdu, on le reprend.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && suivi !== null) garderEcranAllume();
});
