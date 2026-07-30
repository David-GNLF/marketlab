import { createContext, useContext, useEffect, useMemo, useState } from "react";
import * as api from "./api";
import { Coquille, EtatDonnees } from "./coquille";
import { GraphiqueCone } from "./charts";
import {
  GardeGraphique, GraphiqueTerminal, PAS, TYPES, agreger, enBougies,
  fusionner, precision, socle,
} from "./graphique";

const pct = (v, signe = false) =>
  v == null ? "—" : `${signe && v > 0 ? "+" : ""}${Number(v).toFixed(2)} %`;
const nb = (v) =>
  v == null ? "—" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 4 });

/**
 * Marque MarketLab.
 *
 * Le dessin dit ce que fait l'outil : une courbe de marché, et sous elle la
 * LIGNE DE RÉFÉRENCE contre laquelle on la mesure. C'est exactement le
 * principe du projet — un rendement ne vaut que comparé à sa référence, une
 * probabilité que confrontée à ce qui advient. Pas de flèche montante :
 * l'outil ne promet pas la hausse, il mesure.
 *
 * Monochrome et en `currentColor` : lisible en thème clair comme sombre,
 * sans image à charger.
 */
function Tuile({ libelle, valeur, delta, note }) {
  return (
    <div className="tuile">
      <div className="libelle">{libelle}</div>
      <div className="valeur">{valeur}</div>
      {delta != null && (
        <div className={"delta " + (delta >= 0 ? "positif" : "negatif")}>
          {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(2)} %
        </div>
      )}
      {note && <div className="note">{note}</div>}
    </div>
  );
}

