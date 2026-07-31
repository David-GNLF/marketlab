// Manipulation des séries de cotation — aucune dépendance au rendu.
//
// Séparé de `graphique.jsx` pour une raison précise : ces fonctions portent
// des RÈGLES (laquelle de deux barres concurrentes gagne, où commence une
// séance, combien de décimales a un prix), et une règle doit être testable
// sans navigateur ni bibliothèque de graphique. Les tests node de
// `tests/test_series_front.py` les exercent directement.

// --------------------------------------------------------------- conversions

/** Colonnes parallèles -> objets, une seule passe. Format publié par
 *  marketlab/serie.py ET par serie.php : une seule lecture pour les deux. */
export function enBougies(bloc) {
  if (!bloc?.t?.length) return [];
  const { t, o, h, l, c } = bloc;
  const out = [];
  for (let i = 0; i < t.length; i++) {
    if (c?.[i] == null) continue;
    out.push({
      time: t[i],
      open: o?.[i] ?? c[i], high: h?.[i] ?? c[i],
      low: l?.[i] ?? c[i], close: c[i],
    });
  }
  return out;
}

export function enLigne(bloc, colonne = "c") {
  if (!bloc?.t?.length) return [];
  const col = bloc[colonne];
  if (!col) return [];
  const out = [];
  for (let i = 0; i < bloc.t.length; i++) {
    if (col[i] == null) continue;
    out.push({ time: bloc.t[i], value: col[i] });
  }
  return out;
}

/**
 * Recolle les barres fraîches du relais en bout de série publiée.
 *
 * La règle est simple et vaut d'être écrite : à horodatage égal, la barre
 * FRAÎCHE gagne. La barre publiée de 15 h 30 a été archivée alors que la
 * demi-heure n'était pas finie ; celle du relais est complète. Prendre
 * l'ancienne parce qu'elle est arrivée la première afficherait un plus-haut
 * de séance faux — le genre d'erreur qui fait croire qu'un objectif n'a pas
 * été touché alors qu'il l'a été.
 */
export function fusionner(publiee, fraiche) {
  if (!fraiche?.t?.length) return publiee;
  if (!publiee?.t?.length) return fraiche;
  const par_temps = new Map();
  const pousser = (bloc) => {
    for (let i = 0; i < bloc.t.length; i++) {
      par_temps.set(bloc.t[i], {
        o: bloc.o?.[i], h: bloc.h?.[i], l: bloc.l?.[i],
        c: bloc.c?.[i], v: bloc.v?.[i],
      });
    }
  };
  pousser(publiee);
  pousser(fraiche);   // en second : la fraîche écrase la publiée
  const temps = [...par_temps.keys()].sort((a, b) => a - b);
  const bloc = { t: temps, o: [], h: [], l: [], c: [], v: [] };
  for (const t of temps) {
    const b = par_temps.get(t);
    bloc.o.push(b.o); bloc.h.push(b.h);
    bloc.l.push(b.l); bloc.c.push(b.c); bloc.v.push(b.v);
  }
  bloc.n = temps.length;
  bloc.interval = fraiche.interval ?? publiee.interval;
  return bloc;
}

/** Nombre de décimales à afficher, déduit du prix lui-même.
 *  Une paire de change se lit à 5 décimales, une action à 2 : figer une
 *  valeur unique rendrait l'un illisible et l'autre faussement précis. */
export function precision(prix) {
  const p = Math.abs(Number(prix) || 0);
  if (p === 0) return 2;
  if (p < 1) return 6;
  if (p < 20) return 4;
  // Au-delà de 20, deux décimales suffisent : la branche « < 1000 » renvoyait
  // exactement la même valeur que la suivante, donc elle ne décidait rien.
  return 2;
}

// ------------------------------------------------------------ pas de bougie

/**
 * Pas de bougie proposés, dans l'ordre du plus fin au plus large.
 *
 * `base` dit dans laquelle des TROIS séries publiées puiser ; `minutes` (ou
 * `groupe` pour les pas calendaires) dit comment agréger. Publier sept séries
 * n'aurait rien apporté : 15 min se déduit de 5 min, 4 h de 1 h, la semaine et
 * le mois du quotidien. Ce qui NE se déduit pas, en revanche, ce sont les
 * pas plus fins que le socle — c'est pourquoi la série horaire existe : on ne
 * fabrique pas 800 barres d'une heure avec cinq séances de barres 5 min.
 */
