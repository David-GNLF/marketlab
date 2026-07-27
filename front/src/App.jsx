import { useEffect, useState } from "react";
import * as api from "./api";
import { GraphiqueCone, GraphiquePrix } from "./charts";

const pct = (v, signe = false) =>
  v == null ? "—" : `${signe && v > 0 ? "+" : ""}${Number(v).toFixed(2)} %`;
const nb = (v) =>
  v == null ? "—" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 4 });

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
function PageMarches({ onTitre }) {
  const { donnees, erreur } = useDonnees(api.getScreener);
  const [filtre, setFiltre] = useState("");
  if (!donnees) return <Chargement erreur={erreur} />;

  const lignes = donnees.filter((l) =>
    !filtre || String(l.symbole).toLowerCase().includes(filtre.toLowerCase()));
  const forts = lignes.filter((l) => l.avis === "Achat fort");
  const faibles = lignes.filter((l) => l.avis === "Vente forte");

  return (
    <div className="carte">
      <div className="rangee" style={{ marginBottom: 12 }}>
        <Tuile libelle="Titres suivis" valeur={lignes.length} />
        <Tuile libelle="Achat fort" valeur={forts.length}
               note={forts.map((l) => l.symbole).join(", ") || "—"} />
        <Tuile libelle="Vente forte" valeur={faibles.length}
               note={faibles.map((l) => l.symbole).join(", ") || "—"} />
        <label className="champ">Filtrer
          <input type="text" value={filtre} placeholder="AAPL…"
                 onChange={(e) => setFiltre(e.target.value)} />
        </label>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="ml-table">
          <thead><tr>
            {["symbole", "score", "avis", "cours", "rsi14", "perf_20j_%",
              "vol_ann_%", "drawdown_%"].map((c) => <th key={c}>{c}</th>)}
          </tr></thead>
          <tbody>
            {lignes.map((l, i) => (
              <tr key={i} onClick={() => onTitre(l.symbole)}
                  style={{ cursor: "pointer" }}>
                <td><strong>{l.symbole}</strong></td>
                <td>{l.score ?? "—"}</td>
                <td><span className="badge">{l.avis ?? "—"}</span></td>
                <td>{nb(l.cours)}</td>
                <td>{l.rsi14 ?? "—"}</td>
                <td>{l["perf_20j_%"] ?? "—"}</td>
                <td>{l["vol_ann_%"] ?? "—"}</td>
                <td>{l["drawdown_%"] ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">Cliquer sur une ligne pour ouvrir la fiche du titre.</p>
    </div>
  );
}

// ---------------------------------------------------------------- Fiche titre
function PageTitre({ meta, symbole, setSymbole }) {
  const dispo = meta?.titres ?? [];
  const actif = symbole && dispo.includes(symbole) ? symbole : dispo[0];
  const { donnees: f, erreur } = useDonnees(() => api.getTitre(actif), [actif]);

  return (
    <>
      <div className="carte">
        <label className="champ">Titre
          <select value={actif ?? ""} onChange={(e) => setSymbole(e.target.value)}>
            {dispo.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <p className="note">Seuls les titres ci-dessus disposent d'une fiche
          détaillée (choix fait à la génération).</p>
      </div>

      {!f ? <Chargement erreur={erreur} /> : (
        <>
          {f.strategie && !f.strategie.erreur && (
            <div className="carte" style={{ borderLeft: "3px solid var(--series-1)" }}>
              <h3 style={{ marginTop: 0 }}>🎯 {f.strategie.nom} — stratégie de
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
                <p style={{ marginTop: 10 }}>🧭 <strong>Conclusion</strong> —{" "}
                  {f.strategie.verdict.conclusion.texte}</p>
              )}
              {f.strategie.plan && (
                <p style={{ marginTop: 10 }}>📋 Entrée {nb(f.strategie.plan.entree)} ·
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
                <p className="note">🪜 Stop suiveur (Chandelier) :{" "}
                  {nb(f.strategie.plan.stop_suiveur.niveau)} —{" "}
                  {f.strategie.plan.stop_suiveur.raison}</p>
              )}
              {f.strategie.renforts && (
                <p className="note">🛡️ Renforts ({f.strategie.renforts.feux_verts}
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
                <p key={i} className="erreur">⚠️ {v}</p>
              ))}
            </div>
          )}
          <div className="carte">
            <div className="rangee">
              <Tuile libelle="Cours" valeur={nb(f.signaux?.close)} />
              <Tuile libelle="Score composite" valeur={f.signaux?.score ?? "—"}
                     note={f.signaux?.avis} />
              <Tuile libelle="RSI 14" valeur={f.signaux?.rsi14 ?? "—"} />
              <Tuile libelle="Perf 20 j" valeur={pct(f.signaux?.ret_20d, true)}
                     delta={f.signaux?.ret_20d} />
              {f.regime && !f.regime.erreur && (
                <Tuile libelle="Régime"
                       valeur={f.regime.tendance}
                       note={`volatilité ${f.regime.volatilite}`} />
              )}
            </div>
            {f.regime?.lecture && <p className="note">{f.regime.lecture}</p>}
            {f.historique && <GraphiquePrix donnees={f.historique} />}
          </div>

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
              <h3>🧰 Les outils des brokers</h3>
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
                <p className="erreur">⚠️ {f.brokers.avertissement_regime}</p>
              )}
            </div>
          )}
          <div className="carte">
            <h3>Contexte</h3>
            {f.analogues && !f.analogues.erreur && (
              <p>📚 <strong>Analogues historiques</strong> — sur les {f.analogues.k}
                {" "}configurations passées les plus proches : hausse dans{" "}
                <strong>{f.analogues["proba_hausse_%"]} %</strong> des cas,
                rendement médian {pct(f.analogues["rendement_median_%"], true)}
                {" "}(extrêmes {pct(f.analogues["pire_%"])} /{" "}
                {pct(f.analogues["meilleur_%"])}).</p>
            )}
            {f.niveaux?.zones && (
              <p>📉 <strong>Niveaux</strong> — supports :{" "}
                {f.niveaux.zones.supports?.map((z) => nb(z.niveau)).join(", ") || "aucun"}
                {" "}· résistances :{" "}
                {f.niveaux.zones.resistances?.map((z) => nb(z.niveau)).join(", ") || "aucune"}</p>
            )}
            {f.resultats && !f.resultats.erreur && f.resultats.message && (
              <p className={f.resultats.dans_horizon ? "erreur" : ""}>
                📣 <strong>Résultats</strong> — {f.resultats.message}</p>
            )}
            {f.moteurs?.length > 0 && f.moteurs.map((m, i) => (
              <p key={i}>⚙️ <strong>{m.outil}</strong> — {m.lecture}</p>
            ))}
            {f.saisonnalite && !f.saisonnalite.erreur && (
              <p>🗓️ <strong>Saisonnalité</strong> — {f.saisonnalite.conclusion}</p>
            )}
            {f.sentiment && !f.sentiment.erreur && f.sentiment.n_titres > 0 && (
              <p>📰 <strong>Actualités</strong> — sentiment {f.sentiment.lecture}
                {" "}({f.sentiment.positifs}➕ / {f.sentiment.negatifs}➖ sur{" "}
                {f.sentiment.n_titres} titres, mesure lexicale indicative).</p>
            )}
          </div>
        </>
      )}
    </>
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
    return { texte: "⛔ NE PAS TRADER — un garde-fou bloque ce dossier",
             couleur: "var(--critical)" };
  }
  if (d.avis === "Favorable" && d.plan) {
    const t = d.taille_multiplicateur < 1
      ? ` à taille réduite (×${d.taille_multiplicateur})` : "";
    return { texte: `🟢 ACHAT envisageable${t} — entrée ${nb(d.plan.entree)}, `
             + `stop ${nb(d.plan.stop)}, objectif ${nb(d.plan.objectif)}`,
             couleur: "var(--good)" };
  }
  if (d.avis === "Défavorable") {
    return { texte: "🔴 ÉVITER À L'ACHAT — analyses défavorables (le bilan "
             + "déconseille aussi la vente à découvert : rester à l'écart)",
             couleur: "var(--critical)" };
  }
  return { texte: "⚪ SURVEILLER — les analyses ne convergent pas assez pour agir",
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

function Verdict({ d, rang, onTitre }) {
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
            cours {nb(d.prix)}</div>
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
          <div className="tuile" style={{ minWidth: 100 }}>
            <div className="libelle">P(hausse) · {c.periode_seances} séances</div>
            <div className="valeur" style={{ fontSize: 18 }}>
              {c["proba_hausse_combinee_%"]} %</div>
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
            <div style={{ fontSize: 13 }}>{d.brokers.haussiers} 🟢 ·{" "}
              {d.brokers.baissiers} 🔴 <span className="note">
                ({d.brokers.tendance})</span></div>
          </div>
        )}
        <span className="note" style={{ marginLeft: "auto" }}>
          {ouvert ? "▲ replier" : "▼ pourquoi ?"}</span>
      </div>

      <p style={{ margin: "8px 0 0", fontWeight: 600, color: action.couleur }}>
        {action.texte}</p>
      {d.brokers?.avertissement && (
        <p className="note" style={{ margin: "4px 0 0" }}>
          ⚠️ {d.brokers.avertissement}</p>
      )}
      {d.vetos?.length > 0 && d.vetos.map((v, i) => (
        <p key={i} className="erreur" style={{ margin: "4px 0 0" }}>⚠️ {v}</p>
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
          {d.conclusion?.texte && (
            <p style={{ marginTop: 8 }}>🧭 <strong>Conclusion</strong> —{" "}
              {d.conclusion.texte}</p>
          )}
          {d.plan && (
            <p style={{ marginTop: 8 }}>📋 Plan : entrée {nb(d.plan.entree)} ·
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
  const classes = ["Tous",
                   ...new Set(tous.map((d) => d.classe).filter(Boolean))];
  const dossiers = tous
    .filter((d) => classe === "Tous" || d.classe === classe)
    .sort(TRIS[tri]);
  const bilan = donnees.bilan;
  return (
    <>
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
            ouvre le détail.</span>
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
                n: app.donnees.ic_par_composante?.[nom]?.n ?? "—",
              }))} />
            )}
            <p className="note">{app.donnees.methode}</p>
          </>
        )}
      </div>
      {dossiers.map((d, i) => (
        <Verdict key={d.symbole} d={d} rang={i + 1} onTitre={onTitre} />
      ))}
    </>
  );
}

