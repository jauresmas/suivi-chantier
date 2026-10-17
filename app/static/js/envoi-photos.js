// Envoi de photos depuis le chantier : position GPS, EXIF, compression, puis envoi une par une avec reprise.
// La position du navigateur est la source principale : iOS et Android retirent souvent le GPS de l'EXIF.
// exifr est chargé globalement par la page (version épinglée, intégrité vérifiée).
import { compresser } from "./compression.js";

const page = document.getElementById("envoi-photos");
const chantierId = page.dataset.chantier;
const jetonCsrf = document.querySelector('meta[name="csrf-token"]').content;
const etatGps = document.getElementById("etat-gps");
const aideGps = document.getElementById("aide-gps");
const avertissementHttps = document.getElementById("avertissement-https");
const champFichiers = document.getElementById("fichiers");
const fileEnvoi = document.getElementById("file-envoi");
const resume = document.getElementById("resume");

const OPTIONS_GPS = { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 };
const AGE_MAX_POSITION_MS = 2 * 60 * 1000;
const TAILLE_MAX_OCTETS = 15 * 1024 * 1024;
const TOLERANCE_FUTUR_MS = 5 * 60 * 1000;
const DELAIS_REESSAI_MS = [2000, 5000]; // 3 tentatives au total
const ETATS_EN_ATTENTE = new Set(["preparation", "attente", "envoi", "erreur"]);

// --- Position GPS : démarrée dès l'ouverture pour laisser le GPS se stabiliser ----------------------

let dernierePosition = null;
let geolocalisationRefusee = false;

const versPosition = (p) => ({ lat: p.coords.latitude, lon: p.coords.longitude, precision: p.coords.accuracy, horodatage: p.timestamp });

// Classes écrites en entier : Tailwind ne génère que les classes qu'il lit telles quelles.
const CLASSES_GPS = {
  bonne: "text-succes",
  moyenne: "text-alerte",
  faible: "text-danger",
  attente: "text-texte-doux",
};
const CLASSES_BADGE = {
  succes: "bg-succes-fond text-succes",
  danger: "bg-danger-fond text-danger",
  alerte: "bg-alerte-fond text-alerte",
  neutre: "bg-neutre-fond text-neutre",
};
const CLASSE_BADGE = "inline-flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-libelle font-medium";

function afficherEtatGps(texte, niveau) {
  etatGps.querySelector("[data-texte]").textContent = texte;
  etatGps.className = `flex items-center gap-2 font-semibold ${CLASSES_GPS[niveau]}`;
}

function memoriserPosition(p) {
  dernierePosition = versPosition(p);
  const precision = Math.round(dernierePosition.precision);
  afficherEtatGps(`Position : ±${precision} m`, precision <= 30 ? "bonne" : precision <= 100 ? "moyenne" : "faible");
}

function surErreurGps(erreur) {
  if (erreur.code === erreur.PERMISSION_DENIED) {
    geolocalisationRefusee = true;
    afficherEtatGps("Position refusée : les photos seront envoyées sans position.", "faible");
    aideGps.hidden = false;
  } else if (!dernierePosition) {
    afficherEtatGps("Position introuvable pour l'instant… À l'extérieur, le GPS capte mieux.", "attente");
  }
}

const geolocalisationPossible = () => window.isSecureContext && "geolocation" in navigator && !geolocalisationRefusee;

if (!window.isSecureContext) {
  avertissementHttps.hidden = false;
  afficherEtatGps("Position indisponible : connexion non sécurisée.", "faible");
} else if (!("geolocation" in navigator)) {
  afficherEtatGps("Cet appareil ne fournit pas de position.", "faible");
} else {
  navigator.geolocation.watchPosition(memoriserPosition, surErreurGps, OPTIONS_GPS);
}

/** Dernière position si elle a moins de 2 minutes, sinon un nouveau relevé (ou null). */
function positionPourPhoto() {
  if (dernierePosition && Date.now() - dernierePosition.horodatage < AGE_MAX_POSITION_MS) return Promise.resolve(dernierePosition);
  if (!geolocalisationPossible()) return Promise.resolve(null);
  return new Promise((ok) => {
    navigator.geolocation.getCurrentPosition(
      (p) => {
        memoriserPosition(p);
        ok(dernierePosition);
      },
      () => ok(null),
      OPTIONS_GPS,
    );
  });
}

