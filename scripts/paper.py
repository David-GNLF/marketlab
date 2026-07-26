"""CLI du paper trading.

Exemples :
    python scripts/paper.py init --capital 10000
    python scripts/paper.py acheter AAPL 1500
    python scripts/paper.py vendre AAPL            # tout
    python scripts/paper.py vendre AAPL --qty 2
    python scripts/paper.py etat
    python scripts/paper.py historique
    python scripts/paper.py auto --dry-run         # signaux du screener, simulés
    python scripts/paper.py auto                   # exécution papier réelle
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from marketlab import paper

pd.set_option("display.width", 160)


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper trading MarketLab")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="créer/réinitialiser le portefeuille")
    p_init.add_argument("--capital", type=float, default=10_000)

    p_buy = sub.add_parser("acheter", help="acheter pour un montant en USD")
    p_buy.add_argument("symbole")
    p_buy.add_argument("montant", type=float)

    p_sell = sub.add_parser("vendre", help="vendre (tout par défaut)")
    p_sell.add_argument("symbole")
    p_sell.add_argument("--qty", type=float, default=None)

    sub.add_parser("etat", help="valorisation et P&L")
    sub.add_parser("historique", help="liste des transactions")

    p_auto = sub.add_parser("auto", help="exécuter les signaux du screener en papier")
    p_auto.add_argument("--dry-run", action="store_true")
    p_auto.add_argument("--univers", nargs="*", default=None)

    args = parser.parse_args()
    try:
        if args.cmd == "init":
            if paper.PORTFOLIO_PATH.exists():
                rep = input(f"Écraser le portefeuille existant "
                            f"({paper.PORTFOLIO_PATH.name}) ? [o/N] ")
                if rep.strip().lower() != "o":
                    print("Abandon.")
                    return 1
            paper.init(args.capital)
            print(f"Portefeuille papier créé : {args.capital:.2f} USD")
        elif args.cmd == "acheter":
            t = paper.acheter(args.symbole.upper(), args.montant)
            print(f"ACHAT {t['symbole']} : {t['qty']} @ {t['prix_usd']} USD "
                  f"= {t['montant_usd']} USD")
        elif args.cmd == "vendre":
            t = paper.vendre(args.symbole.upper(), args.qty)
            print(f"VENTE {t['symbole']} : {t['qty']} @ {t['prix_usd']} USD "
                  f"= {t['montant_usd']} USD (PnL {t['pnl_usd']} USD)")
        elif args.cmd == "etat":
            e = paper.etat()
            print(f"Valeur totale : {e['valeur_totale_usd']} USD "
                  f"({e['perf_totale_%']:+.2f} %) — cash {e['cash_usd']} USD")
            if len(e["positions"]):
                print(e["positions"].to_string(index=False))
            else:
                print("(aucune position)")
        elif args.cmd == "historique":
            pf = paper.load()
            if pf["transactions"]:
                print(pd.DataFrame(pf["transactions"]).to_string(index=False))
            else:
                print("(aucune transaction)")
        elif args.cmd == "auto":
            for ligne in paper.auto(universes=args.univers, dry_run=args.dry_run):
                print("• " + ligne)
    except RuntimeError as exc:
        print(f"Erreur : {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