// ---------------------------------------------------------------- Concours
function PageConcours() {
  const { donnees, erreur } = useDonnees(api.getConcours);
  if (!donnees) return <Chargement erreur={erreur} />;
  const robot = donnees.comptes?.find((c) => c.est_robot);
  return (
    <>
      <div className="carte">
        <h3>🏆 Concours de trading virtuel</h3>
        <p className="note">Chaque compte part avec{" "}
          {nb(donnees.capital_depart)} $ virtuels. Le robot « claude » applique
          les verdicts de l'outil — le battre, c'est battre la machine.
          Créer votre compte : <a href="trading/">espace de trading</a>.
          Mis à jour le {donnees.date}.</p>
        <Table lignes={donnees.comptes?.map((c, i) => ({
          "#": i + 1,
          compte: (c.est_robot ? "🤖 " : "👤 ") + c.nom,
          "équité $": c.equite,
          "perf %": c["perf_%"],
          positions: c.n_positions,
          "trades clos": c.n_trades,
        }))} />
      </div>
      {robot && (
        <div className="carte">
          <h3>🤖 Le robot en transparence totale</h3>
          <p className="note">{donnees.regles_robot}</p>
          {robot.positions?.length > 0 && (
            <>
              <h4>Positions ouvertes</h4>
              <Table lignes={robot.positions} />
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
      )}
      <p className="note">{donnees.avertissement}</p>
    </>
  );
}

// ---------------------------------------------------------------- App
const PAGES = {
  "🎯 Décisions": PageDecisions,
  "🏆 Concours": PageConcours,
  "Marchés": PageMarches,
  "Titre": PageTitre,
  "Macro & agenda": PageMacro,
  "Fondamentaux": PageFondamentaux,
  "Corrélations": PageCorrelations,
  "Portefeuille": PagePortefeuille,
};

export default function App() {
  const [page, setPage] = useState("🎯 Décisions");
  const [symbole, setSymbole] = useState(null);
  const { donnees: meta, erreur } = useDonnees(api.getMeta);

  const ouvrirTitre = (s) => { setSymbole(s); setPage("Titre"); };
  const Page = PAGES[page];

  return (
    <>
      <header className="ml-header">
        <h1>📈 MarketLab</h1>
        <span className="ml-disclaimer">Analyses statistiques, pas des
          prédictions. Aucun contenu ne constitue un conseil en investissement.</span>
      </header>
      <nav className="ml-nav">
        {Object.keys(PAGES).map((p) => (
          <button key={p} className={p === page ? "actif" : ""}
                  onClick={() => setPage(p)}>{p}</button>
        ))}
      </nav>
      {erreur && <p className="erreur">Données indisponibles : {erreur}</p>}
      {meta && (
        <p className="note">Instantané du {meta.genere_le} ({meta.fuseau}).
          {meta.erreurs && Object.keys(meta.erreurs).length > 0 &&
            ` ${Object.keys(meta.erreurs).length} bloc(s) en erreur lors de la génération.`}
        </p>
      )}
      {meta && (page === "Titre"
        ? <Page meta={meta} symbole={symbole} setSymbole={setSymbole} />
        : <Page onTitre={ouvrirTitre} />)}
    </>
  );
}