// --- Préparation locale : EXIF et empreinte sur l'original, puis compression ----------------------

async function lireExif(fichier) {
  if (typeof exifr === "undefined") return {};
  const [gps, tags] = await Promise.all([
    exifr.gps(fichier).catch(() => null),
    exifr.parse(fichier, ["DateTimeOriginal"]).catch(() => null),
  ]);
  return {
    gps: gps && Number.isFinite(gps.latitude) && Number.isFinite(gps.longitude) ? gps : null,
    dateOriginale: tags?.DateTimeOriginal instanceof Date && !Number.isNaN(tags.DateTimeOriginal.getTime()) ? tags.DateTimeOriginal : null,
  };
}

async function empreinteSha256(fichier) {
  if (!crypto.subtle) return ""; // indisponible hors contexte sécurisé
  const condensat = await crypto.subtle.digest("SHA-256", await fichier.arrayBuffer());
  return [...new Uint8Array(condensat)].map((octet) => octet.toString(16).padStart(2, "0")).join("");
}

/** Navigateur d'abord, puis EXIF, sinon aucune position (la photo part quand même). */
function choisirPosition(navigateur, exif) {
  if (navigateur) {
    return {
      source_position: "navigateur",
      latitude: navigateur.lat,
      longitude: navigateur.lon,
      precision_m: navigateur.precision,
      position_obtenue_le: new Date(navigateur.horodatage).toISOString(),
    };
  }
  if (exif.gps) return { source_position: "exif", latitude: exif.gps.latitude, longitude: exif.gps.longitude };
  return { source_position: "aucune" };
}

function datePriseDeVue(fichier, exif) {
  const date = exif.dateOriginale ?? (fichier.lastModified ? new Date(fichier.lastModified) : null);
  // Horloge d'appareil photo déréglée : le serveur refuserait une date future.
  return date && date.getTime() <= Date.now() + TOLERANCE_FUTUR_MS ? date.toISOString() : "";
}

// --- File d'envoi ----------------------------------------------------------------------------------

const envois = [];
let rapportId = page.dataset.rapport || null; // brouillon repris, s'il existe
let envoiEnCours = false;
let preparations = Promise.resolve();

class ErreurEnvoi extends Error {
  constructor(message, definitive = false) {
    super(message);
    this.definitive = definitive; // inutile de réessayer (fichier refusé, droits…)
  }
}

function creerLigne(numero) {
  const ligne = document.createElement("li");
  ligne.className = "envoi flex items-start gap-3 rounded-carte border border-bordure bg-surface p-3 shadow-carte data-[etat=erreur]:border-danger data-[etat=rejetee]:border-danger";
  const vignette = document.createElement("img");
  vignette.className = "size-18 shrink-0 rounded-lg bg-neutre-fond object-cover";
  vignette.width = 72;
  vignette.height = 72;
  vignette.alt = `Photo ${numero}`;
  const infos = document.createElement("div");
  infos.className = "infos grid min-w-0 flex-1 gap-1.5";
  const titre = document.createElement("strong");
  titre.textContent = `Photo ${numero}`;
  const progression = document.createElement("progress");
  progression.max = 1;
  progression.value = 0;
  progression.className = "h-2 w-full accent-primaire";
  const statut = document.createElement("span");
  statut.className = "text-libelle text-texte-doux";
  const badge = document.createElement("span");
  badge.className = CLASSE_BADGE;
  badge.hidden = true;
  const verification = document.createElement("span");
  verification.className = CLASSE_BADGE;
  verification.hidden = true;
  const alertes = document.createElement("details");
  alertes.className = "text-libelle [&_summary]:cursor-pointer [&_summary]:font-medium [&_summary]:text-primaire [&_ul]:mt-1 [&_ul]:list-disc [&_ul]:pl-5";
  alertes.hidden = true;
  const reessayer = document.createElement("button");
  reessayer.type = "button";
  reessayer.className = "inline-flex min-h-11 w-fit items-center rounded-lg border border-bordure bg-surface px-3 font-medium hover:bg-fond";
  reessayer.textContent = "Réessayer";
  reessayer.hidden = true;
  infos.append(titre, progression, statut, badge, verification, alertes, reessayer);
  ligne.append(vignette, infos);
  fileEnvoi.prepend(ligne);

  const envoi = { ligne, vignette, progression, statut, badge, verification, alertes, reessayer, etat: "preparation" };
  reessayer.addEventListener("click", () => {
    reessayer.hidden = true;
    changerEtat(envoi, "attente", "En attente d'envoi…");
    lancerEnvois();
  });
  return envoi;
}

