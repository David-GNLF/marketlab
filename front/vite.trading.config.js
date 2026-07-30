// Deuxième construction : le graphique en module AUTONOME, pour l'espace de
// trading.
//
// POURQUOI UNE SECONDE PASSE. L'espace de trading est du PHP servi tel quel :
// il n'a pas de bundler, pas de React, pas d'étape de build. Mais il doit
// afficher EXACTEMENT le même graphique que le site — sinon on entretient deux
// implémentations qui divergeront au premier correctif, ce que ce projet
// combat partout ailleurs par des sources uniques.
//
// La sortie est un fichier au nom FIXE (pas de hachage) : la page PHP le
// référence en dur, et un nom qui change à chaque build casserait la balise à
// chaque publication. Le cache est géré par un paramètre de version côté page.

import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist-trading",
    emptyOutDir: true,
    lib: {
      entry: "src/graphique-autonome.js",
      name: "MarketLabGraphique",
      formats: ["iife"],
      fileName: () => "marketlab-graphique.js",
    },
  },
});