function Table({ lignes, colonnes, max }) {
  if (!lignes?.length) return <p className="note">Aucune donnée.</p>;
  const cols = colonnes ?? Object.keys(lignes[0]);
  const visibles = max ? lignes.slice(0, max) : lignes;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ml-table">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {visibles.map((l, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{typeof l[c] === "boolean" ? (l[c] ? "oui" : "non")
                  : l[c] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Chargement({ erreur }) {
  if (erreur) return <p className="erreur">{erreur}</p>;
  return <p className="note">Chargement…</p>;
}

// ------------------------------------------------------- cotations vivantes
// L'analyse date de l'instantané quotidien, mais les PRIX peuvent vivre :
// un seul sondage pour toute l'application, partagé par contexte.
const CoursContext = createContext({ cours: {}, actualise: null });
const useCours = () => useContext(CoursContext);

function FournisseurCours({ children }) {
  const [etat, setEtat] = useState({ cours: {}, actualise: null });
  useEffect(() => {
    let actif = true;
    // `premier` : un onglet ouvert en arrière-plan doit quand même afficher
    // des prix ; ce n'est qu'ENSUITE qu'on économise pendant qu'il est caché.
    const tirer = async (premier = false) => {
      if (document.hidden && !premier) return;
      const cours = await api.getCoursDirect();
      if (actif && Object.keys(cours).length) {
        setEtat({ cours, actualise: new Date() });
      }
    };
    tirer(true);
    const t = setInterval(tirer, 60000);
    const reveil = () => { if (!document.hidden) tirer(); };
    document.addEventListener("visibilitychange", reveil);
    return () => { actif = false; clearInterval(t);
                   document.removeEventListener("visibilitychange", reveil); };
  }, []);
  return <CoursContext.Provider value={etat}>{children}</CoursContext.Provider>;
}

function ageTexte(s) {
  if (s == null) return "âge inconnu";
  if (s < 90) return `il y a ${s} s`;
  if (s < 5400) return `il y a ${Math.round(s / 60)} min`;
  if (s < 172800) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}

/** Prix vivant d'un symbole, avec repli explicite sur le cours de
 *  l'instantané : on n'affiche jamais « direct » sans que ce soit vrai. */
function PrixVivant({ symbole, secours, avecAge = true }) {
  const { cours } = useCours();
  const c = cours[symbole];
  if (!c) return <>{nb(secours)}</>;
  return (
    <>
      {nb(c.prix)}
      {c.var_pct != null && (
        <span className={"delta " + (c.var_pct >= 0 ? "positif" : "negatif")}
              style={{ marginLeft: 6 }}>
          {c.var_pct >= 0 ? "+" : ""}{c.var_pct.toFixed(2)} %
        </span>
      )}
      {avecAge && (
        <span className="note" style={{ marginLeft: 6 }}
              title={c.source === "publié"
                ? "repli sur le dernier cours publié par le site"
                : "cotation du fournisseur"}>
          {c.source === "publié" ? "publié" : (c.frais ? "direct" : "différé")}{" "}
          {ageTexte(c.age_s)}
        </span>
      )}
    </>
  );
}

// Petit hook : charge une ressource et expose {donnees, erreur}
function useDonnees(chargeur, deps = []) {
  const [etat, setEtat] = useState({ donnees: null, erreur: null });
  useEffect(() => {
    let actif = true;
    setEtat({ donnees: null, erreur: null });
    chargeur()
      .then((d) => actif && setEtat({ donnees: d, erreur: null }))
      .catch((e) => actif && setEtat({ donnees: null, erreur: e.message }));
    return () => { actif = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return etat;
}

// ---------------------------------------------------------------- Marchés
const COULEUR_SCORE = (s) =>
  s == null ? "inherit" : s >= 40 ? "var(--good)" : s <= -40 ? "var(--critical)"
    : s >= 15 ? "#3f8f3f" : s <= -15 ? "#b35b5b" : "inherit";

const TRIS_MARCHES = {
  "score (meilleur d'abord)": (a, b) => (b.score ?? -999) - (a.score ?? -999),
  "score (pire d'abord)": (a, b) => (a.score ?? 999) - (b.score ?? 999),
  "performance 20 j": (a, b) => (b["perf_20j_%"] ?? -999) - (a["perf_20j_%"] ?? -999),
  "RSI (le plus haut)": (a, b) => (b.rsi14 ?? -1) - (a.rsi14 ?? -1),
  "RSI (le plus bas)": (a, b) => (a.rsi14 ?? 999) - (b.rsi14 ?? 999),
  "volatilité (la plus faible)": (a, b) => (a["vol_ann_%"] ?? 999) - (b["vol_ann_%"] ?? 999),
  "repli depuis le sommet": (a, b) => (b["drawdown_%"] ?? -999) - (a["drawdown_%"] ?? -999),
  "alphabétique": (a, b) => String(a.symbole).localeCompare(String(b.symbole)),
};

function PageMarches({ onTitre }) {
  const { donnees, erreur } = useDonnees(api.getScreener);
  const [filtre, setFiltre] = useState("");
  const [theme, setTheme] = useState("Tous");
  const [tri, setTri] = useState("score (meilleur d'abord)");
  if (!donnees) return <Chargement erreur={erreur} />;

  // Les thèmes viennent du serveur (config.UNIVERS fait foi) ; repli sur une
  // déduction par suffixe pour les instantanés publiés avant cet ajout.
  const themeDe = (l) => l.theme
    ?? (String(l.symbole).endsWith("=X") ? "Forex"
      : String(l.symbole).endsWith("=F") ? "Matières premières"
      : String(l.symbole).endsWith("USDT") ? "Crypto"
      : String(l.symbole).startsWith("^") ? "Indices" : "Actions");
  const themes = ["Tous", ...Array.from(new Set(donnees.map(themeDe))).sort()];

  const lignes = donnees
    .filter((l) => theme === "Tous" || themeDe(l) === theme)
    .filter((l) => {
      if (!filtre) return true;
      const q = filtre.toLowerCase();
      return String(l.symbole).toLowerCase().includes(q)
          || String(l.nom ?? "").toLowerCase().includes(q);
    })
    .slice()
    .sort(TRIS_MARCHES[tri]);
  const forts = lignes.filter((l) => l.avis === "Achat fort");
  const faibles = lignes.filter((l) => l.avis === "Vente forte");

  return (
    <div className="carte">
      <div className="rangee" style={{ marginBottom: 8 }}>
        <label className="champ">Thème
          <select value={theme} onChange={(e) => setTheme(e.target.value)}>
            {themes.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="champ">Classer par
          <select value={tri} onChange={(e) => setTri(e.target.value)}>
            {Object.keys(TRIS_MARCHES).map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="champ">Chercher
          <input type="text" value={filtre} placeholder="AAPL, cuivre…"
                 onChange={(e) => setFiltre(e.target.value)} />
        </label>
      </div>
      <div className="rangee" style={{ marginBottom: 12 }}>
        <Tuile libelle={theme === "Tous" ? "Actifs suivis" : theme}
               valeur={lignes.length} />
        <Tuile libelle="Achat fort" valeur={forts.length}
               note={forts.map((l) => l.symbole).join(", ") || "—"} />
        <Tuile libelle="Vente forte" valeur={faibles.length}
               note={faibles.map((l) => l.symbole).join(", ") || "—"} />
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="ml-table">
          <thead><tr>
            {["actif", "thème", "score", "avis", "cours", "RSI",
              "perf 20 j", "volatilité", "repli"].map((c) => <th key={c}>{c}</th>)}
          </tr></thead>
          <tbody>
            {lignes.map((l, i) => (
              <tr key={i} onClick={() => onTitre(l.symbole)}
                  style={{ cursor: "pointer" }}>
                <td><strong>{l.symbole}</strong>
                  {l.nom && l.nom !== l.symbole &&
                    <div className="note">{l.nom}</div>}</td>
                <td className="note">{themeDe(l)}</td>
                <td style={{ color: COULEUR_SCORE(l.score), fontWeight: 600 }}>
                  {l.score ?? "—"}</td>
                <td><span className="badge" style={{
                      color: COULEUR_AVIS[l.avis] ?? "inherit" }}>
                  {l.avis ?? "—"}</span></td>
                <td><PrixVivant symbole={l.symbole} secours={l.cours}
                                avecAge={false} /></td>
                <td>{l.rsi14 == null ? "—" : (
                  <span style={{ color: l.rsi14 >= 70 ? "var(--critical)"
                                   : l.rsi14 <= 30 ? "var(--good)" : "inherit" }}>
                    {l.rsi14}</span>)}</td>
                <td className={"delta " + ((l["perf_20j_%"] ?? 0) >= 0 ? "positif" : "negatif")}>
                  {pct(l["perf_20j_%"], true)}</td>
                <td className="note">{pct(l["vol_ann_%"])}</td>
                <td className="delta negatif">{pct(l["drawdown_%"])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">Cliquer sur une ligne pour ouvrir la fiche du titre.
        La colonne « cours » est rafraîchie en continu ; les indicateurs
        (score, avis, RSI…) datent de l'instantané quotidien. Un RSI ≥ 70
        signale un surachat, ≤ 30 une survente — deux situations où le
        consensus se retourne souvent.</p>
    </div>
  );
}

// ---------------------------------------------------------------- Fiche titre
//
// La fiche est le poste de travail du site : c'est là qu'on décide. Elle est
// donc bâtie comme un terminal de marché et non comme un article — liste de
// suivi à gauche, graphique et son barreau d'outils au centre, analyse en
// dessous. Ce qui manquait à la version précédente n'était pas cosmétique :
// une seule échelle de temps, un seul type de tracé, aucun volume, aucune
// bougie, et le plan de trade (entrée, stop, objectif) écrit en toutes lettres
// à côté d'une courbe où il n'apparaissait pas.

/**
 * Série de bougies d'un titre : socle publié + tête fraîche du relais.
 *
 * Deux origines, deux rôles. Le fichier publié porte cinq ans de quotidien et
 * cinq séances de barres 5 min — la structure. Le relais `serie.php` porte la
 * séance en cours — l'exécution. Les recoller ici, une fois, évite que chaque
 * composant se demande laquelle des deux il regarde.
 *
 * Le rafraîchissement ne tourne QUE sur les échelles fines et onglet visible :
 * sur une vue à cinq ans, une barre de plus ne change rien à l'écran et
 * n'aurait pour effet que de consommer le quota de la source.
 */
function useSerie(symbole, fine) {
  const { donnees, erreur } = useDonnees(() => api.getSerie(symbole), [symbole]);
  const [fraiche, setFraiche] = useState(null);

  useEffect(() => {
    setFraiche(null);
    if (!symbole || !fine) return undefined;
    let actif = true;
    const tirer = async (premier = false) => {
      if (document.hidden && !premier) return;
      const s = await api.getSerieFraiche(symbole, "5m");
      if (actif && s) setFraiche(s);
    };
    tirer(true);
    const t = setInterval(tirer, 60000);
    const reveil = () => { if (!document.hidden) tirer(); };
    document.addEventListener("visibilitychange", reveil);
    return () => { actif = false; clearInterval(t);
                   document.removeEventListener("visibilitychange", reveil); };
  }, [symbole, fine]);

  // Les trois socles publiés, la tête fraîche recollée sur le plus fin.
  const series = useMemo(() => ({
    quotidien: donnees?.quotidien,
    horaire: donnees?.horaire,
    intraday: fusionner(donnees?.intraday, fraiche),
  }), [donnees, fraiche]);
  return { series, erreur, fraiche: Boolean(fraiche) };
}

/** Liste de suivi : le sélecteur de titre d'un terminal, pas un menu déroulant.
 *  Symbole, nom, prix vivant et variation — de quoi choisir où regarder sans
 *  ouvrir six fiches. */
function ListeSuivi({ titres, actif, onChoisir }) {
  const { donnees: screener } = useDonnees(api.getScreener);
  const [filtre, setFiltre] = useState("");
  const par_symbole = useMemo(() => {
    const m = new Map();
    for (const l of screener ?? []) m.set(l.symbole, l);
    return m;
  }, [screener]);

  const q = filtre.trim().toLowerCase();
  const lignes = titres
    .map((s) => ({ symbole: s, ...(par_symbole.get(s) ?? {}) }))
    .filter((l) => !q || l.symbole.toLowerCase().includes(q)
                || String(l.nom ?? "").toLowerCase().includes(q));

  return (
    <aside className="ml-rail">
      <input type="text" className="ml-rail-recherche" value={filtre}
             placeholder="Filtrer la liste…"
             onChange={(e) => setFiltre(e.target.value)} />
      <div className="ml-rail-liste">
        {lignes.map((l) => (
          <button key={l.symbole} type="button"
                  className={"ml-rail-ligne" + (l.symbole === actif ? " actif" : "")}
                  onClick={() => onChoisir(l.symbole)}>
            <span className="ml-rail-sym">{l.symbole}</span>
            <span className="ml-rail-nom">{l.nom ?? ""}</span>
            <span className="ml-rail-prix">
              <PrixVivant symbole={l.symbole} secours={l.cours} avecAge={false} />
              {l.avis && <span className="ml-rail-avis"
                               style={{ color: COULEUR_SCORE(l.score) }}>
                {l.avis}</span>}
            </span>
          </button>
        ))}
        {!lignes.length && <p className="note" style={{ padding: "8px 10px" }}>
          Aucun titre ne correspond.</p>}
      </div>
    </aside>
  );
}

/** Barreau d'outils du graphique : échelle de temps, type de tracé, surcouches. */
function BarreOutils({ pas, setPas, type, setType,
                       surcouches, setSurcouches, log, setLog, dispo }) {
  const bascule = (cle) => setSurcouches((s) => ({ ...s, [cle]: !s[cle] }));
  const surcouche = (cle, libelle, titre) => (
    <button type="button" title={titre}
            className={"ml-chip" + (surcouches[cle] ? " actif" : "")}
            onClick={() => bascule(cle)}>{libelle}</button>
  );
  return (
    <div className="ml-outils">
      <div className="ml-groupe" role="group" aria-label="Pas de bougie">
        {PAS.map((e) => {
          // Sans le socle correspondant (BRVM, titre récent, source en
          // panne), le pas n'a rien à montrer : mieux vaut un bouton
          // désactivé et explicite qu'un graphique vide.
          const off = !dispo?.[e.base];
          return (
            <button key={e.cle} type="button" disabled={off}
                    title={off
                      ? `aucune barre ${e.libelle} disponible pour ce titre`
                      : `une bougie = ${e.libelle}`}
                    className={"ml-chip" + (pas === e.cle ? " actif" : "")}
                    onClick={() => setPas(e.cle)}>{e.libelle}</button>
          );
        })}
      </div>
      <div className="ml-groupe" role="group" aria-label="Type de tracé">
        {TYPES.map((t) => (
          <button key={t.cle} type="button"
                  className={"ml-chip" + (type === t.cle ? " actif" : "")}
                  onClick={() => setType(t.cle)}>{t.libelle}</button>
        ))}
      </div>
      <div className="ml-groupe" role="group" aria-label="Surcouches">
        {surcouche("sma50", "MM 50", "moyenne mobile 50 séances")}
        {surcouche("sma200", "MM 200", "moyenne mobile 200 séances")}
        {surcouche("bollinger", "Bollinger", "bandes de Bollinger (2 écarts-types)")}
        {surcouche("volume", "Volume", "volume échangé, en sous-panneau")}
        {surcouche("plan", "Plan", "entrée, stop et objectif tracés sur le prix")}
        {surcouche("niveaux", "Niveaux", "supports et résistances repérés")}
        <button type="button" title="axe des prix logarithmique — à préférer sur les longues périodes"
                className={"ml-chip" + (log ? " actif" : "")}
                onClick={() => setLog((v) => !v)}>Log</button>
      </div>
    </div>
  );
}

/** Lecture OHLC sous le réticule. Un graphique de trading sans cette ligne
 *  oblige à deviner les valeurs à l'œil sur l'axe. */
function LectureOHLC({ point, derniere, dec }) {
  const b = point ?? derniere;
  if (!b) return null;
  const f = (v) => v == null ? "—"
    : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: dec,
                                          maximumFractionDigits: dec });
  const hausse = b.close >= b.open;
  const ampleur = b.open ? ((b.close / b.open - 1) * 100) : null;
  return (
    <div className="ml-ohlc">
      <span>O <b>{f(b.open)}</b></span>
      <span>H <b>{f(b.high)}</b></span>
      <span>B <b>{f(b.low)}</b></span>
      <span>C <b className={hausse ? "delta positif" : "delta negatif"}>
        {f(b.close)}</b></span>
      {ampleur != null && (
        <span className={"delta " + (hausse ? "positif" : "negatif")}>
          {ampleur >= 0 ? "+" : ""}{ampleur.toFixed(2)} %</span>
      )}
      {b.volume ? <span className="note">vol {Number(b.volume)
        .toLocaleString("fr-FR")}</span> : null}
      {!point && <span className="note">dernière bougie — survolez le
        graphique pour lire une autre séance</span>}
    </div>
  );
}

function PageTitre({ meta, symbole, setSymbole }) {
  const dispo = meta?.titres ?? [];
  const actif = symbole && dispo.includes(symbole) ? symbole : dispo[0];
  const { donnees: f, erreur } = useDonnees(() => api.getTitre(actif), [actif]);

  const [pas, setPas] = useState("D1");
  const [type, setType] = useState("bougies");
  const [log, setLog] = useState(false);
  const [surcouches, setSurcouches] = useState({
    sma50: true, sma200: true, bollinger: false, volume: true,
    plan: true, niveaux: false,
  });
  const [survol, setSurvol] = useState(null);

  const conf = PAS.find((e) => e.cle === pas) ?? PAS[4];
  const fine = conf.base === "intraday";
  const { series, fraiche } = useSerie(actif, fine);

  // Le bloc réellement tracé : le socle qui convient au pas, agrégé au pas.
  const bloc = useMemo(() => {
    const base = socle(series, conf);
    return base ? agreger(base, conf) : null;
  }, [series, conf]);
  const soclesDispo = useMemo(() => ({
    intraday: Boolean(series.intraday?.t?.length),
    horaire: Boolean(series.horaire?.t?.length),
    quotidien: Boolean(series.quotidien?.t?.length),
  }), [series]);

  // Sur une vue intrajournalière, les moyennes 50/200 séances n'existent pas :
  // les demander tracerait une ligne plate absurde. On les retire de la
  // demande sans toucher aux préférences de l'utilisateur, qui les retrouve
  // intactes en repassant au quotidien.
  const surcouchesActives = useMemo(() => ({
    ...surcouches,
    // Les moyennes et bandes publiées sont calculées sur des SÉANCES :
    // les afficher sur un pas de 5 minutes ou de quatre heures tracerait
    // une courbe qui ne veut rien dire. Elles restent cochées, simplement
    // sans effet hors du pas quotidien, et l'utilisateur les retrouve.
    sma50: surcouches.sma50 && conf.cle === "D1",
    sma200: surcouches.sma200 && conf.cle === "D1",
    bollinger: surcouches.bollinger && conf.cle === "D1",
  }), [surcouches, conf]);

  const derniere = useMemo(() => {
    const b = enBougies(bloc);
    const d = b.at(-1);
    return d ? { ...d, volume: bloc?.v?.at(-1) } : null;
  }, [bloc]);
  const dec = precision(derniere?.close ?? f?.signaux?.close);

  return (
    <div className="ml-terminal">
      <ListeSuivi titres={dispo} actif={actif} onChoisir={setSymbole} />

      <div className="ml-terminal-corps">
        {!f ? <Chargement erreur={erreur} /> : (
          <>
            <div className="carte ml-entete-titre">
              <div>
                <div className="ml-titre-nom">{f.nom}</div>
                <div className="note">{f.symbole}</div>
              </div>
              <div className="ml-titre-prix">
                <PrixVivant symbole={f.symbole} secours={f.signaux?.close} />
              </div>
              <div className="ml-titre-avis">
                <span className="badge" style={{ color: COULEUR_SCORE(f.signaux?.score) }}>
                  {f.signaux?.avis}</span>
                <span className="note">score {f.signaux?.score ?? "—"} ·
                  RSI {f.signaux?.rsi14 ?? "—"} ·
                  20 j {pct(f.signaux?.ret_20d, true)}
                  {f.regime && !f.regime.erreur &&
                    <> · {f.regime.tendance}, volatilité {f.regime.volatilite}</>}
                </span>
              </div>
            </div>

            <div className="carte">
              <BarreOutils pas={pas} setPas={setPas}
                           type={type} setType={setType}
                           surcouches={surcouches} setSurcouches={setSurcouches}
                           log={log} setLog={setLog} dispo={soclesDispo} />
              <LectureOHLC point={survol} derniere={derniere} dec={dec} />
              {bloc?.t?.length ? (
                <GardeGraphique key={`${actif}-${type}-${pas}`}>
                  <GraphiqueTerminal
                    bloc={bloc} type={type} surcouches={surcouchesActives}
                    barresVisibles={conf.visibles}
                    logarithmique={log} hauteur={430} onSurvol={setSurvol}
                    plan={surcouches.plan ? f.strategie?.plan : null}
                    niveaux={surcouches.niveaux ? f.niveaux?.zones : null} />
                </GardeGraphique>
              ) : (
                <p className="note" style={{ padding: "40px 0", textAlign: "center" }}>
                  Série de bougies indisponible pour ce titre. La prochaine
                  publication la produira.</p>
              )}
              <p className="note">
                {`Une bougie = ${conf.libelle}. `}
                {fine
                  ? (fraiche
                     ? "Série rafraîchie chaque minute depuis la source."
                     : "Série de la dernière publication : le relais direct n'a rien renvoyé (marché fermé ou source indisponible).")
                  : conf.cle === "D1"
                    ? "Clôture à clôture, cinq ans d'historique."
                    : "Agrégé dans le navigateur à partir du socle "
                      + (conf.base === "horaire" ? "horaire." : "quotidien.")}
                {surcouches.plan && f.strategie?.plan &&
                  " Le plan du verdict est tracé sur le prix : entrée pleine, stop en rouge, objectif en vert."}
                {" "}Molette pour zoomer, glisser pour se déplacer.
              </p>
            </div>

            {f.strategie && !f.strategie.erreur && (
              <div className="carte" style={{ borderLeft: "3px solid var(--series-1)" }}>
                <h3 style={{ marginTop: 0 }}>{f.strategie.nom} — stratégie de
                  position</h3>
                <div className="rangee">
                  <div className="tuile" style={{ minWidth: 220 }}>
                    <div className="libelle">QUEL SENS ?</div>
                    <div className="valeur" style={{ fontSize: 17 }}>
                      {f.strategie.sens.reponse}</div>
                    <div className="note">{f.strategie.sens.raison}</div>
                  </div>
                  <div className="tuile" style={{ minWidth: 220 }}>
                    <div className="libelle">QUAND ?</div>
                    <div className="valeur" style={{ fontSize: 17 }}>
                      {f.strategie.quand.reponse}</div>
                    <div className="note">{f.strategie.quand.raison}</div>
                  </div>
                  {f.strategie.marge && (
                    <div className="tuile" style={{ minWidth: 220 }}>
                      <div className="libelle">QUELLE MARGE ?</div>
                      <div className="valeur" style={{ fontSize: 17 }}>
                        +{f.strategie.marge["objectif_%"]} % visés</div>
                      <div className="note">{f.strategie.marge.lecture} Scénario
                        porteur : {f.strategie.marge["scenario_porteur_%"] > 0 ? "+" : ""}
                        {f.strategie.marge["scenario_porteur_%"]} %.</div>
                    </div>
                  )}
                </div>
                {f.strategie.verdict?.conclusion?.texte && (
                  <p style={{ marginTop: 10 }}><strong>Conclusion</strong> —{" "}
                    {f.strategie.verdict.conclusion.texte}</p>
                )}
                {f.strategie.plan && (
                  <p style={{ marginTop: 10 }}>Entrée {nb(f.strategie.plan.entree)} ·
                    stop {nb(f.strategie.plan.stop)} · objectif{" "}
                    {nb(f.strategie.plan.objectif)} · ratio{" "}
                    {f.strategie.plan.ratio_gain_risque}
                    {f.strategie.taille && <> · taille {f.strategie.taille.montant} $
                      (perte max {f.strategie.taille.perte_max} $
                      {f.strategie.taille.plafond_kelly != null &&
                        ", plafond Kelly appliqué"})</>}
                  </p>
                )}
                {f.strategie.plan?.stop_suiveur && (
                  <p className="note">Stop suiveur (Chandelier) :{" "}
                    {nb(f.strategie.plan.stop_suiveur.niveau)} —{" "}
                    {f.strategie.plan.stop_suiveur.raison}</p>
                )}
                {f.strategie.renforts && (
                  <p className="note">Renforts ({f.strategie.renforts.feux_verts}
                    {" "}feux verts) —{" "}
                    {f.strategie.renforts.confluence?.raison}
                    {f.strategie.renforts.force_relative?.raison &&
                      <> · {f.strategie.renforts.force_relative.raison}</>}
                    {f.strategie.renforts.cot?.raison &&
                      <> · COT : {f.strategie.renforts.cot.raison}</>}
                    {f.strategie.renforts.kelly?.raison &&
                      <> · {f.strategie.renforts.kelly.raison}</>}</p>
                )}
                {f.strategie.verdict?.vetos?.map((v, i) => (
                  <p key={i} className="erreur">{v}</p>
                ))}
              </div>
            )}

            {f.regime?.lecture && (
              <p className="note" style={{ marginTop: -6 }}>{f.regime.lecture}</p>
            )}

            {f.projection && !f.projection.erreur && (
              <div className="carte">
                <h3>Prévision à {f.projection.horizon} séances</h3>
                <div className="rangee">
                  <Tuile libelle="Médiane projetée" valeur={nb(f.projection.prix_median)}
                         delta={f.projection["rendement_median_%"]} />
                  <Tuile libelle="Intervalle 80 %"
                         valeur={f.projection.intervalle_80?.map(nb).join(" – ")} />
                  <Tuile libelle="P(hausse)"
                         valeur={`${f.projection["proba_hausse_%"]} %`} />
                  <Tuile libelle="VaR 95 %" valeur={`${f.projection["var_95_%"]} %`} />
                </div>
                <GraphiqueCone projection={f.projection}
                               historique={f.historique} />
                <p className="note">Cône de probabilités, pas un prix cible. La
                  direction reste peu prévisible ; ce sont les intervalles et le
                  risque qui sont exploitables.</p>
              </div>
            )}

            {f.brokers?.outils && (
              <div className="carte">
                <h3>Les outils des brokers</h3>
                <p className="note">Les six indicateurs les plus utilisés sur les
                  plateformes professionnelles. Ils décrivent — le verdict et ses
                  garde-fous restent seuls décideurs.</p>
                {f.brokers.outils.map((o) => (
                  <p key={o.outil} style={{ margin: "5px 0" }}>
                    <strong style={{ display: "inline-block", minWidth: 110 }}>
                      {o.outil}</strong>
                    <span className="badge" style={{ marginRight: 8,
                      color: o.signal === "haussier" ? "var(--good)"
                        : o.signal === "baissier" ? "var(--critical)"
                        : "var(--muted)" }}>{o.signal}</span>
                    <span className="note">{o.lecture}</span>
                  </p>
                ))}
                <p style={{ fontWeight: 600 }}>Consensus :{" "}
                  {f.brokers.consensus?.texte}</p>
                {f.brokers.avertissement_regime && (
                  <p className="erreur">{f.brokers.avertissement_regime}</p>
                )}
              </div>
            )}
            <div className="carte">
              <h3>Contexte</h3>
              {f.analogues && !f.analogues.erreur && (
                <p><strong>Analogues historiques</strong> — sur les {f.analogues.k}
                  {" "}configurations passées les plus proches : hausse dans{" "}
                  <strong>{f.analogues["proba_hausse_%"]} %</strong> des cas,
                  rendement médian {pct(f.analogues["rendement_median_%"], true)}
                  {" "}(extrêmes {pct(f.analogues["pire_%"])} /{" "}
                  {pct(f.analogues["meilleur_%"])}).</p>
              )}
              {f.niveaux?.zones && (
                <p><strong>Niveaux</strong> — supports :{" "}
                  {f.niveaux.zones.supports?.map((z) => nb(z.niveau)).join(", ") || "aucun"}
                  {" "}· résistances :{" "}
                  {f.niveaux.zones.resistances?.map((z) => nb(z.niveau)).join(", ") || "aucune"}
                  {" "}<span className="note">(bouton « Niveaux » pour les tracer)</span></p>
              )}
              {f.resultats && !f.resultats.erreur && f.resultats.message && (
                <p className={f.resultats.dans_horizon ? "erreur" : ""}>
                  <strong>Résultats</strong> — {f.resultats.message}</p>
              )}
              {f.moteurs?.length > 0 && f.moteurs.map((m, i) => (
                <p key={i}><strong>{m.outil}</strong> — {m.lecture}</p>
              ))}
              {f.saisonnalite && !f.saisonnalite.erreur && (
                <p><strong>Saisonnalité</strong> — {f.saisonnalite.conclusion}</p>
              )}
              {f.sentiment && !f.sentiment.erreur && f.sentiment.n_titres > 0 && (
                <p><strong>Actualités</strong> — sentiment {f.sentiment.lecture}
                  {" "}({f.sentiment.positifs} positifs / {f.sentiment.negatifs} négatifs sur{" "}
                  {f.sentiment.n_titres} titres, mesure lexicale indicative).</p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Macro
function PageMacro() {
  const macro = useDonnees(api.getMacro);
  const cal = useDonnees(api.getCalendrier);
  const res = useDonnees(api.getResultats);
  const cotD = useDonnees(api.getCot);
  const fg = useDonnees(api.getSentimentMarche);
  const baro = useDonnees(api.getBarometres);
  return (
    <>
      <div className="carte">
        <h3>Thermomètre peur / avidité</h3>
        {!fg.donnees ? <Chargement erreur={fg.erreur} /> : (
          <>
            <div className="rangee" style={{ alignItems: "center" }}>
              <div className="tuile">
                <div className="libelle">Indice (0 = peur, 100 = avidité)</div>
                <div className="valeur" style={{
                  color: fg.donnees.valeur <= 25 ? "var(--critical)"
                    : fg.donnees.valeur >= 75 ? "var(--series-2)" : "inherit" }}>
                  {fg.donnees.valeur} — {fg.donnees.zone}</div>
              </div>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={{ background: "var(--grid)", borderRadius: 6,
                              height: 10, position: "relative" }}>
                  <div style={{ position: "absolute", left: `${fg.donnees.valeur}%`,
                                top: -4, width: 4, height: 18, borderRadius: 2,
                                background: "var(--series-1)",
                                transform: "translateX(-50%)" }} />
                </div>
                <div className="note" style={{ display: "flex",
                                               justifyContent: "space-between" }}>
                  <span>peur extrême</span><span>neutre</span><span>avidité extrême</span>
                </div>
              </div>
            </div>
            <p>{fg.donnees.lecture}</p>
            {fg.donnees.composantes?.map((c, i) => (
              <p key={i} className="note" style={{ margin: "3px 0" }}>
                <strong style={{ display: "inline-block", minWidth: 180 }}>
                  {c.nom}</strong>
                <span style={{ display: "inline-block", minWidth: 42 }}>
                  {c.note ?? "—"}</span> {c.detail}</p>
            ))}
            <p className="note">{fg.donnees.methode}</p>
          </>
        )}
      </div>
      <div className="carte">
        <h3>Régime macroéconomique</h3>
        {!macro.donnees ? <Chargement erreur={macro.erreur} /> : (
          <>
            <p><strong>{macro.donnees.regime?.lecture ?? "—"}</strong></p>
            {macro.donnees.regime?.details?.map((d, i) => (
              <p key={i} className="note">• {d}</p>
            ))}
            <Table lignes={macro.donnees.indicateurs} />
          </>
        )}
      </div>
      <div className="carte">
        <h3>Événements économiques à venir</h3>
        {!cal.donnees ? <Chargement erreur={cal.erreur} /> : (
          <Table lignes={cal.donnees} max={25}
                 colonnes={["quand", "devise", "evenement", "impact_fr",
                            "prevision", "precedent"]} />
        )}
      </div>
      <div className="carte">
        <h3>Publications de résultats</h3>
        {!res.donnees ? <Chargement erreur={res.erreur} /> : (
          <Table lignes={res.donnees} max={25}
                 colonnes={["symbole", "date", "dans_jours",
                            "amplitude_historique_%"]} />
        )}
      </div>
      {baro.donnees && (
        <div className="carte">
          <h3>Baromètres cross-asset</h3>
          <p>{baro.donnees.lecture}</p>
        </div>
      )}
      <div className="carte">
        <h3>Positionnement des spéculateurs (COT, CFTC)</h3>
        <p className="note">Rapport hebdomadaire officiel : position nette des
          fonds spéculatifs sur les contrats à terme américains. COT index =
          position actuelle rapportée à ses extrêmes sur 3 ans — au-delà de 85
          ou sous 15, le trade est « encombré » : tout le monde est déjà du
          même côté.</p>
        {!cotD.donnees ? <Chargement erreur={cotD.erreur} /> : (
          <Table lignes={[...cotD.donnees].sort(
                   (a, b) => (b.cot_index_3ans ?? 0) - (a.cot_index_3ans ?? 0))}
                 colonnes={["nom", "net_speculateurs", "variation_1_sem",
                            "cot_index_3ans", "extreme", "date_rapport"]} />
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------- Fondamentaux
function PageFondamentaux() {
  const { donnees, erreur } = useDonnees(api.getFondamentaux);
  return (
    <div className="carte">
      <h3>Notation fondamentale</h3>
      <p className="note">Quatre axes notés sur 100 : valorisation, qualité,
        croissance, solidité. Les seuils ne sont pas normalisés par secteur —
        comparer des titres comparables.</p>
      {!donnees ? <Chargement erreur={erreur} /> : (
        <Table lignes={donnees}
               colonnes={["symbole", "nom", "secteur", "score", "valorisation",
                          "qualite", "croissance", "solidite", "per",
                          "marge_nette_%", "dividende_%"]} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Corrélations
function PageCorrelations() {
  const { donnees, erreur } = useDonnees(api.getCorrelations);
  if (!donnees) return <Chargement erreur={erreur} />;
  const pf = donnees.portefeuille;
  return (
    <>
      <div className="carte">
        <h3>Corrélations</h3>
        {donnees.par_regime && (
          <p>En marché calme :{" "}
            <strong>{donnees.par_regime.correlation_moyenne_calme}</strong> ·
            en marché agité :{" "}
            <strong>{donnees.par_regime.correlation_moyenne_agite}</strong>.{" "}
            {donnees.par_regime.lecture}</p>
        )}
        <div className="rangee" style={{ alignItems: "start" }}>
          <div style={{ flex: 1, minWidth: 260 }}>
            <h4>Les plus corrélées — doublons possibles</h4>
            {donnees.extremes?.plus_correlees?.map((x, i) => (
              <p key={i} className="note">{x.a} / {x.b} → <strong>{x.correlation}</strong></p>
            ))}
          </div>
          <div style={{ flex: 1, minWidth: 260 }}>
            <h4>Les moins corrélées — vraie diversification</h4>
            {donnees.extremes?.moins_correlees?.map((x, i) => (
              <p key={i} className="note">{x.a} / {x.b} → <strong>{x.correlation}</strong></p>
            ))}
          </div>
        </div>
      </div>
      {pf && (
        <div className="carte">
          <h3>Risque du portefeuille</h3>
          <div className="rangee">
            <Tuile libelle="Volatilité" valeur={`${pf["vol_portefeuille_%"]} %`} />
            <Tuile libelle="Sans diversification"
                   valeur={`${pf["vol_sans_diversification_%"]} %`} />
            <Tuile libelle="Bénéfice diversification"
                   valeur={`${pf["benefice_diversification_%"]} %`} />
            <Tuile libelle="Positions équivalentes"
                   valeur={pf.equivalent_positions} />
          </div>
          <Table lignes={pf.lignes} />
          <p className="note">{pf.lecture}</p>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- Portefeuille
function PagePortefeuille() {
  const { donnees, erreur } = useDonnees(api.getPaper);
  if (!donnees) return <Chargement erreur={erreur} />;
  return (
    <div className="carte">
      <h3>Portefeuille papier</h3>
      <div className="rangee">
        <Tuile libelle="Valeur totale"
               valeur={`${nb(donnees.valeur_totale_usd)} $`}
               delta={donnees["perf_totale_%"]} />
        <Tuile libelle="Cash" valeur={`${nb(donnees.cash_usd)} $`} />
        <Tuile libelle="Positions"
               valeur={`${nb(donnees.valeur_positions_usd)} $`} />
        <Tuile libelle="Transactions" valeur={donnees.nb_transactions} />
      </div>
      <Table lignes={donnees.positions} />
      <p className="note">Consultation seule. Les opérations se font depuis le
        poste de travail (scripts/paper.py) : ce site est un miroir publié.</p>
    </div>
  );
}

// ---------------------------------------------------------------- Décisions
const COULEUR_AVIS = {
  "Favorable": "var(--good)",
  "Défavorable": "var(--critical)",
  "S'abstenir": "var(--critical)",
  "Neutre": "var(--muted)",
};

// Ce qu'il faut FAIRE, écrit en toutes lettres : n'importe qui doit
// comprendre la carte sans connaître la mécanique interne.
function actionExplicite(d) {
  if (d.avis === "S'abstenir") {
    return { texte: "NE PAS TRADER — un garde-fou bloque ce dossier",
             couleur: "var(--critical)" };
  }
  if (d.avis === "Favorable" && d.plan) {
    const t = d.taille_multiplicateur < 1
      ? ` à taille réduite (×${d.taille_multiplicateur})` : "";
    return { texte: `ACHAT envisageable${t} — entrée ${nb(d.plan.entree)}, `
             + `stop ${nb(d.plan.stop)}, objectif ${nb(d.plan.objectif)}`,
             couleur: "var(--good)" };
  }
  if (d.avis === "Défavorable") {
    return { texte: "ÉVITER À L'ACHAT — analyses défavorables (le bilan "
             + "déconseille aussi la vente à découvert : rester à l'écart)",
             couleur: "var(--critical)" };
  }
  return { texte: "SURVEILLER — les analyses ne convergent pas assez pour agir",
           couleur: "var(--muted)" };
}

function BarreNote({ note }) {
  const pct = Math.min(100, Math.max(0, (note + 100) / 2));
  return (
    <div style={{ background: "var(--grid)", borderRadius: 4, height: 8,
                  width: 120, position: "relative" }}
         title={`note ${note} sur une échelle de −100 à +100`}>
      <div style={{ position: "absolute", left: "50%", top: -2, width: 1,
                    height: 12, background: "var(--baseline)" }} />
      <div style={{ position: "absolute", left: `${pct}%`, top: -3, width: 6,
                    height: 14, borderRadius: 3, transform: "translateX(-50%)",
                    background: note >= 30 ? "var(--good)"
                      : note <= -30 ? "var(--critical)" : "var(--series-1)" }} />
    </div>
  );
}

/**
 * Où en est le prix VIF par rapport au plan du jour.
 *
 * Le plan (entrée, stop, objectif) est recalculé une fois par jour, mais le
 * prix bouge en permanence — et c'est précisément l'écart entre les deux qui
 * dit s'il est encore temps d'entrer. Le ratio gain/risque, notamment, se
 * dégrade à mesure que le cours monte vers l'objectif : un plan excellent au
 * moment du calcul peut ne plus valoir grand-chose deux heures plus tard.
 * Rien n'est recalculé ici, tout est déduit du prix en direct.
 */
function PositionVive({ symbole, plan }) {
  const { cours } = useCours();
  const c = cours[symbole];
  if (!plan || !c) return null;
  const { prix } = c;
  const { entree, stop, objectif } = plan;
  if (!entree || !stop || !objectif) return null;

  const ecart = (prix / entree - 1) * 100;
  const versStop = (prix / stop - 1) * 100;
  const versObjectif = (objectif / prix - 1) * 100;
  const rr = prix > stop ? (objectif - prix) / (prix - stop) : null;

  let etat, couleur;
  if (prix <= stop) {
    etat = "Le stop du plan est déjà franchi — plan caduc, ne pas entrer";
    couleur = "var(--critical)";
  } else if (prix >= objectif) {
    etat = "L'objectif du plan est déjà atteint — le mouvement a eu lieu";
    couleur = "var(--muted)";
  } else if (Math.abs(ecart) <= 0.5) {
    etat = "Au prix du plan — c'est le point d'entrée prévu";
    couleur = "var(--good)";
  } else if (ecart > 0.5) {
    etat = `${pct(ecart)} au-dessus du plan — entrer maintenant dégrade le `
         + `rapport gain/risque ; attendre un repli vers ${nb(entree)}`;
    couleur = "var(--series-2)";
  } else {
    etat = `${pct(Math.abs(ecart))} sous le prix du plan — point d'entrée `
         + `plus favorable qu'au calcul`;
    couleur = "var(--good)";
  }

  return (
    <div style={{ marginTop: 8, fontSize: 13 }}>
      <span style={{ color: couleur, fontWeight: 600 }}>{etat}</span>
      <div className="note">
        cours vif {nb(prix)} ·{" "}
        {versStop >= 0 ? `stop à ${pct(versStop)} sous le cours`
                       : `stop franchi de ${pct(-versStop)}`} ·{" "}
        {versObjectif >= 0 ? `objectif à ${pct(versObjectif)} au-dessus`
                           : `objectif dépassé de ${pct(-versObjectif)}`}
        {rr != null && <> · rapport gain/risque au cours actuel{" "}
          <strong style={{ color: rr >= 1.5 ? "var(--good)"
                                 : rr >= 1 ? "inherit" : "var(--critical)" }}>
            {rr.toFixed(2)}</strong>
          {plan.ratio_gain_risque != null &&
            ` (${plan.ratio_gain_risque} au calcul)`}</>}
      </div>
    </div>
  );
}

/** Le pari court, affiché à côté du long : deux fenêtres, deux paris. */
function PlanCourt({ court, horizon }) {
  if (!court || court.erreur) return null;
  const p = court.plan;
  const couleur = court.avis === "Favorable" ? "var(--good)"
    : court.avis === "S'abstenir" || court.avis === "Défavorable"
      ? "var(--critical)" : "var(--muted)";
  return (
    <div style={{ marginTop: 8, paddingTop: 8,
                  borderTop: "1px dashed var(--grid)", fontSize: 13 }}>
      <span className="badge" style={{ marginRight: 8 }}>
        {horizon} séances</span>
      <span style={{ color: couleur, fontWeight: 600 }}>{court.avis}</span>
      <span className="note"> · note {court.note_globale > 0 ? "+" : ""}
        {court.note_globale}</span>
      {p ? (
        <span> — entrée {nb(p.entree)}, stop {nb(p.stop)}, objectif{" "}
          {nb(p.objectif)} <span className="note">(risque{" "}
          {pct(Math.abs(p.stop / p.entree - 1) * 100)}, R/R{" "}
          {p.ratio_gain_risque})</span></span>
      ) : <span className="note"> — pas de plan à cette échéance</span>}
    </div>
  );
}

function Verdict({ d, rang, onTitre, court, horizonCourt }) {
  const [ouvert, setOuvert] = useState(false);
  if (d.erreur) {
    return <div className="carte"><strong>{d.symbole}</strong>{" "}
      <span className="note">indisponible : {d.erreur}</span></div>;
  }
  const action = actionExplicite(d);
  const c = d.conclusion;
  return (
    <div className="carte" style={{ borderLeft: `4px solid ${
        d.avis === "Favorable" ? "var(--good)"
        : d.avis === "Défavorable" || d.avis === "S'abstenir"
          ? "var(--critical)" : "var(--grid)"}` }}>
      <div className="rangee" style={{ alignItems: "center", cursor: "pointer" }}
           onClick={() => setOuvert(!ouvert)}>
        <span className="note" style={{ minWidth: 28 }}>#{rang}</span>
        <div style={{ minWidth: 150 }}>
          <strong>{d.nom ?? d.symbole}</strong>
          <div className="note">{d.symbole}{d.classe ? ` · ${d.classe}` : ""} ·
            cours <PrixVivant symbole={d.symbole} secours={d.prix} /></div>
        </div>
        <span className="badge" style={{ color: COULEUR_AVIS[d.avis] ?? "inherit",
                                         fontWeight: 700, fontSize: 14 }}>
          {d.avis}</span>
        <div>
          <BarreNote note={d.note_globale} />
          <div className="note">note {d.note_globale > 0 ? "+" : ""}
            {d.note_globale} · accord {d["concordance_%"]} %</div>
        </div>
        {c && (
          <div className="tuile" style={{ minWidth: 116 }}>
            <div className="libelle">P(hausse) · {c.periode_seances} séances</div>
            {/* La probabilité CALIBRÉE domine : c'est celle que l'historique
                autorise. La brute reste visible, barrée, parce que l'écart
                entre les deux est en soi une information. */}
            <div className="valeur" style={{ fontSize: 18 }}>
              {c.proba_calibree ? `${c.proba_calibree["proba_%"]} %`
                                : `${c["proba_hausse_combinee_%"]} %`}</div>
            {c.proba_calibree && (
              <div className="note" title="probabilité affichée avant calibration">
                <s>{c["proba_hausse_combinee_%"]} %</s> avant calibration</div>
            )}
          </div>
        )}
        {c && (
          <div className="tuile" style={{ minWidth: 128 }}>
            <div className="libelle">potentiel / risque extrême</div>
            <div style={{ fontSize: 14 }}>
              <span className="delta positif">▲ {c["scenario_porteur_%"]} %</span>
              {" / "}
              <span className="delta negatif">{c["var_95_%"]} %</span>
            </div>
          </div>
        )}
        {d.brokers?.tendance && (
          <div className="tuile" style={{ minWidth: 112 }}
               title="ADX/DMI, Supertrend, Ichimoku, Fibonacci, Stochastique, OBV">
            <div className="libelle">outils brokers</div>
            <div style={{ fontSize: 13 }}>{d.brokers.haussiers} haussiers ·{" "}
              {d.brokers.baissiers} baissiers <span className="note">
                ({d.brokers.tendance})</span></div>
          </div>
        )}
        <span className="note" style={{ marginLeft: "auto" }}>
          {ouvert ? "▲ replier" : "▼ pourquoi ?"}</span>
      </div>

      <p style={{ margin: "8px 0 0", fontWeight: 600, color: action.couleur }}>
        {action.texte}</p>
      <PositionVive symbole={d.symbole} plan={d.plan} />
      <PlanCourt court={court} horizon={horizonCourt} />
      {d.brokers?.avertissement && (
        <p className="note" style={{ margin: "4px 0 0" }}>
          {d.brokers.avertissement}</p>
      )}
      {d.vetos?.length > 0 && d.vetos.map((v, i) => (
        <p key={i} className="erreur" style={{ margin: "4px 0 0" }}>{v}</p>
      ))}
      {ouvert && (
        <div style={{ marginTop: 10 }}>
          {d.composantes?.map((c) => (
            <p key={c.nom} className="note" style={{ margin: "4px 0" }}>
              <strong style={{ display: "inline-block", minWidth: 110 }}>
                {c.nom}</strong>{" "}
              <span style={{ display: "inline-block", minWidth: 52,
                             color: c.note > 5 ? "var(--good)"
                               : c.note < -5 ? "var(--critical)" : "inherit" }}>
                {c.note > 0 ? "+" : ""}{c.note}</span>{" "}
              {c.raisons.join(" · ")}
            </p>
          ))}
          {d.contexte_marche && Object.keys(d.contexte_marche).length > 0 && (
            <div style={{ marginTop: 8 }}>
              <p style={{ margin: "0 0 2px" }}><strong>Briques candidates</strong>{" "}
                <span className="note">— mesurées à chaque verdict, sans aucun
                poids tant qu'elles n'ont rien prouvé</span></p>
              {Object.entries(d.contexte_marche).map(([nom, m]) => (
                <p key={nom} className="note" style={{ margin: "2px 0" }}>
                  <strong style={{ display: "inline-block", minWidth: 150 }}>
                    {nom.replace(/_/g, " ")}</strong>
                  <span style={{ display: "inline-block", minWidth: 52,
                                 color: m.note > 5 ? "var(--good)"
                                   : m.note < -5 ? "var(--critical)" : "inherit" }}>
                    {m.note > 0 ? "+" : ""}{Math.round(m.note)}</span>{" "}
                  {m.raison}
                </p>
              ))}
            </div>
          )}
          {d.conclusion?.texte && (
            <p style={{ marginTop: 8 }}><strong>Conclusion</strong> —{" "}
              {d.conclusion.texte}</p>
          )}
          {d.plan && (
            <p style={{ marginTop: 8 }}>Plan : entrée {nb(d.plan.entree)} ·
              stop {nb(d.plan.stop)} · objectif {nb(d.plan.objectif)} ·
              ratio {d.plan.ratio_gain_risque} ·
              P(stop) {d.plan["proba_toucher_stop_%"]} % ·
              P(objectif) {d.plan["proba_toucher_objectif_%"]} % ·
              espérance {d.plan["esperance_%"] > 0 ? "+" : ""}{d.plan["esperance_%"]} %</p>
          )}
          <button className="action secondaire" style={{ marginTop: 8 }}
                  onClick={(e) => { e.stopPropagation(); onTitre(d.symbole); }}>
            Ouvrir la fiche complète
          </button>
        </div>
      )}
    </div>
  );
}

const TRIS = {
  "avis (favorable → défavorable)":
    (a, b) => (b.note_globale ?? -999) - (a.note_globale ?? -999),
  "probabilité de hausse":
    (a, b) => (b.conclusion?.["proba_hausse_combinee_%"] ?? -1)
      - (a.conclusion?.["proba_hausse_combinee_%"] ?? -1),
  "potentiel de gain":
    (a, b) => (b.conclusion?.["scenario_porteur_%"] ?? -999)
      - (a.conclusion?.["scenario_porteur_%"] ?? -999),
  "risque le plus faible":
    (a, b) => (b.conclusion?.["var_95_%"] ?? -999)
      - (a.conclusion?.["var_95_%"] ?? -999),
};

function PageDecisions({ onTitre }) {
  const { donnees, erreur } = useDonnees(api.getVerdicts);
  const app = useDonnees(api.getApprentissage);
  const [tri, setTri] = useState(Object.keys(TRIS)[0]);
  const [classe, setClasse] = useState("Tous");
  if (!donnees) return <Chargement erreur={erreur} />;
  const tous = donnees.dossiers ?? [];
  const courts = Object.fromEntries(
    (donnees.dossiers_court ?? []).map((c) => [c.symbole, c]));
  const classes = ["Tous",
                   ...new Set(tous.map((d) => d.classe).filter(Boolean))];
  const dossiers = tous
    .filter((d) => classe === "Tous" || d.classe === classe)
    .sort(TRIS[tri]);
  const bilan = donnees.bilan;
  const comp = bilan?.competence;
  return (
    <>
      {comp?.sens && (
        <div className="carte" style={{ borderLeft: `4px solid ${
            comp.sens === "négatif" ? "var(--critical)"
            : comp.sens === "positif" ? "var(--good)" : "var(--baseline)"}` }}>
          <h3 style={{ marginTop: 0, color: comp.sens === "négatif"
              ? "var(--critical)" : "inherit" }}>
            Ce que vaut cet outil, mesuré sur ses propres verdicts</h3>
          <p>{comp.lecture}</p>
          <p className="note">Mesure : corrélation de rang entre la note
            attribuée et le rendement advenu, calculée <em>date par date</em>{" "}
            puis moyennée sur {comp.n_dates} dates —{" "}
            <strong>{comp.ic_transversal_moyen}</strong> (t = {comp.t}).
            L'erreur-type tient compte du recouvrement des fenêtres : à{" "}
            {comp.horizon_seances} séances d'horizon, des verdicts voisins
            décrivent presque le même bout de marché, d'où seulement{" "}
            ~{comp.episodes_independants} épisodes réellement indépendants.
            {" "}{comp.methode}</p>
          {bilan.competence_par_horizon?.length > 1 && (
            <>
              <h4 style={{ marginBottom: 4 }}>Les mêmes verdicts, mesurés à
                plusieurs horizons</h4>
              <p className="note">Un horizon court n'est pas « meilleur » : il
                donne simplement plus d'épisodes de marché indépendants pour
                une même durée d'observation, donc une réponse plus vite. La
                colonne « IC détectable » dit à partir de quelle compétence
                réelle on saurait la distinguer du hasard avec le recul
                actuel.</p>
              <Table lignes={bilan.competence_par_horizon
                .filter((c) => c.ic_transversal_moyen != null)
                .map((c) => ({
                  "horizon (séances)": c.horizon,
                  "IC transversal": c.ic_transversal_moyen,
                  t: c.t,
                  "épisodes indépendants": c.episodes_independants,
                  "IC détectable": c.ic_detectable,
                  conclusion: c.sens,
                }))} />
            </>
          )}
        </div>
      )}
      <div className="carte">
        <div className="rangee" style={{ alignItems: "end" }}>
          <label className="champ">Classer par
            <select value={tri} onChange={(e) => setTri(e.target.value)}>
              {Object.keys(TRIS).map((t) => <option key={t}>{t}</option>)}
            </select>
          </label>
          <label className="champ">Classe d'actif
            <select value={classe} onChange={(e) => setClasse(e.target.value)}>
              {classes.map((cl) => <option key={cl}>{cl}</option>)}
            </select>
          </label>
          <span className="note">{dossiers.length} actif(s) — la liste se
            réordonne du plus favorable au plus défavorable selon le critère
            choisi. Chaque carte dit explicitement quoi faire, et « pourquoi ? »
            ouvre le détail. Le classement suit l'horizon officiel de{" "}
            {donnees.horizon_officiel ?? 20} séances ; la seconde ligne donne le
            pari indépendant à {donnees.horizon_court ?? 5} séances, dont la
            prévision, le stop et l'objectif sont recalculés pour cette
            fenêtre — les deux peuvent diverger, et c'est normal.</span>
        </div>
      </div>
      <div className="carte">
        <h3>Bilan des verdicts passés</h3>
        {bilan?.verdicts_evalues > 0 ? (
          <>
            <p className="note">{bilan.verdicts_evalues} verdict(s) arrivé(s) à
              l'horizon depuis le {bilan.premiere_date}.</p>
            <Table lignes={bilan.par_avis} />
            <p className="note">{bilan.lecture}</p>
            {bilan.fiabilite_probas?.length > 0 && (
              <>
                <h4 style={{ marginBottom: 4 }}>Les probabilités annoncées
                  étaient-elles tenues ?</h4>
                <p className="note">Chaque ligne compare ce que l'outil
                  annonçait à ce qui est réellement arrivé. Un écart proche de
                  zéro signifie une probabilité honnête.</p>
                <Table lignes={bilan.fiabilite_probas} />
                {bilan.calibration?.paliers && (
                  <p className="note">
                    Score de Brier (erreur moyenne, plus bas = mieux) :{" "}
                    <strong>{bilan.calibration.brier_avant}</strong> avant
                    calibration — <em>pire qu'un tirage à pile ou face
                    (0,25)</em> — contre{" "}
                    <strong>{bilan.calibration.brier_calibre}</strong> après.
                    {" "}{bilan.calibration.statut} Les cartes ci-dessous
                    affichent donc la probabilité calibrée, calculée sur{" "}
                    {bilan.calibration.n} verdicts (taux de base observé :{" "}
                    {bilan.calibration["taux_de_base_%"]} %).
                  </p>
                )}
              </>
            )}
          </>
        ) : (
          <p className="note">{bilan?.message ?? "Bilan indisponible."} Chaque
            verdict est journalisé automatiquement : ce tableau mesurera le taux
            de réussite réel de l'outil, avis par avis.</p>
        )}
        {app.donnees && (
          <>
            <h4 style={{ marginBottom: 4 }}>Pondérations du verdict —
              apprises du bilan réel</h4>
            <p className="note">{app.donnees.statut}</p>
            {app.donnees.poids && (
              <Table lignes={Object.keys(app.donnees.poids_base).map((nom) => ({
                composante: nom,
                "poids de base": app.donnees.poids_base[nom],
                "poids actuel": app.donnees.poids[nom],
                "IC mesuré": app.donnees.ic_par_composante?.[nom]?.ic ?? "—",
                "prouvé à 95 %": app.donnees.ic_par_composante?.[nom]
                  ?.prouve === undefined ? "—"
                  : (app.donnees.ic_par_composante[nom].prouve ? "oui" : "non"),
                n: app.donnees.ic_par_composante?.[nom]?.n ?? "—",
              }))} />
            )}
            {!app.donnees.poids && app.donnees.ic_par_composante && (
              <Table lignes={Object.keys(app.donnees.poids_base).map((nom) => ({
                composante: nom,
                "poids appliqué": app.donnees.poids_base[nom],
                "IC mesuré": app.donnees.ic_par_composante?.[nom]?.ic ?? "—",
                "borne basse 95 %": app.donnees.ic_par_composante?.[nom]
                  ?.ic_borne_basse_95 ?? "—",
                n: app.donnees.ic_par_composante?.[nom]?.n ?? "—",
              }))} />
            )}
            {app.donnees.candidats &&
             Object.keys(app.donnees.candidats).length > 0 && (
              <>
                <h4 style={{ marginBottom: 4 }}>Briques candidates —
                  journalisées, pas encore pondérées</h4>
                <p className="note">Elles sont mesurées comme les autres. Une
                  brique gagne sa place dans le verdict en démontrant sa
                  valeur, elle ne la reçoit pas d'office.</p>
                <Table lignes={Object.entries(app.donnees.candidats).map(
                  ([nom, c]) => ({
                    candidat: nom, "IC mesuré": c.ic ?? "—",
                    "borne basse 95 %": c.ic_borne_basse_95 ?? "—",
                    n: c.n, statut: c.note ?? (c.prouve ? "prouvé" : "non prouvé"),
                  }))} />
              </>
            )}
            <p className="note">{app.donnees.methode}</p>
          </>
        )}
      </div>
      {dossiers.map((d, i) => (
        <Verdict key={d.symbole} d={d} rang={i + 1} onTitre={onTitre}
                 court={courts[d.symbole]}
                 horizonCourt={donnees.horizon_court} />
      ))}
    </>
  );
}

// ---------------------------------------------------------------- Concours
/**
 * Les positions ouvertes d'un robot, suivies en direct.
 *
 * Lecture seule : on observe l'évolution sans jamais intervenir. Pour chaque
 * position, le P&L latent est exprimé en dollars ET en pourcentage de la
 * mise — c'est la seconde mesure qui dit le risque réellement pris, le
 * levier amplifiant l'écart. La barre situe le cours entre le stop et
 * l'objectif : d'un coup d'œil, on voit de quel côté penche le pari.
 */
function PositionsVivantes({ positions }) {
  const { cours } = useCours();
  if (!positions?.length) {
    return <p className="note">Aucune position ouverte actuellement.</p>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ml-table">
        <thead><tr>
          {["actif", "mise", "entrée", "cours", "P&L", "stop", "objectif",
            "où en est le pari"].map((c) => <th key={c}>{c}</th>)}
        </tr></thead>
        <tbody>
          {positions.map((p, i) => {
            const c = cours[p.symbole];
            const prix = c?.prix;
            const sens = p.sens === "long" ? 1 : -1;
            const marge = Number(p.marge) || 1;
            const qte = (marge * (Number(p.levier) || 1)) / Number(p.prix_entree);
            const pnl = prix == null ? null
              : (prix - Number(p.prix_entree)) * qte * sens;
            const surMise = pnl == null ? null : (pnl / marge) * 100;
            const stop = Number(p.stop), obj = Number(p.objectif);
            const progression = (prix == null || !stop || !obj || obj === stop)
              ? null
              : Math.max(0, Math.min(100, ((prix - stop) / (obj - stop)) * 100));
            return (
              <tr key={i}>
                <td><strong>{p.symbole}</strong>
                  <div className="note">{p.sens} ×{p.levier}</div></td>
                <td>{nb(marge)} $</td>
                <td>{nb(p.prix_entree)}</td>
                <td>{prix == null ? "—" : nb(prix)}</td>
                <td className={"delta " + ((pnl ?? 0) >= 0 ? "positif" : "negatif")}>
                  {pnl == null ? "—"
                    : `${pnl >= 0 ? "+" : ""}${nb(pnl, 2)} $`}
                  {surMise != null &&
                    <div className="note">{surMise >= 0 ? "+" : ""}
                      {surMise.toFixed(1)} % de la mise</div>}
                </td>
                <td className="note">{nb(p.stop)}
                  {prix != null && stop ?
                    <div>{pct(Math.abs(prix / stop - 1) * 100)} de marge</div> : null}</td>
                <td className="note">{nb(p.objectif)}
                  {prix != null && obj ?
                    <div>{pct(Math.abs(obj / prix - 1) * 100)} à parcourir</div> : null}</td>
                <td style={{ minWidth: 130 }}>
                  {progression == null ? "—" : (
                    <>
                      <div style={{ height: 8, borderRadius: 4,
                                    background: "var(--grid)", overflow: "hidden" }}>
                        <div style={{ width: `${progression}%`, height: "100%",
                          background: progression >= 50 ? "var(--good)"
                            : progression >= 25 ? "var(--series-2)"
                            : "var(--critical)" }} />
                      </div>
                      <div className="note">{progression.toFixed(0)} % du chemin
                        stop → objectif</div>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="note">Suivi en lecture seule, rafraîchi avec les cours —
        aucune intervention sur les positions du robot. Le pourcentage « de la
        mise » est la vraie mesure du risque : le levier amplifie l'écart du
        cours.</p>
    </div>
  );
}

function RapportSeance() {
  const { donnees: r } = useDonnees(api.getRapportSeance);
  if (!r?.robots?.length) return null;
  return (
    <div className="carte">
      <h3>Rapport de séance — ce qui était prévu contre ce qui est advenu</h3>
      <p className="note">{r.lecture}</p>
      {r.robots.map((b) => (
        <div key={b.nom} style={{ marginTop: 12 }}>
          <h4 style={{ marginBottom: 4 }}>{b.nom}</h4>
          {b.n === 0 ? <p className="note">{b.message}</p> : (
            <>
              <div className="rangee" style={{ marginBottom: 6 }}>
                <Tuile libelle="Trades rejoués" valeur={b.n} />
                <Tuile libelle="Creux médian traversé"
                       valeur={pct(b["mae_mediane_%"])}
                       note="MAE — jusqu'où ça descend avant de se terminer" />
                <Tuile libelle="Sommet médian atteint"
                       valeur={pct(b["mfe_mediane_%"])}
                       note="MFE — le meilleur point touché en cours de route" />
                <Tuile libelle="Stops qui coupaient du bruit"
                       valeur={`${b.stops_qui_etaient_du_bruit}/${b.stops_touches}`}
                       note="cours revenu au-dessus de l'entrée ensuite" />
              </div>
              {b.constats?.map((c, i) => (
                <p key={i} style={{ margin: "3px 0" }}>• {c}</p>
              ))}
              {!b.echantillon_suffisant && (
                <p className="note">Échantillon encore trop mince : ces chiffres
                  se liront vraiment après quelques dizaines de trades.</p>
              )}
            </>
          )}
        </div>
      ))}
      <p className="note">{r.methode ?? ""}</p>
    </div>
  );
}

function PageConcours() {
  const { donnees, erreur } = useDonnees(api.getConcours);
  if (!donnees) return <Chargement erreur={erreur} />;
  const robots = donnees.comptes?.filter((c) => c.est_robot) ?? [];
  return (
    <>
      <div className="carte">
        <h3>Concours de trading virtuel</h3>
        <p className="note">Chaque compte part avec{" "}
          {nb(donnees.capital_depart)} $ virtuels. Les robots appliquent les
          verdicts de l'outil — les battre, c'est battre la machine.
          Créer votre compte : <a href="trading/">espace de trading</a>.
          Mis à jour le {donnees.date}.</p>
        {donnees.experience && (
          <p className="note" style={{ borderLeft: "3px solid var(--series-1)",
                                       paddingLeft: 10 }}>
            <strong>Expérience en cours</strong> — {donnees.experience}</p>
        )}
        <Table lignes={donnees.comptes?.map((c, i) => ({
          "#": i + 1,
          compte: "" + c.nom,
          horizon: c.horizon ? `${c.horizon} séances` : "—",
          "équité $": c.equite,
          "perf %": c["perf_%"],
          positions: c.n_positions,
          "trades clos": c.n_trades,
        }))} />
      </div>
      {robots.map((robot) => (
        <div className="carte" key={robot.nom}>
          <h3>{robot.nom} — horizon {robot.horizon ?? "?"} séances,
            en transparence totale</h3>
          <p className="note">{donnees.regles_robot}</p>
          <h4>Positions ouvertes — suivies en direct</h4>
          <PositionsVivantes positions={robot.positions} />
          {robot.bilan_trades?.n > 0 && (
            <>
              <h4>Ce que valent ses trades</h4>
              <div className="rangee" style={{ marginBottom: 8 }}>
                <Tuile libelle="Trades clos" valeur={robot.bilan_trades.n} />
                <Tuile libelle="P&L réalisé"
                       valeur={`${robot.bilan_trades.pnl_total > 0 ? "+" : ""}${
                         robot.bilan_trades.pnl_total} $`} />
                <Tuile libelle="Taux de réussite"
                       valeur={`${robot.bilan_trades["taux_reussite_%"]} %`}
                       note={`${robot.bilan_trades.gagnants} gagnant(s) · ${
                         robot.bilan_trades.perdants} perdant(s)`} />
                <Tuile libelle="Gain moyen"
                       valeur={robot.bilan_trades.gain_moyen != null
                         ? `${robot.bilan_trades.gain_moyen} $` : "—"} />
                <Tuile libelle="Perte moyenne"
                       valeur={robot.bilan_trades.perte_moyenne != null
                         ? `${robot.bilan_trades.perte_moyenne} $` : "—"} />
              </div>
              {robot.bilan_trades.par_motif && (
                <p className="note">Sorties par motif —{" "}
                  {Object.entries(robot.bilan_trades.par_motif)
                    .map(([m, v]) => `${m} : ${v.n} pour ${v.pnl > 0 ? "+" : ""}${v.pnl} $`)
                    .join(" · ")}. C'est ici qu'on voit ce qui coûte : un motif
                  qui revient souvent et toujours en perte désigne une règle à
                  corriger.</p>
              )}
              <h4>Chaque trade en détail</h4>
              <Table lignes={robot.trades} max={15} />
            </>
          )}
          {robot.journal?.length > 0 && (
            <>
              <h4>Journal des décisions</h4>
              {robot.journal.map((l, i) => (
                <p key={i} className="note" style={{ margin: "3px 0" }}>{l}</p>
              ))}
            </>
          )}
        </div>
      ))}
      <RapportSeance />
      <p className="note">{donnees.avertissement}</p>
    </>
  );
}

// ---------------------------------------------------------------- Alertes
function BilanAlertes() {
  // `donnees` EST le contenu du fichier : `useDonnees` nomme ainsi son champ.
  // Une première version cherchait `donnees.donnees`, qui n'existe pas — le
  // bloc ne s'affichait donc jamais, sans la moindre erreur visible.
  const { donnees: b } = useDonnees(api.getBilanAlertes);
  if (!b) return null;
  return (
    <div className="carte">
      <h3>Ce que valent ces alertes</h3>
      {b.par_regle?.length ? (
        <>
          <p className="note">{b.evaluees} alerte(s) jugée(s) sur {b.n}{" "}
            consignée(s) — mesure faite {b.horizon_seances} séances après
            l'envoi. Les règles de contexte (agenda, sentiment, VIX)
            n'annoncent pas de sens : elles sont consignées mais non notées.</p>
          <Table lignes={b.par_regle} />
          <p className="note">{b.lecture}</p>
        </>
      ) : (
        <p className="note">{b.message}</p>
      )}
    </div>
  );
}

function PageAlertes() {
  const { donnees, erreur } = useDonnees(api.getAlertes);
  if (erreur) {
    return <div className="carte"><p className="note">Aucune alerte publiée
      pour l'instant — le fil se remplit au premier passage horaire du
      scanner.</p></div>;
  }
  if (!donnees) return <Chargement />;
  return (
    <>
      <div className="carte">
        <h3>Fil des alertes</h3>
        <p className="note">Le scanner est déclenché chaque heure ; les
          exécutions planifiées gratuites de GitHub étant au mieux-effort,
          seule une partie aboutit. Chaque déclenchement obtenu lance donc une
          <strong> veille de 3 heures qui rebalaie toutes les 10 minutes</strong>,
          sur des cours réellement rafraîchis — un déclenchement couvre un
          tiers de journée au lieu d'un instantané. Le seul horodatage qui fasse foi est
          celui du dernier passage, ci-dessous. Ce fil est en avance sur le
          reste du site, régénéré une fois par jour (l'instantané daté en haut
          de page). Dernier
          passage du scanner : <strong>{donnees.dernier_passage ?? "?"}</strong>
          {" "}({donnees.fuseau ?? "UTC"}). Les mêmes alertes arrivent en
          notification ntfy.</p>
      </div>
      <BilanAlertes />
      {(donnees.alertes ?? []).length === 0 && (
        <div className="carte"><p className="note">Rien à signaler pour le
          moment — le silence est l'état normal du système.</p></div>
      )}
      {(donnees.alertes ?? []).map((a, i) => (
        <div key={i} className="carte" style={{
          padding: "10px 16px",
          borderLeft: a.urgent ? "4px solid var(--critical)"
                               : "4px solid var(--grid)" }}>
          <div className="note">{a.quand}
            {a.urgent && <strong style={{ color: "var(--critical)" }}>
              {" "}· URGENT</strong>}</div>
          <div style={{ whiteSpace: "pre-wrap" }}>{a.texte}</div>
        </div>
      ))}
    </>
  );
}

// ---------------------------------------------------------------- App
// ---------------------------------------------------------------- DevApp
//
// L'espace consacré à l'application elle-même : d'où viennent les chiffres,
// quand ils ont été produits, ce qui a échoué, et ce qui reste à faire.
//
// Le principe est le même que pour le reste du site : ne rien AFFIRMER qui ne
// soit constaté. Tout ce qui suit est lu dans le dépôt, dans les fichiers
// réellement publiés et dans la configuration — jamais recopié à la main dans
// une page qui deviendrait fausse à la première modification oubliée.

function Jauge({ fait, total, libelle }) {
  const pct = total ? Math.round((fait / total) * 100) : 0;
  return (
    <div style={{ minWidth: 200, flex: 1 }}>
      <div className="libelle" style={{ display: "flex",
        justifyContent: "space-between" }}>
        <span>{libelle}</span><span>{fait} / {total}</span>
      </div>
      <div style={{ height: 5, background: "var(--grid)", borderRadius: 3,
        marginTop: 5, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%",
          background: pct === 100 ? "var(--good)" : "var(--series-1)" }} />
      </div>
    </div>
  );
}

const CRON_LISIBLE = (c) => {
  const [min, heure] = c.split(" ");
  if (heure === "*") return `à ${min.padStart(2, "0")} de chaque heure (UTC)`;
  return `à ${heure.padStart(2, "0")} h ${min.padStart(2, "0")} UTC`;
};

function PageDevApp() {
  const { donnees: d, erreur } = useDonnees(api.getDevApp);
  const [tout, setTout] = useState(false);
  if (!d) return <Chargement erreur={erreur} />;

  const { depot, tests, perimetre: p, sources, automatisation, sante } = d;
  const route = d.feuille_de_route ?? [];
  const sections = [...new Set(route.map((r) => r.section))];
  const livres = route.filter((r) => r.livre).length;

  return (
    <>
      <div className="carte">
        <h3>Ce que fait cette page</h3>
        <p className="note">Elle décrit l'application, pas les marchés. Tout ce
          qui s'y trouve est LU — dans le dépôt, dans les fichiers réellement
          publiés, dans les tâches planifiées — jamais recopié à la main. Un
          tableau de bord recopié devient faux à la première modification qu'on
          oublie d'y reporter.</p>
        <div className="rangee">
          <Tuile libelle="Version" valeur={depot.version}
                 note={`branche ${depot.branche}`} />
          <Tuile libelle="Modifications"
                 valeur={depot.commits_total ?? "historique tronqué"}
                 note={depot.historique_complet === false
                   ? "le runner a cloné sans historique"
                   : "depuis le début du projet"} />
          <Tuile libelle="Fonctions de test" valeur={tests.fonctions}
                 note={`${tests.fichiers} fichiers`} />
          <Tuile libelle="Lignes de code"
                 valeur={(depot.lignes_python + depot.lignes_front
                          + depot.lignes_php).toLocaleString("fr-FR")}
                 note={`dont ${depot.lignes_tests.toLocaleString("fr-FR")} de tests`} />
        </div>
        <p className="note">Python {depot.lignes_python.toLocaleString("fr-FR")} ·
          front {depot.lignes_front.toLocaleString("fr-FR")} ·
          PHP {depot.lignes_php.toLocaleString("fr-FR")}. Le compte de tests
          porte sur les FONCTIONS de test : un test paramétré en produit
          plusieurs à l'exécution, et deviner ce nombre reviendrait à gonfler
          le chiffre.</p>
      </div>

      <div className="carte">
        <h3>Dernière génération</h3>
        <div className="rangee">
          <Tuile libelle="Produite le" valeur={sante.genere_le ?? "—"} />
          <Tuile libelle="Blocs" valeur={sante.blocs_produits} />
          <Tuile libelle="Fiches" valeur={sante.fiches_produites} />
          <Tuile libelle="Échecs" valeur={sante.erreurs.length}
                 note={sante.erreurs.length ? "voir ci-dessous" : "aucun"} />
        </div>
        {sante.erreurs.length > 0 ? (
          <>
            <p className="note" style={{ marginTop: 8 }}>Un bloc en échec ne
              bloque pas la publication : le reste du site part quand même, et
              l'erreur est consignée plutôt qu'avalée.</p>
            {sante.erreurs.map((e) => (
              <p key={e.quoi} className="erreur" style={{ margin: "4px 0" }}>
                <strong>{e.quoi}</strong> — {e.message}</p>
            ))}
          </>
        ) : (
          <p className="note">Aucun bloc en échec à la dernière génération.</p>
        )}
      </div>

      <div className="carte">
        <h3>Couverture des données</h3>
        <div className="rangee" style={{ alignItems: "flex-start" }}>
          <Jauge libelle="Fiches détaillées" fait={p.fiches_publiees}
                 total={p.fiches_attendues} />
          <Jauge libelle="Séries de bougies" fait={p.series_publiees}
                 total={p.fiches_attendues} />
          <Jauge libelle="Socle horaire (H1, H4)"
                 fait={p.series_par_socle.horaire} total={p.fiches_attendues} />
          <Jauge libelle="Socle 5 min (M5, M15)"
                 fait={p.series_par_socle.intraday} total={p.fiches_attendues} />
        </div>
        <p className="note" style={{ marginTop: 10 }}>
          {p.actifs_suivis} actifs suivis au total, dont {p.fiches_attendues} avec
          une fiche détaillée. Poids des données publiées :{" "}
          {p.poids_donnees_ko.toLocaleString("fr-FR")} Ko.
          {p.series_manquantes.length > 0 && (
            <> Séries absentes : {p.series_manquantes.join(", ")} — leur
              graphique reste vide plutôt que d'afficher celui d'un autre
              actif.</>
          )}
        </p>
        <div className="rangee" style={{ marginTop: 6 }}>
          {Object.entries(p.univers).map(([nom, n]) => (
            <span key={nom} className="badge">{nom} · {n}</span>
          ))}
        </div>
      </div>

      <div className="carte">
        <h3>Sources de données</h3>
        <Table lignes={sources.fournisseurs.map((f) => ({
          Fournisseur: f.nom, Usage: f.usage,
          "Clé requise": f.cle ? "oui" : "non",
        }))} />
        <h3 style={{ marginTop: 14 }}>Accès configurés</h3>
        <p className="note">Présence seulement : cette page ne montre jamais
          une clé, ni même un fragment. La question posée est « la clé est-elle
          présente dans l'environnement qui PUBLIE » — c'est-à-dire GitHub
          Actions, pas ce navigateur.</p>
        {sources.cles.map((c) => (
          <p key={c.nom} style={{ margin: "4px 0" }}>
            <span className="badge" style={{ marginRight: 8,
              color: c.configuree ? "var(--good)" : "var(--muted)" }}>
              {c.configuree ? "configurée" : "absente"}</span>
            <span className="note">{c.role}</span>
          </p>
        ))}
      </div>

      <div className="carte">
        <h3>Automatisation</h3>
        {automatisation.taches.map((t) => (
          <p key={t.fichier} style={{ margin: "5px 0" }}>
            <strong style={{ display: "inline-block", minWidth: 210 }}>
              {t.nom}</strong>
            <span className="note">
              {t.crons_utc.length
                ? t.crons_utc.map(CRON_LISIBLE).join(" · ")
                : "déclenchement manuel uniquement"}
              {t.manuel && t.crons_utc.length ? " · déclenchable à la main" : ""}
            </span>
          </p>
        ))}
        <p className="note" style={{ marginTop: 8 }}>
          {automatisation.note_planification}</p>
      </div>

      <div className="carte">
        <h3>Feuille de route — {livres} livrés sur {route.length}</h3>
        <p className="note">Une case n'est cochée que lorsque la chose est
          livrée ET vérifiée en ligne. La liste vit dans le dépôt
          (docs/FEUILLE_DE_ROUTE.md) : la page ne peut donc pas prétendre à un
          état que le code ne porte pas.</p>
        {sections.map((s) => (
          <div key={s} style={{ marginTop: 10 }}>
            <div className="libelle">{s}</div>
            {route.filter((r) => r.section === s).map((r, i) => (
              <p key={i} style={{ margin: "3px 0",
                color: r.livre ? "inherit" : "var(--muted)" }}>
                <span style={{ display: "inline-block", width: 74,
                  fontSize: 11, color: r.livre ? "var(--good)" : "var(--muted)" }}>
                  {r.livre ? "livré" : "à faire"}</span>
                {r.texte}
              </p>
            ))}
          </div>
        ))}
      </div>

      <div className="carte">
        <h3>Journal des modifications</h3>
        {(tout ? depot.derniers_commits : depot.derniers_commits.slice(0, 8))
          .map((c) => (
          <p key={c.abrege} style={{ margin: "3px 0" }}>
            <span className="note" style={{ display: "inline-block",
              width: 92, fontVariantNumeric: "tabular-nums" }}>{c.date}</span>
            {c.sujet}
          </p>
        ))}
        {depot.derniers_commits.length > 8 && (
          <button className="action secondaire" style={{ marginTop: 8 }}
                  onClick={() => setTout((v) => !v)}>
            {tout ? "Réduire" : `Voir les ${depot.derniers_commits.length} dernières`}
          </button>
        )}
      </div>
    </>
  );
}

const PAGES = {
  "Décisions": PageDecisions,
  "Alertes": PageAlertes,
  "Concours": PageConcours,
  "Marchés": PageMarches,
  "Titre": PageTitre,
  "Macro & agenda": PageMacro,
  "Fondamentaux": PageFondamentaux,
  "Corrélations": PageCorrelations,
  "Portefeuille": PagePortefeuille,
  "DevApp": PageDevApp,
};

function BandeauFlux() {
  const { cours, actualise } = useCours();
  if (!actualise) return null;
  const total = Object.keys(cours).length;
  const direct = Object.values(cours).filter((c) => c.frais).length;
  return (
    <p className="note">Cours actualisés à{" "}
      {actualise.toLocaleTimeString("fr-FR")} — {direct}/{total} en direct.
      Crypto en temps réel, forex à la minute, actions et matières au différé
      de ~15 min des sources gratuites ; l'âge de chaque cotation est affiché.
    </p>
  );
}

/** Contenu de l'application, à l'intérieur du fournisseur de cours : la barre
 *  d'état a besoin des cotations, et `useCours` n'est lisible que sous lui. */
function Application() {
  const [page, setPage] = useState("Décisions");
  const [symbole, setSymbole] = useState(null);
  const { donnees: meta, erreur } = useDonnees(api.getMeta);
  const { cours, actualise } = useCours();

  const ouvrirTitre = (s) => { setSymbole(s); setPage("Titre"); };
  const Page = PAGES[page];

  return (
    <Coquille pages={Object.keys(PAGES)} page={page} setPage={setPage}
              etat={<EtatDonnees meta={meta} cours={cours}
                                 actualise={actualise} />}>
      {erreur && <p className="erreur">Données indisponibles : {erreur}</p>}
      <BandeauFlux />
      {meta && (page === "Titre"
        ? <Page meta={meta} symbole={symbole} setSymbole={setSymbole} />
        : <Page onTitre={ouvrirTitre} />)}
    </Coquille>
  );
}

export default function App() {
  return (
    <FournisseurCours>
      <Application />
    </FournisseurCours>
  );
}
