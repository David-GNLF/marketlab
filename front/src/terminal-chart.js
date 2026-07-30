// Le graphique de cotation, SANS React.
//
// POURQUOI SANS REACT. Le graphique doit vivre à deux endroits : le site
// (React) et l'espace de trading (PHP servi tel quel, sans build). Deux
// implémentations, ce serait deux comportements qui divergent au premier
// correctif — exactement la dérive que ce projet corrige partout ailleurs par
// des sources uniques (config.SUIVIS, cours_lib.php, ml_equite_compte).
// Le noyau est donc du JavaScript ordinaire, piloté par des appels ; React
// n'en est qu'un ENROBAGE, et la page de trading un autre.
//
// L'objet renvoyé est un pilote : on lui pousse des données, un type de tracé,
// des surcouches, des lignes de prix. Il ne décide de rien — les règles de
// lecture des séries sont dans `series.js`, les choix d'interface chez
// l'appelant.

import {
  ColorType, CrosshairMode, LineStyle, createChart,
} from "lightweight-charts";
import { enBougies, enLigne, precision } from "./series";

export const TYPES_TRACE = ["bougies", "barres", "ligne", "montagne"];

/** Jetons de couleur lus dans le thème CSS de la page hôte.
 *  Le site et l'espace de trading n'ont pas les mêmes variables : on prend
 *  celles qui existent, avec un repli neutre plutôt qu'une couleur en dur qui
 *  jurerait sur l'un des deux. */
function jetons(racine = document.documentElement) {
  const s = getComputedStyle(racine);
  const v = (nom, repli) => {
    const x = s.getPropertyValue(nom).trim();
    return x || repli;
  };
  const sombre = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return {
    grid: v("--grid", sombre ? "#2c2c2a" : "#e1e0d9"),
    muted: v("--muted", "#898781"),
    texte: v("--text-primary", sombre ? "#ffffff" : "#0b0b0b"),
    s1: v("--series-1", sombre ? "#3987e5" : "#2a78d6"),
    s2: v("--series-2", sombre ? "#d95926" : "#eb6834"),
    s3: v("--series-3", sombre ? "#199e70" : "#1baf7a"),
    hausse: v("--good", sombre ? "#0ca30c" : "#006300"),
    baisse: v("--critical", "#d03b3b"),
  };
}

/**
 * Crée un graphique dans `conteneur` et renvoie son pilote.
 *
 * options :
 *   hauteur   — pixels (défaut 420)
 *   type      — "bougies" | "barres" | "ligne" | "montagne"
 *   surSurvol — rappel(bougie | null) à chaque déplacement du réticule
 */