function changerEtat(envoi, etat, texte) {
  envoi.etat = etat;
  envoi.ligne.dataset.etat = etat;
  envoi.statut.textContent = texte;
  majResume();
}

function majResume() {
  const envoyees = envois.filter((e) => e.etat === "ok").length;
  const restantes = envois.filter((e) => ETATS_EN_ATTENTE.has(e.etat)).length;
  resume.textContent = envois.length ? `${envoyees} photo${envoyees > 1 ? "s" : ""} envoyée${envoyees > 1 ? "s" : ""}${restantes ? `, ${restantes} en cours` : ""}.` : "";
}

/** Badge calculé par le serveur : sur le chantier, hors zone (avec distance) ou non vérifiable. */
function afficherVerification(envoi, badge) {
  // Icône et texte : la couleur ne porte jamais seule l'information. L'icône SVG vient du serveur.
  envoi.verification.innerHTML = badge.icone_svg;
  envoi.verification.append(`${badge.avertissement ? " ⚠ " : " "}${badge.texte}`);
  envoi.verification.title = badge.titre;
  envoi.verification.className = `${CLASSE_BADGE} ${CLASSES_BADGE[badge.ton]}`;
  envoi.verification.hidden = false;
  if (badge.mention) {
    const mention = document.createElement("span");
    mention.className = "text-libelle text-texte-doux";
    mention.textContent = badge.mention;
    envoi.verification.after(mention);
  }
  if (!badge.alertes.length) return;
  const resumeAlertes = document.createElement("summary");
  resumeAlertes.textContent = badge.avertissement ? "À vérifier" : "Information";
  const liste = document.createElement("ul");
  for (const libelle of badge.alertes) {
    const element = document.createElement("li");
    element.textContent = libelle;
    liste.append(element);
  }
  envoi.alertes.replaceChildren(resumeAlertes, liste);
  envoi.alertes.hidden = false;
}

function libelleBadge(position) {
  if (position.source_position === "navigateur") return [`GPS ±${Math.round(position.precision_m)} m`, CLASSES_BADGE.succes];
  if (position.source_position === "exif") return ["GPS (photo)", CLASSES_BADGE.succes];
  return ["Sans position", CLASSES_BADGE.alerte];
}

async function preparer(fichier, envoi, positionNavigateur) {
  changerEtat(envoi, "preparation", "Préparation…");
  const [position, exif, empreinte] = await Promise.all([
    positionNavigateur,
    lireExif(fichier).catch(() => ({})),
    empreinteSha256(fichier).catch(() => ""),
  ]);
  let blob;
  try {
    blob = await compresser(fichier);
  } catch {
    blob = fichier; // le serveur compressera l'original
  }
  if (blob.size > TAILLE_MAX_OCTETS) {
    changerEtat(envoi, "rejetee", "Photo trop lourde (15 Mo maximum).");
    return;
  }
  envoi.blob = blob;
  envoi.vignette.src = URL.createObjectURL(blob);
  const choix = choisirPosition(position, exif);
  envoi.champs = { ...choix, prise_le: datePriseDeVue(fichier, exif), empreinte_originale: empreinte };
  const [texte, classe] = libelleBadge(choix);
  envoi.badge.textContent = texte;
  envoi.badge.className = `${CLASSE_BADGE} ${classe}`;
  envoi.badge.hidden = false;
  changerEtat(envoi, "attente", "En attente d'envoi…");
}

