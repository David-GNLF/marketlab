"""Génère l'instantané du site et le publie sur l'hébergement cPanel.

    .venv\\Scripts\\python scripts\\publier.py                 # génère + publie
    .venv\\Scripts\\python scripts\\publier.py --generer-seul   # sans transfert
    .venv\\Scripts\\python scripts\\publier.py --publier-seul   # transfert seul
    .venv\\Scripts\\python scripts\\publier.py --tester         # teste la connexion
    .venv\\Scripts\\python scripts\\publier.py --blocs screener macro

À planifier (Planificateur de tâches) pour tenir le site à jour, par exemple
chaque soir après la clôture américaine.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlab import ftps, publish


def main() -> int:
    parser = argparse.ArgumentParser(description="Publication du site MarketLab")
    parser.add_argument("--generer-seul", action="store_true")
    parser.add_argument("--publier-seul", action="store_true")
    parser.add_argument("--tester", action="store_true",
                        help="tester la connexion FTPS et sortir")
    parser.add_argument("--blocs", nargs="*", default=None,
                        help="limiter à certains blocs (screener, macro, …)")
    parser.add_argument("--titres", nargs="*", default=None,
                        help="limiter les fiches à ces symboles")
    parser.add_argument("--sans-purge", action="store_true",
                        help="ne pas effacer les bundles orphelins de assets/")
    args = parser.parse_args()

    if args.tester:
        try:
            print(ftps.tester_connexion())
            return 0
        except Exception as exc:
            print(f"ECHEC : {exc}")
            return 1

    if not args.publier_seul:
        print("== Génération de l'instantané ==")
        meta = publish.generer(titres=args.titres, blocs=args.blocs)
        print(f"  {len(meta['blocs'])} bloc(s), {len(meta['titres'])} fiche(s)")
        if meta["erreurs"]:
            print(f"  {len(meta['erreurs'])} erreur(s) :")
            for cle, message in meta["erreurs"].items():
                print(f"    - {cle} : {message[:100]}")
        relais = publish.copier_php()
        print(f"  relais PHP : {', '.join(relais) or 'aucun'}")
        print("  module graphique : "
              + ("copié" if publish.copier_module_graphique()
                 else "ABSENT (lancer « npm run build --prefix front »)"))
        if publish.copier_front():
            print("  front React copié")
        else:
            print("  ATTENTION : front/dist absent — lancer d'abord "
                  "« npm run build --prefix front »")

    if args.generer_seul:
        print(f"\nSite prêt dans : {publish.RACINE_SITE}")
        return 0

    print("\n== Publication FTPS ==")
    try:
        bilan = ftps.publier(purger=not args.sans_purge)
    except Exception as exc:
        print(f"ECHEC : {exc}")
        return 1
    print(f"  {bilan['envoyes']} fichier(s) envoyé(s), "
          f"{bilan['inchanges']} inchangé(s) -> {bilan['destination']}")
    purge = bilan.get("purge")
    if purge:
        if purge["abandon"]:
            print(f"  purge assets/ non effectuée : {purge['abandon']}")
        else:
            print(f"  purge assets/ : {len(purge['supprimes'])} orphelin(s) "
                  f"supprimé(s) ({purge['octets_liberes'] / 1e6:.1f} Mo), "
                  f"{len(purge['conserves'])} conservé(s) en sursis")
        for fichier, message in purge["echecs"].items():
            print(f"    - suppression impossible : {fichier} : {message}")
    if bilan["echecs"]:
        print(f"  {len(bilan['echecs'])} échec(s) :")
        for fichier, message in list(bilan["echecs"].items())[:10]:
            print(f"    - {fichier} : {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
