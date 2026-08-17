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
// Ce fichier n'est qu'un ENROBAGE React : tout le dessin vit dans
// `terminal-chart.js`, sans framework, parce que l'espace de trading est en
// PHP et doit afficher EXACTEMENT le même graphique. Deux implémentations,
// ce serait deux comportements qui divergent au premier correctif.
//
// CE QU'IL AFFICHE QUE LES PLATEFORMES DE BROKER N'AFFICHENT PAS. Le plan de
// trade est tracé SUR le graphique : entrée, stop, objectif, supports et
// résistances. Chez un broker, ces niveaux sont des chiffres dans un
// formulaire, à reporter mentalement sur la courbe — c'est précisément là que
// se prennent les mauvaises décisions. Ici la question « le stop est-il sous
// le dernier creux ? » se répond d'un coup d'œil.

import { Component, useEffect, useRef } from "react";
import { creerGraphique } from "./terminal-chart";

// Réexportées pour que la page n'ait qu'une porte d'entrée.
export {
  PAS, agreger, enBougies, fusionner, noteDerniereSeance, precision, socle,
} from "./series";

export const TYPES = [
  { cle: "bougies", libelle: "Bougies" },
  { cle: "barres", libelle: "Barres OHLC" },
  { cle: "ligne", libelle: "Ligne" },
  { cle: "montagne", libelle: "Montagne" },
];

/**
 * Filet de sécurité autour du graphique.
 *
 * POURQUOI. Le graphique est le seul morceau du site qui dessine en canvas via
 * une bibliothèque tierce, et une exception pendant un rendu React NON
 * RATTRAPÉE ne casse pas le composant fautif : elle démonte toute la page. La
 * fiche d'un titre porte le verdict, le plan, les vetos — l'essentiel de la
 * décision. Il serait absurde que tout cela disparaisse parce qu'une série
 * mal formée a fait trébucher un chandelier.
 *
 * Ce n'est pas une excuse pour ne pas corriger la cause : l'erreur reste
 * écrite en clair à l'écran et dans la console.
 */
export class GardeGraphique extends Component {
  constructor(props) {
    super(props);
    this.state = { panne: null };
  }

  static getDerivedStateFromError(erreur) {
    return { panne: erreur };
  }

  componentDidCatch(erreur, info) {
    console.error("Graphique en panne :", erreur, info);
  }

  render() {
    if (this.state.panne) {
      return (
        <p className="erreur" style={{ padding: "36px 0", textAlign: "center" }}>
          Le graphique n'a pas pu être tracé ({String(this.state.panne.message
            ?? this.state.panne).slice(0, 120)}). Le reste de la fiche — verdict,
          plan, niveaux — est intact ci-dessous.
        </p>
      );
    }
    return this.props.children;
  }
}

// ---------------------------------------------------------------- composant

/** Enrobage React du pilote. Chaque effet ne fait qu'une chose : pousser un
 *  état au pilote. Aucun dessin ici. */
export function GraphiqueTerminal({
  bloc, type = "bougies", surcouches = {}, plan = null, niveaux = null,
  hauteur = 420, logarithmique = false, onSurvol = null, barresVisibles = 0,
}) {
  const conteneur = useRef(null);
  const pilote = useRef(null);
  // Le rappel de survol est lu à travers une référence : il change à chaque
  // rendu, et le réabonner ferait reconstruire le graphique en boucle.
  const survol = useRef(onSurvol);
  survol.current = onSurvol;

  useEffect(() => {
    if (!conteneur.current) return undefined;
    pilote.current = creerGraphique(conteneur.current, {
      hauteur, type, barresVisibles, surSurvol: (b) => survol.current?.(b),
    });
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const maj = () => pilote.current?.theme();
    mq.addEventListener("change", maj);
    return () => {
      mq.removeEventListener("change", maj);
      pilote.current?.detruire();
      pilote.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hauteur]);

  useEffect(() => { pilote.current?.type(type); }, [type]);
  useEffect(() => { if (bloc) pilote.current?.donnees(bloc, barresVisibles); },
            [bloc, barresVisibles]);
  useEffect(() => { pilote.current?.surcouches(surcouches); }, [surcouches, bloc]);
  useEffect(() => { pilote.current?.logarithmique(logarithmique); }, [logarithmique]);

  useEffect(() => {
    const lignes = [];
    if (plan) {
      lignes.push({ prix: plan.entree, titre: "entrée", couleur: "accent", style: "plein" });
      lignes.push({ prix: plan.stop, titre: "stop", couleur: "baisse" });
      lignes.push({ prix: plan.objectif, titre: "objectif", couleur: "hausse" });
    }
    for (const z of niveaux?.supports ?? []) {
      lignes.push({ prix: z.niveau, titre: "support", couleur: "neutre", style: "pointille" });
    }
    for (const z of niveaux?.resistances ?? []) {
      lignes.push({ prix: z.niveau, titre: "résistance", couleur: "neutre", style: "pointille" });
    }
    pilote.current?.lignes(lignes);
  }, [plan, niveaux, type, bloc]);

  return <div ref={conteneur} style={{ width: "100%", height: hauteur }} />;
}
