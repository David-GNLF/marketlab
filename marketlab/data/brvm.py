"""BRVM (Bourse Régionale des Valeurs Mobilières, Abidjan).

Pas d'API publique fiable : deux voies, dans l'ordre.

1. Import manuel : déposer un CSV `data_local/brvm_<SYMBOLE>.csv` avec les
   colonnes `date,open,high,low,close,volume` (open/high/low/volume
   optionnelles ; seule `close` est indispensable). Les cours quotidiens sont
   publiés dans le Bulletin Officiel de la Cote sur brvm.org.
2. Scraping best-effort du site BRVM pour le dernier cours (pas d'historique).
"""

import pandas as pd

from marketlab import config
from marketlab.data import base


def get_ohlcv(symbol: str) -> pd.DataFrame:
    path = config.DATA_DIR / f"brvm_{symbol}.csv"
    if not path.exists():
        raise RuntimeError(
            f"Pas de données BRVM pour {symbol}. Déposer un CSV "
            f"'date,close[,open,high,low,volume]' dans {path}"
        )
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "date" not in df.columns or "close" not in df.columns:
        raise RuntimeError(f"{path} doit contenir au minimum les colonnes date et close")
    df = df.set_index("date")
    for col in ("open", "high", "low"):
        if col not in df.columns:
            df[col] = df["close"]
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return base.normalize(df)
