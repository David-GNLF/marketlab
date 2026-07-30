// Lecture d'ensemble et décomposition du raisonnement.
//
// Trois diagrammes, tous bâtis sur le même dessin : une barre DIVERGENTE
// centrée sur zéro. Le choix n'est pas esthétique — ce qu'on montre ici est
// toujours signé (un apport pousse ou retient, une devise se renforce ou
// s'affaiblit), et un histogramme classique partant de la gauche obligerait à
// lire la couleur pour connaître le sens. Avec un axe central, le sens se voit
// avant le chiffre.
//
// Chaque barre porte sa valeur en toutes lettres à côté : un diagramme dont on
// ne peut pas lire les nombres n'informe pas, il illustre.

import { Terme } from "./glossaire";

const VERT = "var(--good)";
const ROUGE = "var(--critical)";

/** Barre signée, centrée sur zéro. `valeur` et `max` dans la même unité. */
function BarreDivergente({ valeur, max, hauteur = 14 }) {
  const v = Number(valeur) || 0;
  const borne = Math.max(Math.abs(max) || 1, 1e-9);
  const largeur = Math.min(Math.abs(v) / borne, 1) * 50;   // % de la demi-largeur
  return (
    <div style={{ position: "relative", height: hauteur, flex: 1,
                  minWidth: 90, background: "var(--grid)", borderRadius: 3 }}>
      {/* l'axe zéro, toujours visible : c'est lui qui donne le sens */}
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0,
                    width: 1, background: "var(--border)" }} />
      <div style={{
        position: "absolute", top: 0, bottom: 0,
        left: v >= 0 ? "50%" : `${50 - largeur}%`,
        width: `${largeur}%`,
        background: v >= 0 ? VERT : ROUGE,
        borderRadius: 3, opacity: .85,
      }} />
    </div>
  );
}

/**
 * D'où sort la note : contribution de chaque composante.
 *
 * La somme des contributions redonne EXACTEMENT la note publiée — c'est ce qui
 * fait la différence entre expliquer et illustrer. Le désaccord entre
 * composantes est signalé, parce que la note finale l'efface par construction :
 * +40 et −40 donnent 0, exactement comme deux composantes muettes, alors que
 * les deux situations n'ont rien à voir.
 */
