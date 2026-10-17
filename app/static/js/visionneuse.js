// Visionneuse plein écran : flèches, Échap et balayage au doigt ; panneau d'informations rendu par le serveur.
// Délégation d'évènements : les vignettes ajoutées plus tard par HTMX fonctionnent aussi.
const dialogue = document.getElementById("visionneuse");

if (dialogue) {
  const image = dialogue.querySelector("img");
  const infos = dialogue.querySelector("[data-infos]");
  const position = dialogue.querySelector("[data-position]");
  const boutonCarte = dialogue.querySelector("[data-voir-carte]");
  let liens = [];
  let index = 0;

  function afficher(nouvelIndex) {
    index = (nouvelIndex + liens.length) % liens.length;
    const lien = liens[index];
    image.src = lien.href;
    image.alt = lien.querySelector("img")?.alt ?? "";
    infos.innerHTML = lien.querySelector("template")?.innerHTML ?? ""; // rendu par le serveur (échappé par Jinja)
    position.textContent = `${index + 1} / ${liens.length}`;
    boutonCarte.hidden = lien.dataset.position !== "1";
    for (const bouton of dialogue.querySelectorAll("[data-pas]")) bouton.hidden = liens.length < 2;
    // Précharge la suivante : la navigation reste fluide sur une connexion lente.
    if (liens.length > 1) new Image().src = liens[(index + 1) % liens.length].href;
  }

  document.addEventListener("click", (evenement) => {
    const lien = evenement.target.closest("a.ouvrir-photo");
    if (!lien || evenement.ctrlKey || evenement.metaKey) return;
    evenement.preventDefault();
    const galerie = lien.closest("[data-visionneuse]");
    liens = galerie ? [...galerie.querySelectorAll("a.ouvrir-photo")] : [lien];
    afficher(liens.indexOf(lien));
    dialogue.showModal();
    document.documentElement.style.overflow = "hidden";
  });

  dialogue.addEventListener("close", () => {
    document.documentElement.style.overflow = "";
    image.removeAttribute("src");
  });

  dialogue.addEventListener("click", (evenement) => {
    const pas = evenement.target.closest("[data-pas]")?.dataset.pas;
    if (pas) afficher(index + Number(pas));
    else if (evenement.target.closest("[data-fermer]")) dialogue.close();
  });

  dialogue.addEventListener("keydown", (evenement) => {
    if (evenement.key === "ArrowLeft") afficher(index - 1);
    if (evenement.key === "ArrowRight") afficher(index + 1);
  });

  // Balayage gauche / droite au doigt.
  const zoneImage = dialogue.querySelector("[data-zone-image]");
  let departX = null;
  zoneImage.addEventListener("pointerdown", (evenement) => { departX = evenement.clientX; });
  zoneImage.addEventListener("pointerup", (evenement) => {
    if (departX === null) return;
    const ecart = evenement.clientX - departX;
    departX = null;
    if (Math.abs(ecart) > 50 && liens.length > 1) afficher(index + (ecart < 0 ? 1 : -1));
  });

  // « Voir sur la carte » : sur le tableau de bord, centre la carte ; ailleurs, y conduit.
  boutonCarte.addEventListener("click", () => {
    const lien = liens[index];
    dialogue.close();
    if (document.getElementById("carte-chantier")) {
      document.dispatchEvent(new CustomEvent("voir-sur-carte", { detail: { photo: lien.dataset.photo } }));
    } else {
      window.location.href = lien.dataset.carte;
    }
  });
}
