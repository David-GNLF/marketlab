import { useEffect, useState } from "react";
import * as api from "./api";
import { GraphiqueEquite, GraphiquePrix } from "./charts";

const fmtUsd = (v) => (v == null ? "—" : Number(v).toLocaleString("fr-FR",
  { maximumFractionDigits: 2 }) + " $");

function Tuile({ libelle, valeur, delta }) {
  return (
    <div className="tuile">
      <div className="libelle">{libelle}</div>
      <div className="valeur">{valeur}</div>
      {delta != null && (
        <div className={"delta " + (delta >= 0 ? "positif" : "negatif")}>
          {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(2)} %
        </div>
      )}
    </div>
  );
}

function TableSimple({ lignes, colonnes }) {
  if (!lignes?.length) return <p className="note">Aucune donnée.</p>;
  const cols = colonnes ?? Object.keys(lignes[0]);
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ml-table">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {lignes.map((l, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{l[c] ?? "—"}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- Marchés
function PageMarches({ univers }) {
  const [choix, setChoix] = useState(["Actions US"]);
  const [lignes, setLignes] = useState(null);
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState("");

  const scanner = async () => {
    setOccupe(true); setErreur("");
    try { setLignes(await api.getScreener(choix)); }
    catch (e) { setErreur(e.message); }
    finally { setOccupe(false); }
  };

  return (
    <div className="carte">
      <div className="rangee">
        <label className="champ">Univers
          <select multiple size={Math.min(6, Object.keys(univers).length)}
                  value={choix}
                  onChange={(e) => setChoix([...e.target.selectedOptions].map((o) => o.value))}>
            {Object.keys(univers).map((u) => <option key={u}>{u}</option>)}
          </select>
        </label>
        <button className="action" onClick={scanner} disabled={occupe || !choix.length}>
          {occupe ? "Scan en cours…" : "Lancer le scan"}
        </button>
      </div>
      {erreur && <p className="erreur">{erreur}</p>}
      {lignes && <TableSimple lignes={lignes}
        colonnes={["symbole", "score", "avis", "cours", "rsi14", "perf_20j_%", "vol_ann_%", "drawdown_%"]} />}
    </div>
  );
}

// ---------------------------------------------------------------- Analyse
function PageAnalyse({ univers }) {
  const tous = Object.values(univers).flat();
  const [symbole, setSymbole] = useState("AAPL");
  const [donnees, setDonnees] = useState(null);
  const [signaux, setSignaux] = useState(null);
  const [news, setNews] = useState(null);
  const [erreur, setErreur] = useState("");

  useEffect(() => {
    let actif = true;
    setErreur(""); setDonnees(null); setSignaux(null); setNews(null);
    api.getOhlcv(symbole).then((d) => actif && setDonnees(d)).catch((e) => actif && setErreur(e.message));
    api.getSignaux(symbole).then((s) => actif && setSignaux(s)).catch(() => {});
    api.getNews(symbole).then((n) => actif && setNews(n)).catch(() => {});
    return () => { actif = false; };
  }, [symbole]);

  return (
    <>
      <div className="carte">
        <div className="rangee">
          <label className="champ">Titre
            <select value={symbole} onChange={(e) => setSymbole(e.target.value)}>
              {tous.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          {signaux && (
            <>
              <Tuile libelle="Cours" valeur={signaux.close} />
              <Tuile libelle="Score composite" valeur={signaux.score} />
              <div className="tuile">
                <div className="libelle">Avis</div>
                <span className="badge">{signaux.avis}</span>
              </div>
              <Tuile libelle="RSI 14" valeur={signaux.rsi14 ?? "—"} />
              <Tuile libelle="Perf 20 j" valeur={(signaux.ret_20d ?? "—") + " %"}
                     delta={signaux.ret_20d} />
            </>
          )}
        </div>
        {erreur && <p className="erreur">{erreur}</p>}
        {donnees && <GraphiquePrix donnees={donnees} />}
      </div>
      {news?.sentiment?.n_titres > 0 && (
        <div className="carte">
          <p><strong>Actualités : {news.sentiment.lecture}</strong>{" "}
            <span className="note">(score {news.sentiment.score_moyen},
            {" "}{news.sentiment.positifs}➕ / {news.sentiment.negatifs}➖ sur
            {" "}{news.sentiment.n_titres} titres — lexical, indicatif)</span></p>
          <TableSimple lignes={news.titres.slice(0, 8)} colonnes={["titre", "source", "sentiment"]} />
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- ML
function PageMl({ univers }) {
  const tous = Object.values(univers).flat().filter((s) => !s.endsWith("=X"));
  const [symbole, setSymbole] = useState("BTCUSDT");
  const [horizon, setHorizon] = useState(5);
  const [seuil, setSeuil] = useState(0.55);
  const [res, setRes] = useState(null);
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState("");

  const lancer = async () => {
    setOccupe(true); setErreur(""); setRes(null);
    try { setRes(await api.postMl(symbole, { horizon, threshold: seuil })); }
    catch (e) { setErreur(e.message); }
    finally { setOccupe(false); }
  };

  return (
    <div className="carte">
      <p className="note">Walk-forward strict : entraînement sur le passé
        uniquement, ré-entraîné à chaque bloc. AUC 0.50 = hasard — se méfier de
        tout résultat trop beau.</p>
      <div className="rangee">
        <label className="champ">Titre
          <select value={symbole} onChange={(e) => setSymbole(e.target.value)}>
            {tous.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label className="champ">Horizon (bougies)
          <input type="number" min={3} max={20} value={horizon}
                 onChange={(e) => setHorizon(+e.target.value)} />
        </label>
        <label className="champ">Seuil P(hausse)
          <input type="number" min={0.5} max={0.7} step={0.01} value={seuil}
                 onChange={(e) => setSeuil(+e.target.value)} />
        </label>
        <button className="action" onClick={lancer} disabled={occupe}>
          {occupe ? "Entraînement… (peut prendre 1-2 min)" : "Entraîner"}
        </button>
      </div>
      {erreur && <p className="erreur">{erreur}</p>}
      {res && (
        <>
          <div className="rangee" style={{ margin: "16px 0" }}>
            <Tuile libelle="AUC moyen" valeur={res.metrics.auc_moyen ?? "—"} />
            <Tuile libelle="Rendement stratégie"
                   valeur={res.metrics["rendement_strategie_%"] + " %"} />
            <Tuile libelle="Buy & Hold"
                   valeur={res.metrics["rendement_buyhold_%"] + " %"} />
            <Tuile libelle="Sharpe" valeur={res.metrics.sharpe_strategie} />
            <Tuile libelle="Max drawdown"
                   valeur={res.metrics["max_drawdown_%"] + " %"} />
          </div>
          <GraphiqueEquite donnees={res.equity} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Paper
function PagePaper() {
  const [etat, setEtat] = useState(null);
  const [erreur, setErreur] = useState("");
  const [symbole, setSymbole] = useState("");
  const [montant, setMontant] = useState(1000);
  const [journal, setJournal] = useState(null);

  const recharger = () => {
    setErreur("");
    api.getPaper().then(setEtat).catch((e) => { setEtat(null); setErreur(e.message); });
  };
  useEffect(recharger, []);

  const agir = async (fn) => {
    setErreur("");
    try { await fn(); recharger(); } catch (e) { setErreur(e.message); }
  };

  return (
    <div className="carte">
      <p className="note">Portefeuille virtuel (USD) — dernier cours connu, sans
        spread ni slippage : performances papier optimistes par construction.</p>
      {erreur && <p className="erreur">{erreur}</p>}
      {!etat ? (
        <button className="action"
                onClick={() => agir(() => api.postPaper("init", { capital: 10000 }))}>
          Créer le portefeuille (10 000 $)
        </button>
      ) : (
        <>
          <div className="rangee" style={{ marginBottom: 16 }}>
            <Tuile libelle="Valeur totale" valeur={fmtUsd(etat.valeur_totale_usd)}
                   delta={etat["perf_totale_%"]} />
            <Tuile libelle="Cash" valeur={fmtUsd(etat.cash_usd)} />
            <Tuile libelle="Positions" valeur={fmtUsd(etat.valeur_positions_usd)} />
            <Tuile libelle="Transactions" valeur={etat.nb_transactions} />
          </div>
          <TableSimple lignes={etat.positions}
            colonnes={["symbole", "qty", "prix_moyen", "cours", "valeur_usd", "pnl_usd", "pnl_%"]} />
          <div className="rangee" style={{ marginTop: 16 }}>
            <label className="champ">Symbole
              <input type="text" value={symbole} placeholder="AAPL, BTCUSDT…"
                     onChange={(e) => setSymbole(e.target.value)} />
            </label>
            <label className="champ">Montant (USD)
              <input type="number" min={50} value={montant}
                     onChange={(e) => setMontant(+e.target.value)} />
            </label>
            <button className="action" disabled={!symbole.trim()}
                    onClick={() => agir(() => api.postPaper("acheter",
                      { symbole: symbole.trim(), montant }))}>
              Acheter (papier)
            </button>
            <button className="action secondaire" disabled={!symbole.trim()}
                    onClick={() => agir(() => api.postPaper("vendre",
                      { symbole: symbole.trim() }))}>
              Vendre tout (papier)
            </button>
            <button className="action secondaire"
                    onClick={() => agir(async () => {
                      const r = await api.postPaper("auto", { dry_run: true });
                      setJournal(r.journal);
                    })}>
              Signaux auto (simulation)
            </button>
          </div>
          {journal && (
            <ul className="note" style={{ marginTop: 12 }}>
              {journal.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Ordres
function PageOrdres() {
  const [ordres, setOrdres] = useState([]);
  const [occupe, setOccupe] = useState(false);
  const [erreur, setErreur] = useState("");
  const [ticket, setTicket] = useState("");

  const recharger = () => api.getOrdres().then(setOrdres).catch((e) => setErreur(e.message));
  useEffect(recharger, []);

  const generer = async () => {
    setOccupe(true); setErreur(""); setTicket("");
    try {
      const r = await api.postOrdres("generer", {});
      if (!r.nouvelles.length) setErreur("Aucun nouveau signal actionnable.");
      recharger();
    } catch (e) { setErreur(e.message); }
    finally { setOccupe(false); }
  };

  const decider = async (id, action) => {
    setErreur(""); setTicket("");
    try {
      const r = await api.postOrdres(`${id}/${action}`);
      if (r.ticket) setTicket(r.ticket);
      recharger();
    } catch (e) { setErreur(e.message); }
  };

  const enAttente = ordres.filter((o) => o.statut === "proposee");
  const historique = ordres.filter((o) => o.statut !== "proposee").slice(-10).reverse();

  return (
    <div className="carte">
      <p className="note">Le système propose (signaux + dimensionnement par le
        risque, stop à 2×ATR) ; <strong>toi seul valides</strong>. La validation
        exécute en PAPIER et produit un ticket — pour un compte réel, recopie le
        ticket chez ton courtier. MarketLab ne passe jamais d'ordre réel.</p>
      <button className="action" onClick={generer} disabled={occupe}>
        {occupe ? "Analyse des signaux…" : "Générer des propositions"}
      </button>
      {erreur && <p className="erreur">{erreur}</p>}
      {ticket && <p className="note" style={{ marginTop: 8 }}>🧾 {ticket}</p>}

      {enAttente.length > 0 && (
        <>
          <h3>En attente de ta décision ({enAttente.length})</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="ml-table">
              <thead><tr>
                <th>Sens</th><th>Symbole</th><th>Score</th><th>Montant / Qté</th>
                <th>Stop suggéré</th><th>Motif</th><th></th>
              </tr></thead>
              <tbody>
                {enAttente.map((o) => (
                  <tr key={o.id}>
                    <td>{o.sens === "ACHAT" ? "🟢 ACHAT" : "🔴 VENTE"}</td>
                    <td>{o.symbole}</td>
                    <td>{o.score}</td>
                    <td>{o.montant_usd != null ? fmtUsd(o.montant_usd) : o.qty}</td>
                    <td>{o.stop_suggere ?? "—"}</td>
                    <td className="note">{o.motif}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <button className="action" style={{ marginRight: 6 }}
                              onClick={() => decider(o.id, "valider")}>Valider (papier)</button>
                      <button className="action secondaire"
                              onClick={() => decider(o.id, "rejeter")}>Rejeter</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {historique.length > 0 && (
        <>
          <h3>Décisions récentes</h3>
          <TableSimple lignes={historique}
            colonnes={["quand", "sens", "symbole", "score", "statut"]} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- App
const PAGES = { "Marchés": PageMarches, "Analyse": PageAnalyse, "ML": PageMl,
                "Ordres": PageOrdres, "Paper": PagePaper };

export default function App() {
  const [page, setPage] = useState("Marchés");
  const [univers, setUnivers] = useState(null);
  const [erreur, setErreur] = useState("");

  useEffect(() => {
    api.getUnivers().then(setUnivers)
      .catch(() => setErreur("API injoignable — lancer : python -m uvicorn main:app --app-dir api --port 8600"));
  }, []);

  const Page = PAGES[page];
  return (
    <>
      <header className="ml-header">
        <h1>📈 MarketLab</h1>
        <span className="ml-disclaimer">Aide à la décision — analyses
          statistiques, pas des prédictions ni des conseils en investissement.</span>
      </header>
      <nav className="ml-nav">
        {Object.keys(PAGES).map((p) => (
          <button key={p} className={p === page ? "actif" : ""}
                  onClick={() => setPage(p)}>{p}</button>
        ))}
      </nav>
      {erreur && <p className="erreur">{erreur}</p>}
      {univers && <Page univers={univers} />}
    </>
  );
}