export const PAS = [
  { cle: "M5", libelle: "5 min", base: "intraday", minutes: 5, visibles: 120 },
  { cle: "M15", libelle: "15 min", base: "intraday", minutes: 15, visibles: 120 },
  { cle: "H1", libelle: "1 h", base: "horaire", minutes: 60, visibles: 160 },
  { cle: "H4", libelle: "4 h", base: "horaire", minutes: 240, visibles: 140 },
  { cle: "D1", libelle: "1 jour", base: "quotidien", groupe: "jour", visibles: 180 },
  { cle: "S1", libelle: "1 sem.", base: "quotidien", groupe: "semaine", visibles: 160 },
  { cle: "MN", libelle: "1 mois", base: "quotidien", groupe: "mois", visibles: 60 },
];

/** Lundi de la semaine d'une date « AAAA-MM-JJ », au même format.
 *  Semaine ISO : le lundi. Grouper au dimanche couperait la semaine boursière
 *  en deux, ce qui n'a de sens sur aucune place. */
function lundiDe(jour) {
  const d = new Date(`${jour}T00:00:00Z`);
  const decalage = (d.getUTCDay() + 6) % 7;   // lundi = 0
  d.setUTCDate(d.getUTCDate() - decalage);
  return d.toISOString().slice(0, 10);
}

/** Clé de regroupement d'une bougie selon le pas demandé. */
function clef(t, pas) {
  if (pas.minutes) return Math.floor(t / (pas.minutes * 60)) * pas.minutes * 60;
  if (pas.groupe === "semaine") return lundiDe(t);
  if (pas.groupe === "mois") return `${String(t).slice(0, 7)}-01`;
  return t;
}

/**
 * Agrège une série colonnaire au pas demandé.
 *
 * Règles d'agrégation, qui sont celles de toutes les places : l'ouverture est
 * celle de la PREMIÈRE bougie du groupe, la clôture celle de la DERNIÈRE, le
 * plus haut et le plus bas sont les extrêmes du groupe, le volume leur somme.
 * Prendre la moyenne des ouvertures, ou le plus haut des clôtures, donnerait
 * un chandelier qui n'a jamais existé.
 *
 * Les surcouches (moyennes, bandes) ne sont PAS ré-agrégées : une moyenne de
 * moyennes n'est pas une moyenne. Elles ne sont conservées que si le pas ne
 * regroupe rien — sinon elles disparaissent, ce qui est la seule réponse
 * honnête tant qu'elles ne sont pas recalculées sur le pas affiché.
 */
export function agreger(bloc, pas) {
  if (!bloc?.t?.length || !pas) return bloc;
  const groupes = new Map();
  for (let i = 0; i < bloc.t.length; i++) {
    if (bloc.c?.[i] == null) continue;
    const k = clef(bloc.t[i], pas);
    const g = groupes.get(k);
    const o = bloc.o?.[i] ?? bloc.c[i];
    const h = bloc.h?.[i] ?? bloc.c[i];
    const l = bloc.l?.[i] ?? bloc.c[i];
    const v = bloc.v?.[i] ?? 0;
    if (!g) groupes.set(k, { o, h, l, c: bloc.c[i], v });
    else {
      g.h = Math.max(g.h, h);
      g.l = Math.min(g.l, l);
      g.c = bloc.c[i];
      g.v += v;
    }
  }
  if (groupes.size === bloc.t.length) return bloc;   // rien à regrouper

  const t = [...groupes.keys()].sort(
    (a, b) => (typeof a === "number" ? a - b : String(a).localeCompare(String(b))));
  const out = { t, o: [], h: [], l: [], c: [], v: [], n: t.length,
                interval: pas.cle };
  for (const k of t) {
    const g = groupes.get(k);
    out.o.push(g.o); out.h.push(g.h); out.l.push(g.l);
    out.c.push(g.c); out.v.push(g.v);
  }
  return out;
}

/** Choisit le socle publié qui convient à un pas, avec repli explicite.
 *  Un titre sans barres fines (BRVM, actif récent) doit rester lisible : on
 *  retombe sur le quotidien plutôt que d'afficher un graphique vide. */
export function socle(series, pas) {
  const dispo = (nom) => (series?.[nom]?.t?.length ? series[nom] : null);
  return dispo(pas.base) ?? dispo("horaire") ?? dispo("quotidien") ?? null;
}
