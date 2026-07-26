"""Crypto spot via l'API publique Binance (REST, sans clé)."""

import datetime as dt

import pandas as pd
import requests

from marketlab.data import base

API = "https://api.binance.com/api/v3/klines"
INTERVAL_MAP = {"1d": "1d", "1h": "1h", "1wk": "1w"}
_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
]


def get_ohlcv(symbol: str, interval: str = "1d", lookback_days: int = 730) -> pd.DataFrame:
    cached = base.load_cached("binance", symbol, interval, lookback_days)
    if cached is not None:
        return cached

    start_ms = int(
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)).timestamp() * 1000
    )
    rows: list[list] = []
    while True:
        resp = requests.get(
            API,
            params={
                "symbol": symbol,
                "interval": INTERVAL_MAP.get(interval, "1d"),
                "startTime": start_ms,
                "limit": 1000,
            },
            timeout=20,
        )
        resp.raise_for_status()
        batch = resp.json()
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
