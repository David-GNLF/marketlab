"""Crypto spot via l'API publique Binance (REST, sans clé).

Deux points d'accès : l'API principale, puis le miroir officiel de données
publiques `data-api.binance.vision`, prévu par Binance pour les régions où
l'API principale répond 451 — c'est le cas des runners GitHub Actions,
hébergés aux États-Unis.
"""

import datetime as dt

import pandas as pd
import requests

from marketlab.data import base

BASES = [
    "https://api.binance.com/api/v3/klines",
    "https://data-api.binance.vision/api/v3/klines",
]
INTERVAL_MAP = {"1d": "1d", "1h": "1h", "1wk": "1w"}
_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
]

# mémorise le point d'accès qui fonctionne pour ne pas repayer un 451 par appel
_base_active = [BASES[0]]


def _requete(params: dict) -> list:
    dernier = None
    ordre = [_base_active[0]] + [b for b in BASES if b != _base_active[0]]
    for api in ordre:
        try:
            resp = requests.get(api, params=params, timeout=20)
            resp.raise_for_status()
            _base_active[0] = api
            return resp.json()
        except requests.HTTPError as exc:
            dernier = exc
            if exc.response is not None and exc.response.status_code == 451:
                continue  # région bloquée : essayer le miroir
            raise
    raise dernier


def get_ohlcv(symbol: str, interval: str = "1d", lookback_days: int = 730) -> pd.DataFrame:
    cached = base.load_cached("binance", symbol, interval, lookback_days)
    if cached is not None:
        return cached

    start_ms = int(
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)).timestamp() * 1000
    )
    rows: list[list] = []
    while True:
        batch = _requete({
            "symbol": symbol,
            "interval": INTERVAL_MAP.get(interval, "1d"),
            "startTime": start_ms,
            "limit": 1000,
        })
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start_ms = batch[-1][6] + 1  # close_time de la dernière bougie + 1 ms

    if not rows:
        raise RuntimeError(f"Aucune donnée Binance pour {symbol}")

    df = pd.DataFrame(rows, columns=_KLINE_COLS)
    df.index = pd.to_datetime(df["open_time"], unit="ms")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = base.normalize(df)
    base.save_cache(df, "binance", symbol, interval, lookback_days)
    return df
