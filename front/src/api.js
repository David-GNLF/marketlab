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

// L'hébergeur (LiteSpeed) peut servir une version en cache des JSON pendant
// des heures. Le paramètre de version, renouvelé toutes les 5 minutes,
// force un contenu frais sans interdire tout cache navigateur.
const version = () => Math.floor(Date.now() / 300000);

async function charger(chemin) {
  if (cache.has(chemin)) return cache.get(chemin);
  const promesse = fetch(`donnees/${chemin}?v=${version()}`, {
    cache: "no-cache",
  }).then((r) => {
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

// Cotations fraîches : seul appel qui ne lit PAS un fichier publié mais le
// relais PHP (cache serveur de 60 s). Renvoie {} en cas d'indisponibilité —
// l'affichage retombe alors sur les cours de l'instantané, sans casser.
export async function getCoursDirect() {
  try {
    const r = await fetch("cours.php", { cache: "no-store" });
    if (!r.ok) return {};
    return (await r.json()).cours ?? {};
  } catch {
    return {};
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
export const getVerdicts = () => charger("verdicts.json");
export const getCot = () => charger("cot.json");
export const getConcours = () => charger("concours.json");
export const getAlertes = () => charger("alertes_recentes.json");
export const getSentimentMarche = () => charger("sentiment_marche.json");
export const getApprentissage = () => charger("apprentissage.json");
export const getBarometres = () => charger("barometres.json");