export function creerGraphique(conteneur, options = {}) {
  let { hauteur = 420, type = "bougies", surSurvol = null,
        barresVisibles = 0 } = options;

  let t = jetons();
  let bloc = null;         // dernières données poussées
  let bougies = [];
  let dec = 2;
  let intra = false;
  let log = false;
  let surcouches = {};
  let lignesDemandees = [];

  let chart = null;
  let principale = null;
  let secondaires = [];
  let lignesPrix = [];
  let observateur = null;

  // --- construction / reconstruction -----------------------------------
  // Changer de type de série ou de thème n'est pas paramétrable à chaud dans
  // la bibliothèque : on reconstruit. C'est instantané, et cela évite un
  // empilement d'états partiels bien plus coûteux à raisonner.
  function batir() {
    detruireGraphique();
    chart = createChart(conteneur, {
      height: hauteur,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: t.muted, fontSize: 11,
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: t.muted, width: 1, style: LineStyle.Dashed,
                    labelBackgroundColor: t.texte },
        horzLine: { color: t.muted, width: 1, style: LineStyle.Dashed,
                    labelBackgroundColor: t.texte },
      },
      rightPriceScale: { borderColor: t.grid,
                         scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: {
        borderColor: t.grid,
        // Sur une échelle fine c'est l'heure qu'on cherche ; sur du quotidien
        // elle ne serait que du bruit.
        timeVisible: intra, secondsVisible: false,
        rightOffset: 3, barSpacing: 8,
      },
      localization: {
        locale: "fr-FR",
        priceFormatter: (p) => Number(p).toLocaleString("fr-FR", {
          minimumFractionDigits: dec, maximumFractionDigits: dec }),
      },
    });

    const commun = {
      priceFormat: { type: "price", precision: dec, minMove: 10 ** -dec },
    };
    if (type === "bougies") {
      principale = chart.addCandlestickSeries({
        ...commun,
        upColor: t.hausse, downColor: t.baisse,
        borderUpColor: t.hausse, borderDownColor: t.baisse,
        wickUpColor: t.hausse, wickDownColor: t.baisse,
      });
    } else if (type === "barres") {
      principale = chart.addBarSeries({
        ...commun, upColor: t.hausse, downColor: t.baisse });
    } else if (type === "montagne") {
      principale = chart.addAreaSeries({
        ...commun, lineColor: t.s1, lineWidth: 2,
        topColor: `${t.s1}44`, bottomColor: `${t.s1}05` });
    } else {
      principale = chart.addLineSeries({ ...commun, color: t.s1, lineWidth: 2 });
    }

    chart.applyOptions({ width: conteneur.clientWidth });
    observateur = new ResizeObserver(() => {
      if (chart && conteneur.clientWidth) {
        chart.applyOptions({ width: conteneur.clientWidth });
      }
    });
    observateur.observe(conteneur);

    if (surSurvol) {
      chart.subscribeCrosshairMove((p) => {
        const d = p?.time ? p.seriesData?.get(principale) : null;
        if (!d) { surSurvol(null); return; }
        const i = bougies.findIndex((b) => b.time === p.time);
        surSurvol({ ...d, time: p.time, volume: i >= 0 ? bloc?.v?.[i] : null });
      });
    }

    appliquerDonnees();
    appliquerLog();
    appliquerSurcouches();
    appliquerLignes();
  }

  function detruireGraphique() {
    observateur?.disconnect();
    observateur = null;
    secondaires = [];
    lignesPrix = [];
    if (chart) { chart.remove(); chart = null; principale = null; }
  }

  // --- application des états --------------------------------------------

  function appliquerDonnees() {
    if (!principale || !bougies.length) return;
    principale.setData(
      type === "bougies" || type === "barres"
        ? bougies
        : bougies.map((b) => ({ time: b.time, value: b.close })));
    // Tout montrer d'emblée n'est pas un service : 1 250 séances tassées sur
    // 900 pixels donnent une bouillie. On cadre sur les dernières barres —
    // ce que fait tout terminal — et le reste s'atteint à la molette.
    if (barresVisibles > 0 && bougies.length > barresVisibles) {
      chart.timeScale().setVisibleLogicalRange({
        from: bougies.length - barresVisibles, to: bougies.length + 2 });
    } else {
      chart.timeScale().fitContent();
    }
  }

  function appliquerLog() {
    chart?.priceScale("right").applyOptions({ mode: log ? 1 : 0 });
  }

  function appliquerSurcouches() {
    if (!chart) return;
    for (const s of secondaires) { try { chart.removeSeries(s); } catch { /* déjà retirée */ } }
    secondaires = [];
    const courbe = (colonne, couleur, largeur, style) => {
      const pts = enLigne(bloc, colonne);
      if (!pts.length) return;
      const l = chart.addLineSeries({
        color: couleur, lineWidth: largeur, lineStyle: style,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false });
      l.setData(pts);
      secondaires.push(l);
    };
    if (surcouches.sma50) courbe("sma50", t.s2, 2, LineStyle.Solid);
    if (surcouches.sma200) courbe("sma200", t.s3, 2, LineStyle.Solid);
    if (surcouches.bollinger) {
      courbe("bb_upper", t.muted, 1, LineStyle.Dotted);
      courbe("bb_lower", t.muted, 1, LineStyle.Dotted);
    }
    if (surcouches.volume && bloc?.v?.some((x) => x)) {
      const h = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        // le volume et le prix n'ont aucune unité commune : sous-panneau
        // sur sa propre échelle, jamais superposé
        priceScaleId: "volume",
        priceLineVisible: false, lastValueVisible: false });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 } });
      h.setData(bougies.map((b, i) => ({
        time: b.time, value: bloc.v?.[i] ?? 0,
        color: b.close >= b.open ? `${t.hausse}55` : `${t.baisse}55` })));
      secondaires.push(h);
    }
  }

  function appliquerLignes() {
    if (!principale) return;
    for (const l of lignesPrix) { try { principale.removePriceLine(l); } catch { /* déjà retirée */ } }
    lignesPrix = [];
    for (const d of lignesDemandees) {
      const p = Number(d.prix);
      if (!Number.isFinite(p) || p <= 0) continue;
      lignesPrix.push(principale.createPriceLine({
        price: p,
        color: d.couleur === "hausse" ? t.hausse
             : d.couleur === "baisse" ? t.baisse
             : d.couleur === "neutre" ? t.muted : t.s1,
        lineWidth: 1,
        lineStyle: d.style === "plein" ? LineStyle.Solid
                 : d.style === "pointille" ? LineStyle.Dotted : LineStyle.Dashed,
        axisLabelVisible: true, title: d.titre ?? "",
      }));
    }
  }

  // --- pilote --------------------------------------------------------------
  const pilote = {
    /** Pousse une série colonnaire ({t,o,h,l,c,v,...}). */
    donnees(nouveau, visibles) {
      if (visibles != null) barresVisibles = visibles;
      bloc = nouveau;
      bougies = enBougies(nouveau);
      const decAvant = dec;
      const intraAvant = intra;
      dec = precision(bougies.at(-1)?.close);
      intra = typeof nouveau?.t?.[0] === "number";
      // Le format des prix et celui de l'axe du temps se figent à la
      // construction : s'ils changent, il faut rebâtir.
      if (!chart || dec !== decAvant || intra !== intraAvant) batir();
      else { appliquerDonnees(); appliquerSurcouches(); appliquerLignes(); }
      return pilote;
    },
    type(nouveau) {
      if (nouveau === type) return pilote;
      type = TYPES_TRACE.includes(nouveau) ? nouveau : "bougies";
      batir();
      return pilote;
    },
    surcouches(nouveau) {
      surcouches = nouveau ?? {};
      appliquerSurcouches();
      return pilote;
    },
    /** lignes : [{prix, titre, couleur: hausse|baisse|neutre|accent,
     *             style: plein|tirets|pointille}] */
    lignes(nouveau) {
      lignesDemandees = nouveau ?? [];
      appliquerLignes();
      return pilote;
    },
    logarithmique(actif) {
      log = Boolean(actif);
      appliquerLog();
      return pilote;
    },
    theme() {            // à appeler quand le thème de la page bascule
      t = jetons();
      batir();
      return pilote;
    },
    derniereBougie() {
      const b = bougies.at(-1);
      return b ? { ...b, volume: bloc?.v?.at(-1) ?? null } : null;
    },
    decimales() { return dec; },
    detruire: detruireGraphique,
  };

  batir();
  return pilote;
}
