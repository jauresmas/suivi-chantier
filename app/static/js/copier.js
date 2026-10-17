// Bouton « Copier » : <button data-copier="#id-du-champ">
document.querySelectorAll("[data-copier]").forEach((bouton) => {
  bouton.addEventListener("click", async () => {
    const champ = document.querySelector(bouton.dataset.copier);
    const texte = bouton.querySelector("span") ?? bouton; // garde l'icône du bouton
    const libelle = texte.textContent;
    try {
      await navigator.clipboard.writeText(champ.value);
    } catch {
      // Presse-papiers indisponible (page non sécurisée, ancien navigateur) : sélection + copie classique.
      champ.select();
      document.execCommand("copy");
    }
    texte.textContent = "Lien copié";
    setTimeout(() => { texte.textContent = libelle; }, 2000);
  });
});
