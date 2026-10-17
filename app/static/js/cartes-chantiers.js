// Carte de « Mes chantiers » : un marqueur par chantier, avec sa fiche en popup.
import { creerCarte, nouveauMarqueur } from "./carte.js";

const conteneur = document.getElementById("carte");

function contenuPopup(proprietes) {
  // Construit en DOM (textContent) : un nom de chantier ne doit jamais être interprété comme du HTML.
  const bloc = document.createElement("div");
  bloc.className = "grid gap-1 p-3";
  const lien = document.createElement("a");
  lien.href = `/chantiers/${proprietes.id}`;
  lien.className = "font-semibold";
  lien.textContent = proprietes.nom;
  const details = document.createElement("p");
  details.className = "text-libelle text-texte-doux";
  details.textContent = `${proprietes.libelle_statut} · ${proprietes.libelle_role}`;
  bloc.append(lien, details);
  return bloc;
}

async function carteListe() {
  const carte = creerCarte(conteneur, { cooperatif: true });
  const reponse = await fetch("/api/chantiers.geojson", { headers: { Accept: "application/geo+json" } });
  if (!reponse.ok) return;
  const collection = await reponse.json();
  if (!collection.features.length) return;

  const emprise = new maplibregl.LngLatBounds();
  for (const chantier of collection.features) {
    const [lon, lat] = chantier.geometry.coordinates;
    const popup = new maplibregl.Popup({ offset: 30 }).setDOMContent(contenuPopup(chantier.properties));
    nouveauMarqueur().setLngLat([lon, lat]).setPopup(popup).addTo(carte);
    emprise.extend([lon, lat]);
  }
  carte.fitBounds(emprise, { padding: 60, maxZoom: 15, duration: 0 });
}

carteListe();
