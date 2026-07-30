// Graphique de cotation façon terminal de marché.
//
// POURQUOI UNE AUTRE BIBLIOTHÈQUE QUE RECHARTS. Recharts dessine très bien une
// courbe de série temporelle — c'est ce qu'on lui demande partout ailleurs sur
// le site, et il y reste. Mais un graphique de trading n'est pas une courbe :
// il lui faut des bougies, un réticule qui affiche l'OHLC exact sous le
// curseur, un axe des prix logarithmique, des lignes de prix horizontales
// (l'entrée, le stop, l'objectif), un volume en sous-panneau et un zoom à la
// molette sur des milliers de barres sans ramer. Chacune de ces pièces se
// bricole avec Recharts ; les sept ensemble, non. `lightweight-charts` est
// exactement l'outil de ce travail — c'est celui de TradingView, sous licence
// Apache 2.0, 45 Ko compressés.
//
// CE QU'IL AFFICHE QUE LES PLATEFORMES DE BROKER N'AFFICHENT PAS. Le plan de
// trade est tracé SUR le graphique : entrée, stop, objectif, supports et
// résistances. Chez un broker, ces niveaux sont des chiffres dans un
// formulaire, à reporter mentalement sur la courbe — c'est précisément là que
// se prennent les mauvaises décisions. Ici la question « le stop est-il sous
// le dernier creux ? » se répond d'un coup d'œil.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType, CrosshairMode, LineStyle, createChart,
} from "lightweight-charts";
import { enBougies, enLigne, precision } from "./series";

// Réexportées pour que la page n'ait qu'une porte d'entrée.
export { dernieresSeances, enBougies, fusionner, precision } from "./series";

// Échelles de temps. `source` dit dans quelle série puiser, `barres` combien
// en montrer. Le pas de temps fin ne couvre que quelques séances — au-delà,
// c'est le quotidien qui répond, et l'utilisateur n'a pas à le savoir.
export const ECHELLES = [
  { cle: "1J", libelle: "1 J", source: "intraday", seances: 1 },
  { cle: "5J", libelle: "5 J", source: "intraday", seances: 5 },
  { cle: "1M", libelle: "1 M", source: "quotidien", barres: 22 },
  { cle: "3M", libelle: "3 M", source: "quotidien", barres: 66 },
  { cle: "6M", libelle: "6 M", source: "quotidien", barres: 130 },
  { cle: "1A", libelle: "1 an", source: "quotidien", barres: 260 },
  { cle: "5A", libelle: "5 ans", source: "quotidien", barres: 99999 },
];

export const TYPES = [
  { cle: "bougies", libelle: "Bougies" },
  { cle: "barres", libelle: "Barres OHLC" },
  { cle: "ligne", libelle: "Ligne" },
  { cle: "montagne", libelle: "Montagne" },
];

// -------------------------------------------------------------------- thème

function jetons() {
  const s = getComputedStyle(document.documentElement);
  const v = (n) => s.getPropertyValue(n).trim();
  return {
    surface: v("--surface"), grid: v("--grid"), muted: v("--muted"),
    texte: v("--text-primary"), border: v("--border"),
    s1: v("--series-1"), s2: v("--series-2"), s3: v("--series-3"),
    hausse: v("--good"), baisse: v("--critical"),
  };
}

// ---------------------------------------------------------------- composant

