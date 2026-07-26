// Chargement des données publiées.
//
// Le site est statique : plus aucun appel à une API, uniquement des fichiers
// JSON produits en amont par scripts/publier.py. C'est ce qui permet
// l'hébergement sur un mutualisé cPanel — et ce qui rend l'affichage
// instantané.
//
// Chemins RELATIFS : le site doit fonctionner aussi bien à la racine d'un
// domaine que dans un sous-dossier (/marketlab).

const cache = new Map();

async function charger(chemin) {
  if (cache.has(chemin)) return cache.get(chemin);
  const promesse = fetch(`donnees/${chemin}`, { cache: "no-cache" }).then((r) => {
    if (!r.ok) throw new Error(`${chemin} indisponible (HTTP ${r.status})`);
    return r.json();
  });
  cache.set(chemin, promesse);
  try {
    return await promesse;
  } catch (e) {
    cache.delete(chemin); // permet de réessayer après un échec réseau
    throw e;
  }
}

export const getMeta = () => charger("meta.json");
export const getScreener = () => charger("screener.json");
export const getMacro = () => charger("macro.json");
export const getCalendrier = () => charger("calendrier.json");
export const getResultats = () => charger("resultats.json");
export const getFondamentaux = () => charger("fondamentaux.json");
export const getCorrelations = () => charger("correlations.json");
export const getPaper = () => charger("paper.json");
export const getTitre = (symbole) => charger(`titres/${symbole}.json`);
