"""Historise le scan du jour (scores + avis) dans data_local/historique_scores.csv.

À planifier quotidiennement (après clôture US, ex. 22h30 heure Bénin) :
    schtasks /Create /SC DAILY /ST 22:30 /TN "MarketLab historisation" ^
      /TR "python C:\\Users\\Dav\\Downloads\\PROJET\\claude\\marketlab\\scripts\\historiser.py"

Ce fichier accumule les scores RÉELLEMENT émis jour après jour : c'est la
vérité terrain pour vérifier plus tard leur pouvoir prédictif hors historique.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from marketlab import config, screener

CSV = config.DATA_DIR / "historique_scores.csv"
UNIVERS = ["Actions US", "Actions EU", "Indices", "Forex", "Crypto"]


def main() -> int:
    symbols = [s for u in UNIVERS for s in config.UNIVERS[u]]
    table = screener.scan(symbols)
    table.insert(0, "date", pd.Timestamp.today().date().isoformat())

    if CSV.exists():
        histo = pd.read_csv(CSV)
        histo = pd.concat([histo, table], ignore_index=True)
        histo = histo.drop_duplicates(subset=["date", "symbole"], keep="last")
    else:
        histo = table
    histo.to_csv(CSV, index=False)
    ok = table["score"].notna().sum()
    print(f"{ok}/{len(table)} scores historisés pour le {table['date'].iloc[0]} → {CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
