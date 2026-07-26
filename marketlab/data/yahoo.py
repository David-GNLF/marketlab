"""Actions US/EU, indices et forex via Yahoo Finance (yfinance, sans clé)."""

import datetime as dt

import pandas as pd
import yfinance as yf

from marketlab.data import base


def get_ohlcv(symbol: str, interval: str = "1d", lookback_days: int = 730) -> pd.DataFrame:
    cached = base.load_cached("yahoo", symbol, interval, lookback_days)
    if cached is not None:
        return cached

    start = dt.date.today() - dt.timedelta(days=lookback_days)
    df = yf.download(
        symbol,
        start=start.isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"Aucune donnée Yahoo pour {symbol}")
    # yfinance renvoie des colonnes MultiIndex (champ, ticker) même pour un seul titre
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = base.normalize(df)
    base.save_cache(df, "yahoo", symbol, interval, lookback_days)
    return df
