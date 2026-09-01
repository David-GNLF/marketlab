"""Indicateurs techniques vectorisés (pandas). Entrée : DataFrame OHLCV."""

import numpy as np
import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, window)
    std = close.rolling(window).std()
    upper, lower = mid + n_std * std, mid - n_std * std
    width = (upper - lower) / mid
    # position dans le canal : 0 = bande basse, 1 = bande haute
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
                         "bb_width": width, "bb_pct": pct_b})


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False).mean()


def volatility_annualized(close: pd.Series, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    return close.pct_change().rolling(window).std() * np.sqrt(periods_per_year)


def drawdown(close: pd.Series) -> pd.Series:
    return close / close.cummax() - 1


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute l'ensemble des indicateurs standard à un DataFrame OHLCV."""
    out = df.copy()
    c = out["close"]
    out["sma20"] = sma(c, 20)
    out["sma50"] = sma(c, 50)
    out["sma200"] = sma(c, 200)
    # Variantes exponentielles des mêmes fenêtres : plus réactives — c'est
    # sur elles que la candidate « règle d'école » se juge.
    out["ema20"] = ema(c, 20)
    out["ema50"] = ema(c, 50)
    out["ema200"] = ema(c, 200)
    out["rsi14"] = rsi(c)
    out = out.join(macd(c))
    out = out.join(bollinger(c))
    out["atr14"] = atr(out)
    out["vol20"] = volatility_annualized(c)
    out["ret_1d"] = c.pct_change()
    out["ret_20d"] = c.pct_change(20)
    out["ret_60d"] = c.pct_change(60)
    out["dd"] = drawdown(c)
    return out