async function creerRapport() {
  const reponse = await fetch(`/chantiers/${chantierId}/rapports`, {
    method: "POST",
    headers: { "X-CSRF-Token": jetonCsrf, Accept: "application/json" },
  }).catch(() => null);
  if (!reponse) throw new ErreurEnvoi("Connexion interrompue.");
  if (!reponse.ok) throw new ErreurEnvoi(`Erreur ${reponse.status}`, reponse.status < 500);
  const { id } = await reponse.json();
  formulaire.action = `/rapports/${id}/publier`;
  return id;
}

function envoyer(envoi) {
  return new Promise((ok, echec) => {
    const donnees = new FormData();
    donnees.append("fichier", envoi.blob, "photo.jpg"); // le nom n'est jamais réutilisé par le serveur
    for (const [cle, valeur] of Object.entries(envoi.champs)) {
      if (valeur !== "" && valeur !== undefined) donnees.append(cle, valeur);
    }
    const xhr = new XMLHttpRequest(); // XHR plutôt que fetch : progression de l'envoi
    xhr.open("POST", `/rapports/${rapportId}/photos`);
    xhr.setRequestHeader("X-CSRF-Token", jetonCsrf);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "json";
    xhr.timeout = 180000;
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) envoi.progression.value = e.loaded / e.total;
    });
    xhr.addEventListener("load", () => {
      if (xhr.status === 201) return ok(xhr.response);
      const definitive = xhr.status >= 400 && xhr.status < 500 && ![408, 429].includes(xhr.status);
      echec(new ErreurEnvoi(xhr.response?.detail ?? `Erreur ${xhr.status}`, definitive));
    });
    xhr.addEventListener("error", () => echec(new ErreurEnvoi("Connexion interrompue.")));
    xhr.addEventListener("timeout", () => echec(new ErreurEnvoi("Délai dépassé.")));
    xhr.send(donnees);
  });
}

const pause = (ms) => new Promise((ok) => setTimeout(ok, ms));

async function envoyerAvecReprises(envoi) {
  for (let tentative = 0; ; tentative++) {
    try {
      changerEtat(envoi, "envoi", tentative ? `Nouvel essai (${tentative + 1}/3)…` : "Envoi…");
      envoi.progression.value = 0;
      rapportId ??= await creerRapport(); // un rapport pour toutes les photos de la page
      const photo = await envoyer(envoi);
      URL.revokeObjectURL(envoi.vignette.src);
      envoi.vignette.src = photo.miniature_url;
      envoi.progression.hidden = true;
      afficherVerification(envoi, photo.badge);
      envoi.ligne.dataset.photo = photo.id;
      envoi.ligne.querySelector(".infos").append(boutonRetirer());
      changerEtat(envoi, "ok", "Envoyée");
      return;
    } catch (erreur) {
      if (erreur.definitive) {
        changerEtat(envoi, "rejetee", `Refusée : ${erreur.message}`);
        return;
      }
      if (tentative >= DELAIS_REESSAI_MS.length) {
        changerEtat(envoi, "erreur", `Échec de l'envoi (${erreur.message})`);
        envoi.reessayer.hidden = false;
        return;
      }
      const delai = DELAIS_REESSAI_MS[tentative];
      envoi.statut.textContent = `${erreur.message} Nouvel essai dans ${delai / 1000} s…`;
      await pause(delai);
    }
  }
}

async function lancerEnvois() {
  if (envoiEnCours) return;
  envoiEnCours = true;
  try {
    let envoi;
    while ((envoi = envois.find((e) => e.etat === "attente"))) await envoyerAvecReprises(envoi);
  } finally {
    envoiEnCours = false;
  }
}

champFichiers.addEventListener("change", () => {
  for (const fichier of champFichiers.files) {
    const envoi = creerLigne(envois.length + 1);
    envois.push(envoi);
    const position = positionPourPhoto(); // relevée au moment de la sélection
    preparations = preparations
      .then(() => preparer(fichier, envoi, position))
      .catch(() => changerEtat(envoi, "rejetee", "Photo illisible."))
      .then(lancerEnvois);
  }
  champFichiers.value = ""; // permet de reprendre une photo identique
});

