"""Cache disque parquet partagé par tous les fournisseurs."""

import time
from pathlib import Path

import pandas as pd

from marketlab import config

COLUMNS = ["open", "high", "low", "close", "volume"]


def _cache_path(provider: str, symbol: str, interval: str, lookback_days: int) -> Path:
    safe = symbol.replace("=", "_").replace("^", "IDX_").replace(".", "_").replace("/", "_")
    # la profondeur demandée fait partie de la clé : demander 5 ans ne doit
    # jamais renvoyer un cache de 2 ans
    return config.CACHE_DIR / f"{provider}_{safe}_{interval}_{lookback_days}.parquet"


def load_cached(provider: str, symbol: str, interval: str,
                lookback_days: int) -> pd.DataFrame | None:
    path = _cache_path(provider, symbol, interval, lookback_days)
    if not path.exists():
        return None
    ttl_h = config.CACHE_TTL_HOURS.get(interval, 12)
    if time.time() - path.stat().st_mtime > ttl_h * 3600:
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def save_cache(df: pd.DataFrame, provider: str, symbol: str, interval: str,
               lookback_days: int) -> None:
    try:
        df.to_parquet(_cache_path(provider, symbol, interval, lookback_days))
    except Exception:
        pass  # le cache est un confort, jamais bloquant


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Colonnes en minuscules, index datetime trié, lignes vides retirées."""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    df = df[[c for c in COLUMNS if c in df.columns]]
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index().dropna(subset=["close"])
