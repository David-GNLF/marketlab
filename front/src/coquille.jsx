// La coquille de l'application : marque, rail de navigation, barre d'état.
//
// POURQUOI UN RAIL PLUTÔT QU'UNE RANGÉE D'ONGLETS. Onze onglets alignés en
// haut d'une page finissent par passer à la ligne, et la ligne du bas devient
// une seconde classe de navigation qu'on ne voit plus. Un rail vertical tient
// les onze destinations à la même distance de l'œil, garde le nom de la page
// courante lisible pendant qu'on travaille, et libère toute la largeur pour
// le contenu — c'est la disposition de tous les terminaux de marché, et ce
// n'est pas un hasard : leur contenu est large, jamais haut.
//
// LES PICTOGRAMMES SONT GÉOMÉTRIQUES, PAS ILLUSTRATIFS. Chacun reprend la
// forme de ce que la page montre : un histogramme pour les marchés, un
// chandelier pour un titre, une balance pour les corrélations. Aucun n'essaie
// d'être joli ; ils servent à retrouver une ligne du regard, et rien d'autre.

import { useState } from "react";

export function Logo({ taille = 30 }) {
  return (
    <svg width={taille} height={taille} viewBox="0 0 32 32" fill="none"
         aria-hidden="true">
      {/* La ligne de référence en pointillés : l'outil ne promet pas la
          hausse, il mesure un écart à une base. C'est tout le propos. */}
      <path d="M3 20h26" stroke="currentColor" strokeWidth="1.5"
            strokeDasharray="3 3" opacity="0.45" />
      <path d="M3 24l5-6 4 4 6-11 5 8 6-10" stroke="currentColor"
            strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Chaque glyphe est un tracé de 16×16, épaisseur 1.6, sans remplissage.
const GLYPHES = {
  "Décisions": "M2 12l4-4 3 3 5-7M11 4h4v4",
  "Alertes": "M8 2a4 4 0 0 0-4 4c0 3-1 4-1 5h10c0-1-1-2-1-5a4 4 0 0 0-4-4zM6.5 13a1.5 1.5 0 0 0 3 0",
  "Concours": "M4 3h8v3a4 4 0 0 1-8 0V3zM2 4h2M12 4h2M6 13h4M8 10v3",
  "Marchés": "M2 14V8M6 14V4M10 14V10M14 14V6",
  "Titre": "M4 6h3v6H4zM11 4h3v9h-3zM5.5 3v3M5.5 12v2M12.5 2v2M12.5 13v2",
  "Macro & agenda": "M2 4h12v10H2zM2 7h12M6 2v3M10 2v3",
  "Fondamentaux": "M3 13V6l5-4 5 4v7zM6.5 13V9h3v4",
  "Corrélations": "M8 2v12M3 5l5 3 5-3M3 11l5-3 5 3",
  "Portefeuille": "M2 5h12v9H2zM5 5V3h6v2M2 9h12",
  // chevrons de code : la seule section qui parle de l'application
  "Coulisses": "M5.5 5L2 8l3.5 3M10.5 5L14 8l-3.5 3M9 3l-2 10",
};

export function Glyphe({ nom }) {
  const d = GLYPHES[nom];
  if (!d) return null;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d={d} stroke="currentColor" strokeWidth="1.6"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Barre d'état de la donnée, en haut à droite.
 *
 * Elle dit trois choses et pas une de plus : de quand date l'analyse, combien
 * de cotations sont réellement en direct, et si un bloc a échoué à la
 * génération. Le projet tient à ce que le site n'affirme que du vérifié —
 * autant que l'état de la donnée soit lisible en permanence plutôt que
 * relégué en petit sous le titre.
 */
export function EtatDonnees({ meta, cours, actualise }) {
  const total = Object.keys(cours ?? {}).length;
  const directs = Object.values(cours ?? {})
    .filter((c) => c.frais && c.source !== "publié").length;
  const erreurs = Object.keys(meta?.erreurs ?? {}).length;
  return (
    <div className="ml-etat">
      {total > 0 && (
        <span className="ml-etat-item" title={
          `${directs} cotation(s) fraîche(s) sur ${total} ; les autres sont `
          + "différées, sur un marché fermé, ou repliées sur l'instantané"}>
          <span className={"ml-pastille" + (directs ? " vive" : "")} />
          {directs}/{total} en direct
        </span>
      )}
      {actualise && (
        <span className="ml-etat-item">
          cours à {actualise.toLocaleTimeString("fr-FR",
            { hour: "2-digit", minute: "2-digit" })}
        </span>
      )}
      {meta && (
        <span className="ml-etat-item" title={`Fuseau : ${meta.fuseau}`}>
          analyse du {meta.genere_le}
        </span>
      )}
      {erreurs > 0 && (
        <span className="ml-etat-item erreur"
              title={Object.keys(meta.erreurs).join(", ")}>
          {erreurs} bloc(s) en erreur
        </span>
      )}
    </div>
  );
}

/**
 * La coquille. Elle ne connaît AUCUNE page : on lui passe la liste des noms,
 * celui de la page courante, et le contenu. C'est ce qui permet d'ajouter un
 * espace (« Coulisses », par exemple) sans y toucher.
 */
export function Coquille({ pages, page, setPage, titre, etat, children }) {
  const [ouvert, setOuvert] = useState(false);

  return (
    <div className={"ml-app" + (ouvert ? " rail-ouvert" : "")}>
      <aside className="ml-rail-nav">
        <div className="ml-rail-marque">
          <Logo />
          <div className="ml-rail-nom">
            <strong>MarketLab</strong>
            <span>by GNLF Consult</span>
          </div>
        </div>

        <nav aria-label="Sections">
          {pages.map((p) => (
            <button key={p} type="button"
                    className={"ml-rail-item" + (p === page ? " actif" : "")}
                    aria-current={p === page ? "page" : undefined}
                    onClick={() => { setPage(p); setOuvert(false); }}>
              <Glyphe nom={p} /><span>{p}</span>
            </button>
          ))}
        </nav>

        <div className="ml-rail-pied">
          <a href="trading/" className="ml-rail-item">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M2 11l4-4 3 3 5-6M2 14h12" stroke="currentColor"
                    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>Espace de trading</span>
          </a>
          <a href="admin/" className="ml-rail-item discret"
             title="Espace d'administration">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="8" cy="8" r="2.4" stroke="currentColor" strokeWidth="1.5" />
              <path d="M8 1.6v1.8M8 12.6v1.8M14.4 8h-1.8M3.4 8H1.6M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3M12.5 12.5l-1.3-1.3M4.8 4.8L3.5 3.5"
                    stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <span>Administration</span>
          </a>
        </div>
      </aside>

      <div className="ml-principal">
        <header className="ml-barre">
          <button type="button" className="ml-burger"
                  aria-label="Afficher les sections"
                  aria-expanded={ouvert}
                  onClick={() => setOuvert((v) => !v)}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path d="M2 4.5h14M2 9h14M2 13.5h14" stroke="currentColor"
                    strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
          <h1>{titre ?? page}</h1>
          {etat}
        </header>
        <main className="ml-contenu">{children}</main>
        <p className="ml-avertissement">
          Analyses statistiques, pas des prédictions. Aucun contenu ne
          constitue un conseil en investissement.
        </p>
      </div>

      {/* Voile de fermeture du rail sur petit écran : cliquer à côté referme,
          comme partout ailleurs. */}
      {ouvert && <button type="button" className="ml-voile"
                         aria-label="Masquer les sections"
                         onClick={() => setOuvert(false)} />}
    </div>
  );
}