window.addEventListener("beforeunload", (evenement) => {
  if (!publicationEnCours && envois.some((e) => ETATS_EN_ATTENTE.has(e.etat))) {
    evenement.preventDefault();
    evenement.returnValue = "";
  }
});

// --- Photos du brouillon : retrait tant que le rapport n'est pas publié ------------------------------

function boutonRetirer() {
  const bouton = document.createElement("button");
  bouton.type = "button";
  bouton.className = "inline-flex min-h-11 w-fit items-center text-libelle font-medium text-danger hover:underline";
  bouton.textContent = "Retirer cette photo";
  bouton.dataset.supprimer = "";
  return bouton;
}

fileEnvoi.addEventListener("click", async (evenement) => {
  const bouton = evenement.target.closest("[data-supprimer]");
  if (!bouton) return;
  const ligne = bouton.closest(".envoi");
  if (!confirm("Retirer cette photo du rapport ?")) return;
  bouton.disabled = true;
  const reponse = await fetch(`/rapports/${rapportId}/photos/${ligne.dataset.photo}`, {
    method: "DELETE",
    headers: { "X-CSRF-Token": jetonCsrf, Accept: "application/json" },
  }).catch(() => null);
  if (reponse?.status === 204) {
    ligne.remove();
    const envoi = envois.find((e) => e.ligne === ligne);
    if (envoi) envoi.etat = "retiree";
    majResume();
  } else {
    bouton.disabled = false;
    alert("La photo n'a pas pu être retirée. Réessayez.");
  }
});

// --- Formulaire du rapport : étape, avancement, commentaire, publication -----------------------------

const formulaire = document.getElementById("formulaire-rapport");
const choixEtape = document.getElementById("etape");
const blocAvancement = document.getElementById("bloc-avancement");
const curseurAvancement = document.getElementById("avancement");
const valeurAvancement = document.getElementById("valeur-avancement");
const champTexte = document.getElementById("texte");
const compteur = document.getElementById("compteur");
const etatPublication = document.getElementById("etat-publication");
let publicationEnCours = false;

function majAvancement(depuisEtape) {
  const option = choixEtape.selectedOptions[0];
  const avecEtape = Boolean(choixEtape.value);
  blocAvancement.hidden = !avecEtape;
  curseurAvancement.disabled = !avecEtape; // non envoyé sans étape
  if (avecEtape && depuisEtape) curseurAvancement.value = option.dataset.avancement; // valeur actuelle
  valeurAvancement.textContent = curseurAvancement.value;
}

choixEtape.addEventListener("change", () => majAvancement(true));
curseurAvancement.addEventListener("input", () => majAvancement(false));
// Formulaire renvoyé avec une erreur : on garde l'avancement saisi ; sinon, valeur actuelle de l'étape.
majAvancement(!curseurAvancement.dataset.saisi);

champTexte.addEventListener("input", () => {
  compteur.textContent = champTexte.value.length;
});

formulaire.addEventListener("submit", async (evenement) => {
  evenement.preventDefault();
  if (envois.some((e) => ETATS_EN_ATTENTE.has(e.etat))) {
    etatPublication.textContent = "Attendez la fin de l'envoi des photos (ou réessayez celles en échec) avant de publier.";
    return;
  }
  const photosEnvoyees = fileEnvoi.querySelectorAll(".envoi[data-photo]").length;
  if (!photosEnvoyees && !champTexte.value.trim()) {
    etatPublication.textContent = "Ajoutez au moins une photo ou un commentaire avant de publier.";
    return;
  }
  try {
    rapportId ??= await creerRapport(); // rapport sans photo : il faut le créer avant de publier
  } catch {
    etatPublication.textContent = "Connexion impossible pour l'instant. Réessayez dans un moment.";
    return;
  }
  publicationEnCours = true;
  formulaire.submit();
});
