"""CLI du magasin intrajournalier MarketLab.

Exemples :
    python scripts/capturer_intraday.py                    # capture 5 min du périmètre
    python scripts/capturer_intraday.py --releve           # relevé quotidien de volatilité
    python scripts/capturer_intraday.py --releve --jours 60 --recalculer
                                                           # amorce l'historique (une fois)
    python scripts/capturer_intraday.py --etat             # ce qui est archivé

À SAVOIR SUR LA PROFONDEUR. Yahoo ne sert les barres 5 min que sur 60 jours
glissants (et les barres 1 min sur 7 jours). `--jours 60` est donc le maximum
utile pour amorcer l'historique : au-delà, la seule façon d'avoir plus profond
est le relevé quotidien conservé dans data_local/volatilite_realisee.csv.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlab import config, intraday


def capturer(interval: str, jours: int, symboles=None) -> int:
    bilan = intraday.capturer(list(symboles or config.SUIVIS),
                              interval=interval, jours=jours)
    print(f"Barres {interval} : {bilan['barres']} écrite(s) sur "
          f"{bilan['titres']} titre(s).")
    if bilan["ecartes"]:
        print(f"Sans intrajournalier (ignorés) : {', '.join(bilan['ecartes'])}")
    if bilan["echecs"]:
        print(f"Indisponibles : {', '.join(str(e) for e in bilan['echecs'][:12])}")
    return 0 if bilan["titres"] else 1


def relever(interval: str, jours: int, recalculer: bool, symboles=None) -> int:
    bilan = intraday.mettre_a_jour_releve(symboles, interval=interval, jours=jours,
                                          recalculer=recalculer)
    print(f"Volatilité réalisée : {bilan['mesures']} mesure(s) sur "
          f"{bilan['titres']} titre(s) ; {bilan['ajoutees']} ligne(s) ajoutée(s), "
          f"{bilan['total']} au total.")
    print(f"Relevé : {config.RV_PATH}")
    if bilan["echecs"]:
        print(f"Échecs : {', '.join(bilan['echecs'][:12])}")

    # Spread effectif (Roll) sur les MÊMES barres, dans la foulée : la capture
    # vient de les écrire, autant les relire tant qu'elles sont là. Aucun
    # appel réseau supplémentaire — c'est tout l'intérêt de la co-localiser.
    try:
        from marketlab import microstructure
        s = microstructure.mettre_a_jour_releve(symboles, interval=interval)
        print(f"Spread mesuré (Roll) : {s['titres']} titre(s), "
              f"{s['ajoutees']} séance(s) ajoutée(s), {s['total']} au total.")
    except Exception as exc:
        print(f"Spread non mesurable (non bloquant) : {str(exc)[:80]}")
    return 0


def etat(interval: str) -> int:
    releve = intraday.charger_releve()
    archives = [(s, intraday.journees_archivees(s, interval)) for s in config.SUIVIS]
    archives = [(s, j) for s, j in archives if j]
    print(f"Magasin {interval} : {len(archives)} titre(s) archivé(s).")
    for sym, jours in archives[:10]:
        print(f"  {sym:<12} {len(jours):>3} journée(s)  {jours[0]} → {jours[-1]}")
    if len(archives) > 10:
        print(f"  … et {len(archives) - 10} autre(s)")
    if releve.empty:
        print("Relevé de volatilité : vide.")
    else:
        print(f"Relevé de volatilité : {len(releve)} ligne(s), "
              f"{releve['symbole'].nunique()} titre(s), "
              f"{releve['date'].min()} → {releve['date'].max()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Magasin intrajournalier MarketLab")
    parser.add_argument("--releve", action="store_true",
                        help="calculer et conserver la volatilité réalisée des "
                             "séances terminées")
    parser.add_argument("--etat", action="store_true",
                        help="afficher ce qui est archivé, sans rien récupérer")
    parser.add_argument("--interval", default=intraday.INTERVALLE_DEFAUT,
                        help=f"intervalle des barres (défaut "
                             f"{intraday.INTERVALLE_DEFAUT})")
    parser.add_argument("--jours", type=int, default=None,
                        help="profondeur en jours (défaut : 2 en capture, "
                             "5 en relevé ; 60 maximum en 5 min chez Yahoo)")
    parser.add_argument("--recalculer", action="store_true",
                        help="réécrire les journées déjà relevées (par défaut "
                             "l'historique est immuable)")
    parser.add_argument("--symboles", nargs="*", default=None,
                        help="restreindre à ces symboles (défaut : config.SUIVIS)")
    args = parser.parse_args()

    if args.etat:
        return etat(args.interval)
    if args.releve:
        return relever(args.interval, args.jours or intraday.JOURS_RELEVE,
                       args.recalculer, args.symboles)
    return capturer(args.interval, args.jours or intraday.JOURS_VEILLE, args.symboles)


if __name__ == "__main__":
    raise SystemExit(main())
