"""Backtest vectorisé simple, long-only, une position à la fois.

Une stratégie est une fonction (DataFrame enrichi) -> Series de positions
(1 = investi, 0 = cash), décalée automatiquement d'une bougie pour éviter le
biais de look-ahead (on décide à la clôture, on est exposé le lendemain).
"""

from collections.abc import Callable

import numpy as np
import pandas as pd

from marketlab import indicators

Strategy = Callable[[pd.DataFrame], pd.Series]


# --- Stratégies de référence ------------------------------------------------

def strat_buy_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


def strat_sma_cross(df: pd.DataFrame) -> pd.Series:
    """Investi quand SMA50 > SMA200 (tendance haussière confirmée)."""
    return (df["sma50"] > df["sma200"]).astype(float)


def strat_rsi_rebound(df: pd.DataFrame) -> pd.Series:
    """Achat en survente (RSI<30), sortie en surachat (RSI>60)."""
    pos = pd.Series(np.nan, index=df.index)
    pos[df["rsi14"] < 30] = 1.0
    pos[df["rsi14"] > 60] = 0.0
    return pos.ffill().fillna(0.0)


def strat_macd(df: pd.DataFrame) -> pd.Series:
    return (df["hist"] > 0).astype(float)


STRATEGIES: dict[str, Strategy] = {
    "Buy & Hold": strat_buy_hold,
    "Croisement SMA 50/200": strat_sma_cross,
    "Rebond RSI 30/60": strat_rsi_rebound,
    "MACD > 0": strat_macd,
}


# --- Moteur -----------------------------------------------------------------

def run(df: pd.DataFrame, strategy: Strategy, fee_bps: float = 10.0,
        periods_per_year: int = 252) -> dict:
    """Exécute la stratégie et renvoie métriques + courbe d'équité.

    fee_bps : coût aller simple en points de base appliqué à chaque changement
    de position (frais + slippage ; 10 bps = 0,10 %).
    """
    if "sma200" not in df.columns:
        df = indicators.enrich(df)

    pos = strategy(df).clip(0, 1).shift(1).fillna(0.0)  # exposé à la bougie suivante
    rets = df["close"].pct_change().fillna(0.0)
    trades = pos.diff().abs().fillna(0.0)
    strat_rets = pos * rets - trades * fee_bps / 10_000

    equity = (1 + strat_rets).cumprod()
    n = len(equity)
    years = n / periods_per_year
    total = float(equity.iloc[-1]) - 1
    cagr = float(equity.iloc[-1]) ** (1 / years) - 1 if years > 0 else 0.0
    vol = float(strat_rets.std()) * np.sqrt(periods_per_year)
    sharpe = (float(strat_rets.mean()) * periods_per_year) / vol if vol > 0 else 0.0
    max_dd = float((equity / equity.cummax() - 1).min())
    exposure = float(pos.mean())
    n_trades = int((trades > 0).sum())

    return {
        "equity": equity,
        "positions": pos,
        "metrics": {
            "rendement_total_%": round(total * 100, 1),
            "cagr_%": round(cagr * 100, 2),
            "vol_annuelle_%": round(vol * 100, 1),
            "sharpe": round(sharpe, 2),
            "max_drawdown_%": round(max_dd * 100, 1),
            "exposition_%": round(exposure * 100, 0),
            "nb_transactions": n_trades,
        },
    }


def compare(df: pd.DataFrame, strategies: dict[str, Strategy] | None = None) -> pd.DataFrame:
    """Compare toutes les stratégies sur un même titre (tableau de métriques)."""
    strategies = strategies or STRATEGIES
    df = indicators.enrich(df)
    rows = {name: run(df, strat)["metrics"] for name, strat in strategies.items()}
    return pd.DataFrame(rows).T
