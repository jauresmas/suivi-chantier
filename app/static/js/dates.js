// Ajoute l'heure du lecteur aux dates du chantier quand son fuseau est différent :
// « samedi 17 octobre 2026 à 14 h 05 au Bénin — 15 h 05 chez vous ».
const fuseauLecteur = Intl.DateTimeFormat().resolvedOptions().timeZone;

function parties(date, fuseau) {
  const morceaux = new Intl.DateTimeFormat("fr-FR", {
    timeZone: fuseau, weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(morceaux.map((p) => [p.type, p.value]));
}

function completer(element) {
  if (element.dataset.complete) return;
  element.dataset.complete = "1";
  const date = new Date(element.dateTime);
  const chantier = parties(date, element.dataset.fuseau);
  const lecteur = parties(date, fuseauLecteur);
  if (chantier.hour === lecteur.hour && chantier.minute === lecteur.minute && chantier.day === lecteur.day) return;

  let ajout = ` ${element.dataset.lieu} — ${Number(lecteur.hour)} h ${lecteur.minute} chez vous`;
  if (chantier.day !== lecteur.day) ajout += ` (${lecteur.weekday} ${lecteur.day} ${lecteur.month})`;
  element.append(ajout);
}

function toutCompleter(racine) {
  if (racine.matches?.("time.date-chantier")) completer(racine);
  racine.querySelectorAll?.("time.date-chantier").forEach(completer);
}
toutCompleter(document);
// Contenu ajouté plus tard : visionneuse, blocs chargés par HTMX.
new MutationObserver((mutations) => {
  for (const mutation of mutations) for (const noeud of mutation.addedNodes) toutCompleter(noeud);
}).observe(document.body, { childList: true, subtree: true });