export function Contributions({ contributions, note }) {
  const lignes = contributions?.lignes ?? [];
  if (!lignes.length) return null;
  const max = Math.max(...lignes.map((l) => Math.abs(l.contribution)), 1);

  return (
    <div>
      <div className="rangee" style={{ justifyContent: "space-between",
        alignItems: "baseline", marginBottom: 6 }}>
        <strong>D'où vient cette note</strong>
        {contributions.desaccord && (
          <span className="badge" title="Les composantes ne disent pas la même chose">
            composantes en désaccord
          </span>
        )}
      </div>
      <table style={{ width: "100%", fontSize: 12.5 }}>
        <tbody>
          {lignes.map((l) => (
            <tr key={l.nom}>
              <td style={{ whiteSpace: "nowrap", paddingRight: 8 }}
                  title={l.fondement}>{l.libelle}</td>
              <td style={{ width: "42%", padding: "3px 8px" }}>
                <BarreDivergente valeur={l.contribution} max={max} />
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap",
                           fontVariantNumeric: "tabular-nums" }}>
                <strong style={{ color: l.contribution >= 0 ? VERT : ROUGE }}>
                  {l.contribution >= 0 ? "+" : ""}{l.contribution}
                </strong>
                <span className="note"> · {l["part_%"]} %</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note" style={{ marginTop: 6 }}>
        Chaque apport vaut la note de la composante multipliée par son poids ;
        leur somme redonne la note affichée ({note >= 0 ? "+" : ""}{note}).
        Le pourcentage indique la part de l'explication, en valeur absolue —
        une composante qui pousse et une qui retient comptent autant l'une que
        l'autre.
        {contributions.moteur_principal && (
          <> Ici, <strong>{contributions.moteur_principal}</strong> pousse
            {contributions.frein_principal
              ? <> et <strong>{contributions.frein_principal}</strong> retient.</>
              : <> sans opposition.</>}
          </>
        )}
      </p>
      <details style={{ marginTop: 6 }}>
        <summary className="note" style={{ cursor: "pointer" }}>
          Sur quoi chaque composante est-elle basée ?
        </summary>
        <dl className="glossaire" style={{ marginTop: 6 }}>
          {lignes.map((l) => (
            <div key={l.nom}>
              <dt>{l.libelle}</dt>
              <dd>
                {l.fondement}
                {l.raisons?.length > 0 && (
                  <div className="note" style={{ marginTop: 2 }}>
                    Ce qui a été constaté : {l.raisons.join(" · ")}
                  </div>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}

/** Orientation d'ensemble par classe d'actif. */
export function ParCategorie({ donnees }) {
  if (!donnees?.length) return null;
  const max = Math.max(...donnees.map((c) => Math.abs(c.note_moyenne)), 20);
  return (
    <div className="carte">
      <h3>Où est le vent, par catégorie</h3>
      <p className="note">Avant de choisir un titre : les classes d'actif
        sont-elles orientées, et dans quel sens ? Une dispersion élevée signifie
        que la classe ne dit rien de commun — le choix du titre compte alors
        plus qu'elle.</p>
      <table style={{ width: "100%", fontSize: 13 }}>
        <tbody>
          {donnees.map((c) => (
            <tr key={c.classe}>
              <td style={{ whiteSpace: "nowrap", paddingRight: 8 }}>
                <strong>{c.classe}</strong>
                <span className="note"> · {c.n}</span>
              </td>
              <td style={{ width: "40%", padding: "4px 8px" }}>
                <BarreDivergente valeur={c.note_moyenne} max={max} />
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap",
                           fontVariantNumeric: "tabular-nums" }}>
                <strong style={{ color: c.note_moyenne >= 0 ? VERT : ROUGE }}>
                  {c.note_moyenne >= 0 ? "+" : ""}{c.note_moyenne}
                </strong>
                <span className="note"> ± {c.dispersion}</span>
              </td>
              <td className="note" style={{ paddingLeft: 10 }}>
                {Object.entries(c.avis).map(([a, n]) => `${n} ${a}`).join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {donnees[0]?.lecture && (
        <p className="note" style={{ marginTop: 6 }}>{donnees[0].lecture}</p>
      )}
    </div>
  );
}

/**
 * Force des devises — le vrai pari derrière les paires.
 *
 * Une paire oppose deux devises : un avis favorable sur EUR/USD est un avis
 * favorable sur l'euro ET défavorable sur le dollar. Reporter chaque note sur
 * ses deux devises fait apparaître ce que la liste par symbole cache — que
 * trois idées n'en font parfois qu'une.
 */
export function ParDevise({ donnees }) {
  const forces = donnees?.forces ?? [];
  if (!forces.length) return null;
  const max = Math.max(...forces.map((f) => Math.abs(f.force)), 20);
  return (
    <div className="carte">
      <h3>Quel est le vrai pari, par devise</h3>
      <p className="note">Une paire oppose deux devises : un avis favorable sur
        EUR/USD dit du bien de l'euro <em>et</em> du mal du dollar. En reportant
        chaque note sur ses deux devises, on voit si plusieurs idées n'en font
        qu'une.</p>
      <table style={{ width: "100%", fontSize: 13 }}>
        <tbody>
          {forces.map((f) => (
            <tr key={f.devise}>
              <td style={{ whiteSpace: "nowrap", paddingRight: 8 }}>
                <strong>{f.devise}</strong>
              </td>
              <td style={{ width: "42%", padding: "4px 8px" }}>
                <BarreDivergente valeur={f.force} max={max} />
              </td>
              <td style={{ textAlign: "right", whiteSpace: "nowrap",
                           fontVariantNumeric: "tabular-nums" }}>
                <strong style={{ color: f.force >= 0 ? VERT : ROUGE }}>
                  {f.force >= 0 ? "+" : ""}{f.force}
                </strong>
              </td>
              <td className="note" style={{ paddingLeft: 10, whiteSpace: "nowrap" }}>
                {f.n_paires > 1
                  ? `${f.n_paires} paires · ${Math.abs(f.accord * 100).toFixed(0)} % d'accord`
                  : "1 paire — reflet d'un seul verdict"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note" style={{ marginTop: 6 }}>{donnees.lecture}</p>
      <p className="note">Une devise vue sur une seule paire n'apporte rien de
        plus que le verdict de cette paire : sa force n'en est que le miroir.
        Seules celles recoupées sur plusieurs paires disent quelque chose.
        Prendre plusieurs paires qui partagent la même devise revient à répéter
        un pari — ce que le contrôle de{" "}
        <Terme code="volatilite">concentration</Terme> corrige à l'exécution.</p>
    </div>
  );
}
