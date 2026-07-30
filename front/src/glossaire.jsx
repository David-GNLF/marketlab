// Bulles d'explication du vocabulaire.
//
// Le site emploie des sigles (RSI, ATR, VIX, COT, VaR) et des mots de métier
// (levier, notionnel, drawdown, calibration) qui ne veulent rien dire pour
// quelqu'un qui découvre. Les définitions vivent dans `marketlab/glossaire.py`
// et arrivent ici par `donnees/glossaire.json` : une seule source, jamais une
// définition recopiée dans un composant.
//
// TROIS CONTRAINTES, et elles écartent la solution la plus simple.
//
// L'attribut `title` du navigateur serait gratuit, mais il ne s'ouvre qu'après
// une seconde de survol, ne s'affiche jamais au toucher, et n'est pas
// atteignable au clavier. Pour une aide destinée à un débutant — c'est-à-dire
// à quelqu'un qui en a réellement besoin — ces trois défauts sont
// rédhibitoires.
//
// D'où un vrai déclencheur : un `<button>`, donc focusable et actionnable au
// clavier comme au doigt, relié à sa bulle par `aria-describedby` pour les
// lecteurs d'écran.

import { useEffect, useId, useRef, useState } from "react";
import * as api from "./api";

let cache = null;
const abonnes = new Set();

/** Charge le glossaire une seule fois pour toute l'application. */
function useGlossaire() {
  const [g, setG] = useState(cache);
  useEffect(() => {
    if (cache) return;
    abonnes.add(setG);
    if (abonnes.size === 1) {
      api.getGlossaire()
        .then((d) => { cache = d; abonnes.forEach((f) => f(d)); })
        // Une aide indisponible ne doit jamais casser une page : sans
        // glossaire, `Terme` rend simplement son texte, sans bulle.
        .catch(() => { cache = { termes: {} }; abonnes.forEach((f) => f(cache)); });
    }
    return () => abonnes.delete(setG);
  }, []);
  return g;
}

/**
 * Un terme expliqué. `<Terme code="rsi" />` ou `<Terme code="rsi">RSI</Terme>`
 * pour imposer le libellé affiché.
 */
export function Terme({ code, children, discret = false }) {
  const g = useGlossaire();
  const [ouvert, setOuvert] = useState(false);
  const id = useId();
  const boite = useRef(null);
  const def = g?.termes?.[code];

  useEffect(() => {
    if (!ouvert) return;
    const dehors = (e) => {
      if (boite.current && !boite.current.contains(e.target)) setOuvert(false);
    };
    const echap = (e) => { if (e.key === "Escape") setOuvert(false); };
    document.addEventListener("mousedown", dehors);
    document.addEventListener("keydown", echap);
    return () => {
      document.removeEventListener("mousedown", dehors);
      document.removeEventListener("keydown", echap);
    };
  }, [ouvert]);

  const texte = children ?? def?.libelle ?? code;
  if (!def) return <>{texte}</>;   // glossaire non chargé : rien ne casse

  return (
    <span className="terme" ref={boite}
          onMouseEnter={() => setOuvert(true)}
          onMouseLeave={() => setOuvert(false)}>
      <button type="button"
              className={`terme-declencheur${discret ? " discret" : ""}`}
              aria-describedby={ouvert ? id : undefined}
              aria-expanded={ouvert}
              onClick={(e) => { e.stopPropagation(); setOuvert((v) => !v); }}
              onFocus={() => setOuvert(true)}
              onBlur={() => setOuvert(false)}>
        {texte}
      </button>
      {ouvert && (
        <span role="tooltip" id={id} className="terme-bulle">
          <strong>{def.libelle}</strong>
          <span>{def.court}</span>
          {def.long && <span className="terme-long">{def.long}</span>}
        </span>
      )}
    </span>
  );
}

/** Panneau d'aide : tout le vocabulaire, groupé par thème. */
export function PanneauGlossaire() {
  const g = useGlossaire();
  const [filtre, setFiltre] = useState("");
  if (!g) return <p className="note">Chargement du glossaire…</p>;

  const groupes = g.par_categorie ?? {};
  const q = filtre.trim().toLowerCase();
  const retenir = (t) => !q
    || t.libelle.toLowerCase().includes(q)
    || t.court.toLowerCase().includes(q)
    || (t.long ?? "").toLowerCase().includes(q);

  const visibles = Object.entries(groupes)
    .map(([cat, liste]) => [cat, liste.filter(retenir)])
    .filter(([, liste]) => liste.length);

  return (
    <>
      <input className="recherche" type="search" value={filtre}
             placeholder="Chercher un mot, un sigle…"
             onChange={(e) => setFiltre(e.target.value)} />
      {!visibles.length && (
        <p className="note">Aucun terme ne correspond à « {filtre} ».</p>
      )}
      {visibles.map(([categorie, liste]) => (
        <div key={categorie} className="carte">
          <h3>{categorie}</h3>
          <dl className="glossaire">
            {liste.map((t) => (
              <div key={t.code}>
                <dt>{t.libelle}</dt>
                <dd>
                  {t.court}
                  {t.long && <div className="note">{t.long}</div>}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </>
  );
}
