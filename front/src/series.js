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

/** Ne garde que les `seances` dernières journées d'une série fine. */
export function dernieresSeances(bloc, seances) {
  if (!bloc?.t?.length || !seances) return bloc;
  const jour = (sec) => Math.floor(sec / 86400);
  const jours = [...new Set(bloc.t.map(jour))].sort((a, b) => a - b);
  const depuis = jours.slice(-seances)[0];
  const debut = bloc.t.findIndex((s) => jour(s) >= depuis);
  if (debut <= 0) return bloc;
  const coupe = (col) => (col ? col.slice(debut) : col);
  return {
    ...bloc, t: coupe(bloc.t), o: coupe(bloc.o), h: coupe(bloc.h),
    l: coupe(bloc.l), c: coupe(bloc.c), v: coupe(bloc.v),
    n: bloc.t.length - debut,
  };
}

/** Nombre de décimales à afficher, déduit du prix lui-même.
 *  Une paire de change se lit à 5 décimales, une action à 2 : figer une
 *  valeur unique rendrait l'un illisible et l'autre faussement précis. */
export function precision(prix) {
  const p = Math.abs(Number(prix) || 0);
  if (p === 0) return 2;
  if (p < 1) return 6;
  if (p < 20) return 4;
  if (p < 1000) return 2;
  return 2;
}
