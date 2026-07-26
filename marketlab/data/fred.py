"""Séries macro US via le CSV public de FRED (fredgraph, sans clé API)."""

import io
import time

import pandas as pd
import requests

from marketlab import config

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def get_series(series_id: str, lookback_years: int = 10) -> pd.Series:
    """Renvoie une série FRED (index datetime, valeurs float)."""
    cache = config.CACHE_DIR / f"fred_{series_id}.csv"
    if cache.exists() and time.time() - cache.stat().st_mtime < 24 * 3600:
        text = cache.read_text(encoding="utf-8")
    else:
        resp = requests.get(URL, params={"id": series_id}, timeout=20)
        resp.raise_for_status()
        text = resp.text
        cache.write_text(text, encoding="utf-8")

    df = pd.read_csv(io.StringIO(text))
    date_col, value_col = df.columns[0], df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    s = df.set_index(date_col)[value_col].dropna()
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=lookback_years)
    return s[s.index >= cutoff]
