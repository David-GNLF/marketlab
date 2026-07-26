import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // Chemins relatifs : le site doit fonctionner dans un sous-dossier
  // (ex. https://open.bj/marketlab/) aussi bien qu'à la racine d'un domaine.
  base: "./",
  plugins: [react()],
  server: {
    port: 5180,
    // En développement, les JSON sont servis depuis ../site/donnees
    fs: { allow: [".."] },
  },
});
