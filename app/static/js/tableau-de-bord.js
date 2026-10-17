// Carte du tableau de bord : chantier (marqueur, cercle du rayon, emprise) et photos des rapports publiés,
// regroupées aux petites échelles. Les photos suivent les filtres de la timeline (attribut data-geojson).
// Chantier pas encore positionné : ni marqueur ni cercle, la carte se cale sur les photos.
import { couleur, creerCarte, dessinerRayon, nouveauMarqueur, quandChargee, zoomPourRayon } from "./carte.js";

const conteneur = document.getElementById("carte-chantier");
const centre = conteneur.dataset.latitude ? [Number(conteneur.dataset.longitude), Number(conteneur.dataset.latitude)] : null;
const rayon = Number(conteneur.dataset.rayon);
const emprise = conteneur.dataset.emprise ? JSON.parse(conteneur.dataset.emprise) : null;
const SOURCE = "photos";
const CLASSES_BADGE = {
  succes: "bg-succes-fond text-succes",
  danger: "bg-danger-fond text-danger",
  neutre: "bg-neutre-fond text-neutre",
};

const carte = creerCarte(conteneur, { satellite: true, ...(centre && { centre, zoom: zoomPourRayon(rayon) }), cooperatif: true });
if (centre) nouveauMarqueur().setLngLat(centre).addTo(carte);

let adresseGeojson = document.getElementById("zone-timeline").dataset.geojson;
let photos = { type: "FeatureCollection", features: [] };
let popup = null;

function ajouterCouches() {
  if (centre) dessinerRayon(carte, centre, rayon);
  if (emprise) {
    carte.addSource("emprise", { type: "geojson", data: emprise });
    carte.addLayer({ id: "emprise-fond", type: "fill", source: "emprise", paint: { "fill-color": couleur("primaire"), "fill-opacity": 0.08 } });
    carte.addLayer({ id: "emprise-contour", type: "line", source: "emprise", paint: { "line-color": couleur("primaire"), "line-width": 2 } });
  }
  carte.addSource(SOURCE, { type: "geojson", data: photos, cluster: true, clusterRadius: 36, clusterMaxZoom: 17 });
  carte.addLayer({
    id: "groupes",
    type: "circle",
    source: SOURCE,
    filter: ["has", "point_count"],
    paint: {
      "circle-color": couleur("primaire"),
      "circle-radius": ["step", ["get", "point_count"], 16, 10, 20, 50, 26],
      "circle-stroke-color": couleur("white"),
      "circle-stroke-width": 2,
    },
  });
  carte.addLayer({
    id: "groupes-nombre",
    type: "symbol",
    source: SOURCE,
    filter: ["has", "point_count"],
    layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Bold"], "text-size": 13 },
    paint: { "text-color": couleur("white") },
  });
  carte.addLayer({
    id: "photos-points",
    type: "circle",
    source: SOURCE,
    filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": ["match", ["get", "ton"], "succes", couleur("succes"), "danger", couleur("danger"), couleur("neutre")],
      "circle-radius": 7,
      "circle-stroke-color": couleur("white"),
      "circle-stroke-width": 2,
    },
  });

  carte.on("click", "groupes", async (evenement) => {
    const groupe = evenement.features[0];
    const zoom = await carte.getSource(SOURCE).getClusterExpansionZoom(groupe.properties.cluster_id);
    carte.easeTo({ center: groupe.geometry.coordinates, zoom });
  });
  carte.on("click", "photos-points", (evenement) => ouvrirPopup(evenement.features[0]));
  for (const couche of ["groupes", "photos-points"]) {
    carte.on("mouseenter", couche, () => { carte.getCanvas().style.cursor = "pointer"; });
    carte.on("mouseleave", couche, () => { carte.getCanvas().style.cursor = ""; });
  }
}

// Contenu construit en DOM (textContent) : aucune donnée n'est interprétée comme du HTML.
function element(balise, classes, texte) {
  const noeud = document.createElement(balise);
  if (classes) noeud.className = classes;
  if (texte !== undefined) noeud.textContent = texte;
  return noeud;
}