export function GraphiqueTerminal({
  bloc, type = "bougies", surcouches = {}, plan = null, niveaux = null,
  hauteur = 420, logarithmique = false, onSurvol = null,
}) {
  const conteneur = useRef(null);
  const graphique = useRef(null);
  const principale = useRef(null);
  const volumes = useRef(null);
  const lignes = useRef({});
  const [t, setT] = useState(jetons);

  // Le thème du système peut basculer pendant la session : le graphique est
  // dessiné en canvas, il ne suit pas les variables CSS tout seul.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const maj = () => setT(jetons());
    mq.addEventListener("change", maj);
    return () => mq.removeEventListener("change", maj);
  }, []);

  const bougies = useMemo(() => enBougies(bloc), [bloc]);
  const intra = useMemo(
    () => typeof bloc?.t?.[0] === "number", [bloc]);
  const dec = useMemo(
    () => precision(bougies.at(-1)?.close), [bougies]);

  // --- création : une seule fois par changement de thème ou de type
  useEffect(() => {
    if (!conteneur.current) return undefined;
    const chart = createChart(conteneur.current, {
      height: hauteur,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: t.muted,
        fontSize: 11,
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
      },
      grid: {
        vertLines: { color: t.grid, style: LineStyle.Solid },
        horzLines: { color: t.grid, style: LineStyle.Solid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: t.muted, width: 1, style: LineStyle.Dashed,
                    labelBackgroundColor: t.texte },
        horzLine: { color: t.muted, width: 1, style: LineStyle.Dashed,
                    labelBackgroundColor: t.texte },
      },
      rightPriceScale: { borderColor: t.grid, scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: {
        borderColor: t.grid,
        // Sur une échelle fine, la date seule ne suffit pas : c'est l'heure
        // qu'on cherche. Sur du quotidien, l'heure serait du bruit.
        timeVisible: intra, secondsVisible: false,
        rightOffset: 3, barSpacing: 8,
      },
      localization: {
        locale: "fr-FR",
        priceFormatter: (p) => Number(p).toLocaleString("fr-FR", {
          minimumFractionDigits: dec, maximumFractionDigits: dec }),
      },
      handleScale: { axisPressedMouseMove: { time: true, price: true } },
    });
    graphique.current = chart;

    const commun = {
      priceFormat: { type: "price", precision: dec, minMove: 10 ** -dec },
    };
    let s;
    if (type === "bougies") {
      s = chart.addCandlestickSeries({
        ...commun,
        upColor: t.hausse, downColor: t.baisse,
        borderUpColor: t.hausse, borderDownColor: t.baisse,
        wickUpColor: t.hausse, wickDownColor: t.baisse,
      });
    } else if (type === "barres") {
      s = chart.addBarSeries({ ...commun, upColor: t.hausse, downColor: t.baisse });
    } else if (type === "montagne") {
      s = chart.addAreaSeries({
        ...commun, lineColor: t.s1, lineWidth: 2,
        topColor: `${t.s1}44`, bottomColor: `${t.s1}05`,
      });
    } else {
      s = chart.addLineSeries({ ...commun, color: t.s1, lineWidth: 2 });
    }
    principale.current = s;

    const obs = new ResizeObserver(() => {
      if (conteneur.current) {
        chart.applyOptions({ width: conteneur.current.clientWidth });
      }
    });
    obs.observe(conteneur.current);
    chart.applyOptions({ width: conteneur.current.clientWidth });

    return () => { obs.disconnect(); chart.remove(); graphique.current = null; };
  }, [type, hauteur, t, dec, intra]);

  // --- données de la série principale
  useEffect(() => {
    const s = principale.current;
    if (!s || !bougies.length) return;
    if (type === "bougies" || type === "barres") {
      s.setData(bougies);
    } else {
      s.setData(bougies.map((b) => ({ time: b.time, value: b.close })));
    }
    graphique.current?.timeScale().fitContent();
  }, [bougies, type]);

  // --- axe logarithmique
  useEffect(() => {
    graphique.current?.priceScale("right")
      .applyOptions({ mode: logarithmique ? 1 : 0 });
  }, [logarithmique]);

  // --- surcouches (moyennes, bandes, volume)
  useEffect(() => {
    const chart = graphique.current;
    if (!chart) return undefined;
    const ajoutees = [];
    const courbe = (colonne, couleur, largeur = 1, style = LineStyle.Solid) => {
      const pts = enLigne(bloc, colonne);
      if (!pts.length) return;
      const l = chart.addLineSeries({
        color: couleur, lineWidth: largeur, lineStyle: style,
        priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      l.setData(pts);
      ajoutees.push(l);
    };
    if (surcouches.sma50) courbe("sma50", t.s2, 2);
    if (surcouches.sma200) courbe("sma200", t.s3, 2);
    if (surcouches.bollinger) {
      courbe("bb_upper", t.muted, 1, LineStyle.Dotted);
      courbe("bb_lower", t.muted, 1, LineStyle.Dotted);
    }
    if (surcouches.volume && bloc?.v?.some((x) => x)) {
      const h = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        // panneau du bas, sur sa propre échelle : le volume et le prix n'ont
        // aucune unité commune, les superposer sur le même axe n'a pas de sens
        priceScaleId: "volume",
        priceLineVisible: false, lastValueVisible: false,
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      h.setData(bougies.map((b, i) => ({
        time: b.time, value: bloc.v?.[i] ?? 0,
        color: b.close >= b.open ? `${t.hausse}55` : `${t.baisse}55`,
      })));
      ajoutees.push(h);
      volumes.current = h;
    }
    return () => { ajoutees.forEach((l) => { try { chart.removeSeries(l); } catch { /* déjà détruit */ } }); };
  }, [bloc, bougies, surcouches, t]);

  // --- lignes de prix : le plan de trade et les niveaux, tracés SUR le prix
  useEffect(() => {
    const s = principale.current;
    if (!s) return undefined;
    const posees = [];
    const poser = (prix, couleur, titre, style = LineStyle.Dashed) => {
      const p = Number(prix);
      if (!Number.isFinite(p) || p <= 0) return;
      posees.push(s.createPriceLine({
        price: p, color: couleur, lineWidth: 1, lineStyle: style,
        axisLabelVisible: true, title: titre,
      }));
    };
    if (plan) {
      poser(plan.entree, t.s1, "entrée", LineStyle.Solid);
      poser(plan.stop, t.baisse, "stop");
      poser(plan.objectif, t.hausse, "objectif");
    }
    for (const z of niveaux?.supports ?? []) poser(z.niveau, t.muted, "support", LineStyle.Dotted);
    for (const z of niveaux?.resistances ?? []) poser(z.niveau, t.muted, "résistance", LineStyle.Dotted);
    return () => { posees.forEach((l) => { try { s.removePriceLine(l); } catch { /* déjà détruit */ } }); };
  }, [plan, niveaux, t, type, bougies]);

  // --- réticule : la valeur exacte sous le curseur, remontée au parent
  useEffect(() => {
    const chart = graphique.current;
    const s = principale.current;
    if (!chart || !s || !onSurvol) return undefined;
    const cb = (param) => {
      if (!param?.time || !param.seriesData?.get(s)) { onSurvol(null); return; }
      const d = param.seriesData.get(s);
      const i = bougies.findIndex((b) => b.time === param.time);
      onSurvol({ ...d, time: param.time, volume: i >= 0 ? bloc?.v?.[i] : null });
    };
    chart.subscribeCrosshairMove(cb);
    return () => chart.unsubscribeCrosshairMove(cb);
  }, [onSurvol, bougies, bloc]);

  return <div ref={conteneur} style={{ width: "100%", height: hauteur }} />;
}
