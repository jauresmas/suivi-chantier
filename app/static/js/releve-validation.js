// Carte de validation d'un relevé : point relevé, cercle de précision, mesures retenues en petits points
// et position actuelle du chantier (reliée au point relevé par un trait), sur fond satellite.
import { couleur, creerCarte, dessinerCercle, nouveauMarqueur, quandChargee } from "./carte.js";

const { carte: donnees, precision_m: precision } = JSON.parse(document.getElementById("donnees-releve").textContent);
const conteneur = document.getElementById("carte-releve");

if (donnees.resultat) {
  const point = donnees.resultat.coordinates;
  const ancienne = donnees.ancienne?.coordinates ?? null;
  const carte = creerCarte(conteneur, { satellite: true, centre: point, zoom: 18, cooperatif: true });

  quandChargee(carte).then(() => {
    dessinerCercle(carte, "precision", point, Math.max(precision ?? 0, 1));
    if (ancienne) {
      carte.addSource("ecart", { type: "geojson", data: { type: "LineString", coordinates: [ancienne, point] } });
      carte.addLayer({
        id: "ecart",
        type: "line",
        source: "ecart",
        paint: { "line-color": couleur("white"), "line-width": 2, "line-dasharray": [1, 2] },
      });
    }
    if (donnees.mesures) {
      carte.addSource("mesures", { type: "geojson", data: donnees.mesures });
      carte.addLayer({
        id: "mesures",
        type: "circle",
        source: "mesures",
        paint: { "circle-color": couleur("accent"), "circle-radius": 2.5, "circle-stroke-color": couleur("white"), "circle-stroke-width": 0.5 },
      });
    }
  });

  if (ancienne) {
    new maplibregl.Marker({ color: couleur("neutre"), scale: 1.1 }).setLngLat(ancienne).setPopup(new maplibregl.Popup({ offset: 24 }).setText("Position actuelle")).addTo(carte);
  }
  nouveauMarqueur().setLngLat(point).setPopup(new maplibregl.Popup({ offset: 32 }).setText("Point relevé")).addTo(carte);

  // Tout voir : mesures, cercle et ancienne position (même à plusieurs kilomètres).
  const emprise = new maplibregl.LngLatBounds(point, point);
  for (const coordonnees of donnees.mesures?.coordinates ?? []) emprise.extend(coordonnees);
  if (ancienne) emprise.extend(ancienne);
  carte.fitBounds(emprise, { padding: 56, maxZoom: 19, duration: 0 });
}
