"""Score composite calculé sur tout l'historique + mesure de son pouvoir prédictif.

Reproduit exactement les règles de signals.compute_signals mais en vectorisé,
pour obtenir la série temporelle du score, puis mesure ce que le score aurait
« prédit » : corrélation de rang (IC de Spearman) entre le score du jour et le
rendement des `horizon` jours suivants, et écart de rendement entre les
quintiles extrêmes. Un IC durablement proche de 0 = score sans valeur
prédictive à cet horizon, quelle que soit son apparente logique.
"""

import numpy as np
import pandas as pd

from marketlab import indicators
from marketlab.data import get_ohlcv


def score_series(df: pd.DataFrame) -> pd.Series:
    """Score composite (-100..100) pour chaque bougie de l'historique."""
    if "sma200" not in df.columns:
        df = indicators.enrich(df)
    c, s50, s200 = df["close"], df["sma50"], df["sma200"]

    tendance = (
        np.where(s200.notna(), np.where(c > s200, 15.0, -15.0), 0.0)
        + np.where(s50.notna(), np.where(c > s50, 15.0, -15.0), 0.0)
        + np.where(s50.notna() & s200.notna(), np.where(s50 > s200, 10.0, -10.0), 0.0)
    )

    r = df["rsi14"]
    momentum = np.where(
        r >= 70, -25.0 * np.minimum((r - 70) / 15, 1),
        np.where(r <= 30, 25.0 * np.minimum((30 - r) / 15, 1), (r - 50) / 20 * 12),
    )
    momentum = np.where(r.notna(), momentum, 0.0)

    h, h_prev = df["hist"], df["hist"].shift()
    macd = np.where(h.notna(), np.where(h > 0, 10.0, -10.0), 0.0)
    cross = h.notna() & h_prev.notna() & (h * h_prev < 0)
    macd = macd + np.where(cross, np.where(h > 0, 10.0, -10.0), 0.0)

    b = df["bb_pct"]
    boll = np.where(b > 1, -15.0, np.where(b < 0, 15.0, (0.5 - b) * 10))
    boll = np.where(b.notna(), boll, 0.0)

    return pd.Series(tendance + momentum + macd + boll, index=df.index,
                     name="score").clip(-100, 100)


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Corrélation de rang sans dépendre de scipy."""
    return float(a.rank().corr(b.rank()))


def predictive_power(symbol: str, horizon: int = 10,
                     lookback_days: int = 1825) -> dict:
    """Mesure le pouvoir prédictif du score sur un titre.

    Renvoie l'IC de Spearman, le rendement moyen futur par quintile de score
    et l'écart Q5-Q1 (annualisé grossièrement). horizon en bougies.
    """
    df = indicators.enrich(get_ohlcv(symbol, lookback_days=lookback_days))
    sc = score_series(df)
    fwd = df["close"].pct_change(horizon).shift(-horizon)
    data = pd.DataFrame({"score": sc, "fwd": fwd}).dropna()
    if len(data) < 120:
        raise RuntimeError(f"Historique insuffisant pour {symbol} ({len(data)} points)")

    ic = _spearman(data["score"], data["fwd"])
    try:
        data["quintile"] = pd.qcut(data["score"], 5, labels=[1, 2, 3, 4, 5])
        par_quintile = data.groupby("quintile", observed=True)["fwd"].mean() * 100
        spread = float(par_quintile.get(5, np.nan) - par_quintile.get(1, np.nan))
    except ValueError:  # scores trop concentrés pour 5 quintiles
        par_quintile, spread = pd.Series(dtype=float), float("nan")

    return {
        "symbole": symbol,
        "n_points": len(data),
        "horizon_j": horizon,
        "ic_spearman": round(ic, 3),
        "quintiles_%": {str(k): round(float(v), 2) for k, v in par_quintile.items()},
        "spread_q5_q1_%": round(spread, 2) if spread == spread else None,
    }


def ic_table(symbols: list[str], horizon: int = 10) -> pd.DataFrame:
    """IC par titre pour un panier — vision d'ensemble de la valeur du score."""
    rows = []
    for sym in symbols:
        try:
            r = predictive_power(sym, horizon)
            rows.append({"symbole": sym, "ic": r["ic_spearman"],
                         "spread_q5_q1_%": r["spread_q5_q1_%"],
                         "n_points": r["n_points"]})
        except Exception as exc:
            rows.append({"symbole": sym, "ic": None, "spread_q5_q1_%": None,
                         "n_points": 0, "erreur": str(exc)[:60]})
    return pd.DataFrame(rows).sort_values("ic", ascending=False,
                                          na_position="last").reset_index(drop=True)
