// Outils cartographiques partagés. MapLibre GL JS est chargé globalement par _carte_ressources.html.
// Coordonnées toujours dans l'ordre GeoJSON (RFC 7946) : [longitude, latitude].
import { circle } from "https://cdn.jsdelivr.net/npm/@turf/circle@7.4.0/+esm";

const config = JSON.parse(document.getElementById("config-carte").textContent);

export const CENTRE_BENIN = [2.35, 9.3];
export const ZOOM_BENIN = 6;
export const ZOOM_CHANTIER = 17;
const ID_SATELLITE = "fond-satellite";
const ID_RAYON = "rayon-verification";

/** Couleur de la charte, lue dans les variables CSS du thème Tailwind (aucune couleur codée en dur). */
export function couleur(nom) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--color-${nom}`).trim();
}

const TEXTES_FR = {
  "NavigationControl.ZoomIn": "Zoomer",
  "NavigationControl.ZoomOut": "Dézoomer",
  "CooperativeGesturesHandler.WindowsHelpText": "Ctrl + molette pour zoomer sur la carte",
  "CooperativeGesturesHandler.MacHelpText": "⌘ + molette pour zoomer sur la carte",
  "CooperativeGesturesHandler.MobileHelpText": "Utilisez deux doigts pour déplacer la carte",
};

/** Bouton Plan / Satellite : l'imagerie est une couche raster posée sous les libellés du plan. */
class BasculeFond {
  constructor(satellite) {
    this.satellite = satellite;
  }

  onAdd(carte) {
    this.carte = carte;
    this.element = document.createElement("div");
    this.element.className = "maplibregl-ctrl controle-segmente";
    this.element.setAttribute("role", "group");
    this.element.setAttribute("aria-label", "Fond de carte");
    this.boutons = ["Plan", "Satellite"].map((libelle, index) => {
      const bouton = document.createElement("button");
      bouton.type = "button";
      bouton.textContent = libelle;
      bouton.addEventListener("click", () => this.choisir(index === 1));
      this.element.append(bouton);
      return bouton;
    });
    this.majBoutons();
    return this.element;
  }

  onRemove() {
    this.element.remove();
  }

  choisir(satellite) {
    this.satellite = satellite;
    if (this.carte.getLayer(ID_SATELLITE)) {
      this.carte.setLayoutProperty(ID_SATELLITE, "visibility", satellite ? "visible" : "none");
    }
    this.majBoutons();
  }

  majBoutons() {
    this.boutons.forEach((bouton, index) => bouton.setAttribute("aria-pressed", String((index === 1) === this.satellite)));
  }
}

function ajouterFondSatellite(carte, visible) {
  carte.addSource(ID_SATELLITE, {
    type: "raster",
    tiles: [config.satellite_tiles_url],
    tileSize: 256,
    maxzoom: 19,
    attribution: config.satellite_attribution,
  });
  // Sous la première couche de texte : les noms de quartiers et de rues restent lisibles sur l'imagerie.
  const premierTexte = carte.getStyle().layers.find((couche) => couche.type === "symbol")?.id;
  carte.addLayer(
    { id: ID_SATELLITE, type: "raster", source: ID_SATELLITE, layout: { visibility: visible ? "visible" : "none" } },
    premierTexte,
  );
}

/** Crée une carte avec bascule Plan / Satellite et attribution toujours visible. */
export function creerCarte(conteneur, { satellite = false, centre = CENTRE_BENIN, zoom = ZOOM_BENIN, cooperatif = false } = {}) {
  const carte = new maplibregl.Map({
    container: conteneur,
    style: config.plan_style_url,
    center: centre,
    zoom,
    attributionControl: { compact: false },
    cooperativeGestures: cooperatif,
    locale: TEXTES_FR,
  });
  const bascule = new BasculeFond(satellite);
  carte.addControl(bascule, "top-left");
  carte.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  carte.once("load", () => {
    ajouterFondSatellite(carte, bascule.satellite);
    cartesChargees.add(carte);
  });
  return carte;
}

// « load » n'est émis qu'une fois, et carte.loaded() redevient faux pendant le chargement des tuiles :
// on retient donc les cartes déjà chargées.
const cartesChargees = new WeakSet();

/** Promesse résolue quand le style est chargé (sources et couches utilisables). */
export function quandChargee(carte) {
  return cartesChargees.has(carte) ? Promise.resolve(carte) : new Promise((ok) => carte.once("load", () => ok(carte)));
}

/** Marqueur agrandi, pour être déplacé facilement au doigt. */
export function nouveauMarqueur({ deplacable = false } = {}) {
  return new maplibregl.Marker({ color: couleur("primaire"), scale: 1.4, draggable: deplacable });
}

/** Dessine (ou redessine) un cercle de rayonM mètres autour de [lon, lat], sous l'identifiant `id`. */
export function dessinerCercle(carte, id, centre, rayonM, nomCouleur = "primaire") {
  const donnees = circle(centre, rayonM, { steps: 96, units: "meters" });
  const source = carte.getSource(id);
  if (source) {
    source.setData(donnees);
    return;
  }
  // Contour en pointillés, remplissage à 8 % d'opacité : le cercle ne masque pas l'imagerie.
  carte.addSource(id, { type: "geojson", data: donnees });
  carte.addLayer({ id: `${id}-fond`, type: "fill", source: id, paint: { "fill-color": couleur(nomCouleur), "fill-opacity": 0.08 } });
  carte.addLayer({
    id: `${id}-contour`,
    type: "line",
    source: id,
    paint: { "line-color": couleur(nomCouleur), "line-width": 2, "line-dasharray": [2, 2] },
  });
}

/** Cercle du rayon de vérification des photos autour de [lon, lat]. */
export function dessinerRayon(carte, centre, rayonM) {
  dessinerCercle(carte, ID_RAYON, centre, rayonM);
}

/** Zoom qui montre le cercle en entier : 17 pour 100 m, 14 pour 1 km environ. */
export function zoomPourRayon(rayonM) {
  return Math.min(18, Math.max(13, 17 - Math.log2(rayonM / 100)));
}

/** Position GPS du navigateur : { lon, lat, precision } en mètres. */
export function localiser() {
  return new Promise((ok, echec) => {
    if (!navigator.geolocation) {
      echec(new Error("La géolocalisation n'est pas disponible sur cet appareil."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => ok({ lon: p.coords.longitude, lat: p.coords.latitude, precision: p.coords.accuracy }),
      (erreur) => echec(new Error(erreur.code === 1 ? "Autorisez l'accès à votre position pour utiliser ce bouton." : "Position introuvable pour le moment.")),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}