function ouvrirPopup(photo) {
  const p = photo.properties;
  const bloc = element("div", "w-56 overflow-hidden rounded-carte");
  const image = element("img", "h-28 w-full bg-neutre-fond object-cover");
  image.src = p.miniature;
  image.alt = `Photo${p.etape ? ` — ${p.etape}` : ""} — ${p.date}`;
  image.width = 224;
  image.height = 112;
  const corps = element("div", "grid gap-1.5 p-3");
  corps.append(
    element("p", "text-libelle text-texte-doux", p.date),
    element("span", `w-fit rounded-full px-2 py-0.5 text-libelle font-medium ${CLASSES_BADGE[p.ton]}`, p.badge),
  );
  if (p.mention) corps.append(element("p", "text-libelle text-texte-doux", p.mention));
  if (p.distance) corps.append(element("p", "text-libelle tabular-nums", `À ${p.distance} du chantier`));
  const lien = element("a", "text-libelle font-medium", "Voir le rapport");
  lien.href = `/rapports/${p.rapport_id}`;
  corps.append(lien);
  bloc.append(image, corps);
  popup?.remove();
  popup = new maplibregl.Popup({ offset: 14, maxWidth: "none" }).setLngLat(photo.geometry.coordinates).setDOMContent(bloc).addTo(carte);
}

async function chargerPhotos() {
  const reponse = await fetch(adresseGeojson, { headers: { Accept: "application/geo+json" } }).catch(() => null);
  if (!reponse?.ok) return;
  photos = await reponse.json();
  await quandChargee(carte);
  carte.getSource(SOURCE).setData(photos);
}

function voirToutesLesPhotos() {
  const points = photos.features.map((photo) => photo.geometry.coordinates);
  if (centre) points.push(centre);
  if (!points.length) return;
  const emprisePhotos = new maplibregl.LngLatBounds(points[0], points[0]);
  for (const point of points) emprisePhotos.extend(point);
  carte.fitBounds(emprisePhotos, { padding: 48, maxZoom: 18 });
}

function centrerSurPhoto(id) {
  const photo = photos.features.find((f) => f.properties.id === id);
  if (!photo) return;
  conteneur.scrollIntoView({ behavior: "smooth", block: "center" });
  carte.flyTo({ center: photo.geometry.coordinates, zoom: Math.max(carte.getZoom(), 18) });
  carte.once("moveend", () => ouvrirPopup(photo));
}

quandChargee(carte).then(ajouterCouches);
chargerPhotos().then(() => {
  if (conteneur.dataset.photo) centrerSurPhoto(conteneur.dataset.photo);
  else if (!centre) voirToutesLesPhotos();
});

document.getElementById("voir-photos").addEventListener("click", voirToutesLesPhotos);
document.addEventListener("voir-sur-carte", (evenement) => centrerSurPhoto(evenement.detail.photo));

// Les filtres de la timeline remplacent sa zone (HTMX) : la carte suit les mêmes filtres.
document.body.addEventListener("htmx:afterSwap", () => {
  const zone = document.getElementById("zone-timeline");
  if (zone && zone.dataset.geojson !== adresseGeojson) {
    adresseGeojson = zone.dataset.geojson;
    popup?.remove();
    chargerPhotos();
  }
});

// Téléphone : carte agrandissable en plein écran.
const boutonAgrandir = document.getElementById("agrandir-carte");
function basculerPleinEcran(actif) {
  conteneur.classList.toggle("carte-plein-ecran", actif);
  boutonAgrandir.setAttribute("aria-pressed", String(actif));
  boutonAgrandir.querySelector("span").textContent = actif ? "Réduire" : "Agrandir";
  boutonAgrandir.classList.toggle("fixed", actif);
  boutonAgrandir.classList.toggle("bottom-4", actif);
  boutonAgrandir.classList.toggle("right-4", actif);
  boutonAgrandir.classList.toggle("z-50", actif);
  boutonAgrandir.classList.toggle("bg-surface", actif);
  boutonAgrandir.classList.toggle("shadow-flottant", actif);
  carte.resize();
}
boutonAgrandir.addEventListener("click", () => basculerPleinEcran(!conteneur.classList.contains("carte-plein-ecran")));
document.addEventListener("keydown", (evenement) => {
  if (evenement.key === "Escape" && conteneur.classList.contains("carte-plein-ecran")) basculerPleinEcran(false);
});
