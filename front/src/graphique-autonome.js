// Point d'entrée du module autonome chargé par l'espace de trading (PHP).
//
// Il n'expose PAS le pilote brut : il expose une fonction de haut niveau qui
// sait aller chercher les séries, choisir le socle, agréger au pas demandé et
// se rafraîchir. La page PHP n'a alors qu'une ligne à écrire, et surtout : les
// règles de lecture des séries restent au même endroit que pour le site.

import { PAS, agreger, fusionner, noteDerniereSeance, precision,
         socle } from "./series";
import { TYPES_TRACE, creerGraphique } from "./terminal-chart";

// Le relais et les séries sont à la racine du site ; l'espace de trading vit
// dans un sous-dossier. Chemin relatif remontant d'un cran, pour que le tout
// fonctionne aussi bien à la racine d'un domaine que dans un sous-dossier.
const RACINE = "../";

const cache = new Map();

async function lireSerie(symbole) {
  if (!cache.has(symbole)) {
    cache.set(symbole, fetch(
      `${RACINE}donnees/series/${encodeURIComponent(symbole)}.json`,
      { cache: "no-cache" },
    ).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return cache.get(symbole);
}

async function lireFraiche(symbole) {
  try {
    const r = await fetch(
      `${RACINE}serie.php?s=${encodeURIComponent(symbole)}&i=5m`,
      { cache: "no-store" });
    if (!r.ok) return null;
    return (await r.json())?.serie ?? null;
  } catch {
    return null;
  }
}

/**
 * Monte un graphique piloté dans `conteneur`.
 *
 * Renvoie un objet avec `symbole(s)`, `pas(p)`, `type(t)`, `lignes([...])`,
 * `detruire()` — de quoi le brancher sur un tableau de marché et sur un
 * ticket d'ordre.
 *
 * `surEtat` est appelé à chaque changement notable (chargement, dernière
 * bougie, fraîcheur) : c'est ce qui permet à la page d'écrire l'état réel de
 * la donnée au lieu de laisser croire à du direct.
 */
export function monter(conteneur, options = {}) {
  const {
    symbole: symboleInitial = null, pas: pasInitial = "D1",
    type: typeInitial = "bougies", hauteur = 360, surEtat = null,
  } = options;

  let symbole = symboleInitial;
  let clePas = pasInitial;
  let series = null;
  let fraiche = null;
  let lignes = [];
  let vivant = true;

  const pilote = creerGraphique(conteneur, {
    hauteur,
    type: TYPES_TRACE.includes(typeInitial) ? typeInitial : "bougies",
    surSurvol: (b) => surEtat?.({ survol: b }),
  });

  const conf = () => PAS.find((p) => p.cle === clePas) ?? PAS[4];

  function redessiner() {
    const c = conf();
    if (!series) {
      // Silence interdit ici. Sans série, ne rien faire laisserait à l'écran
      // le graphique de l'actif PRÉCÉDENT, avec le nouveau nom sélectionné
      // partout ailleurs : on croirait regarder Bitcoin en regardant Samsung.
      surEtat?.({ vide: true, symbole });
      return;
    }
    const socles = {
      quotidien: series.quotidien,
      horaire: series.horaire,
      intraday: fusionner(series.intraday, fraiche),
    };
    const base = socle(socles, c);
    if (!base) { surEtat?.({ vide: true }); return; }
    pilote.donnees(agreger(base, c), c.visibles);
    pilote.lignes(lignes);
    surEtat?.({
      symbole, pas: c.libelle,
      derniere: pilote.derniereBougie(),
      decimales: pilote.decimales(),
      fraiche: Boolean(fraiche) && c.base === "intraday",
      // « marché fermé (week-end) » ou « aucune donnée plus récente » : un
      // graphique arrêté doit dire POURQUOI, sinon il a l'air en panne —
      // c'est arrivé deux week-ends de suite
      note_seance: noteDerniereSeance(socles.quotidien?.t?.at(-1)),
      socles: {
        intraday: Boolean(socles.intraday?.t?.length),
        horaire: Boolean(socles.horaire?.t?.length),
        quotidien: Boolean(socles.quotidien?.t?.length),
      },
    });
  }

  async function charger() {
    if (!symbole) return;
    const demande = symbole;
    surEtat?.({ chargement: true, symbole });
    series = await lireSerie(demande);
    // Le lecteur a pu changer d'actif pendant l'attente réseau : une réponse
    // en retard ne doit jamais écraser l'affichage du suivant.
    if (!vivant || demande !== symbole) return;
    fraiche = null;
    redessiner();
    if (conf().base === "intraday") rafraichir();
  }

  async function rafraichir() {
    if (!vivant || !symbole || document.hidden) return;
    const demande = symbole;
    const s = await lireFraiche(demande);
    if (!vivant || demande !== symbole || !s) return;
    fraiche = s;
    redessiner();
  }

  const minuterie = setInterval(() => {
    if (conf().base === "intraday") rafraichir();
  }, 60000);

  const api = {
    symbole(s) {
      if (s === symbole) return api;
      symbole = s;
      charger();
      return api;
    },
    pas(p) {
      clePas = PAS.some((x) => x.cle === p) ? p : "D1";
      redessiner();
      if (conf().base === "intraday" && !fraiche) rafraichir();
      return api;
    },
    type(t) { pilote.type(t); redessiner(); return api; },
    lignes(l) { lignes = l ?? []; pilote.lignes(lignes); return api; },
    logarithmique(v) { pilote.logarithmique(v); return api; },
    detruire() {
      vivant = false;
      clearInterval(minuterie);
      pilote.detruire();
    },
  };

  if (symbole) charger();
  return api;
}

// `precision` n'est PAS réexporté : la page de trading n'utilise que
// `monter`, `PAS` et `TYPES_TRACE`. Une surface publique se garde
// étroite, sinon on ne sait plus ce qu'on a le droit de changer.
export { PAS, TYPES_TRACE };
