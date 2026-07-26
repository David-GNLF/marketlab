"""Signaux techniques et score composite par titre.

Le score composite va de -100 (très baissier) à +100 (très haussier).
C'est une synthèse d'indicateurs, PAS une prédiction : son intérêt se mesure
au backtest, et il doit toujours être croisé avec le contexte macro.
"""

import pandas as pd

from marketlab import indicators


def compute_signals(df: pd.DataFrame) -> dict:
    """Évalue les signaux sur la dernière bougie d'un DataFrame enrichi."""
    if "sma50" not in df.columns:
        df = indicators.enrich(df)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    signaux: dict[str, float] = {}

    # Tendance : prix vs moyennes mobiles (poids 40)
    trend = 0.0
    if pd.notna(last["sma200"]):
        trend += 15 if last["close"] > last["sma200"] else -15
    if pd.notna(last["sma50"]):
        trend += 15 if last["close"] > last["sma50"] else -15
    if pd.notna(last["sma50"]) and pd.notna(last["sma200"]):
        trend += 10 if last["sma50"] > last["sma200"] else -10  # golden/death cross
    signaux["tendance"] = trend

    # Momentum : RSI (poids 25) — surachat/survente en contrarien
    r = last["rsi14"]
    if pd.isna(r):
        momentum = 0.0
    elif r >= 70:
        momentum = -25.0 * min((r - 70) / 15, 1)
    elif r <= 30:
        momentum = 25.0 * min((30 - r) / 15, 1)
    else:
        momentum = (r - 50) / 20 * 12  # zone neutre : pente douce
    signaux["momentum_rsi"] = round(momentum, 1)

    # MACD : signe et croisement de l'histogramme (poids 20)
    m = 0.0
    if pd.notna(last["hist"]):
        m += 10 if last["hist"] > 0 else -10
        if pd.notna(prev["hist"]) and last["hist"] * prev["hist"] < 0:
            m += 10 if last["hist"] > 0 else -10  # croisement tout frais
    signaux["macd"] = m

    # Bollinger : position dans le canal (poids 15) — extrêmes en contrarien
    b = last["bb_pct"]
    if pd.isna(b):
        boll = 0.0
    elif b > 1:
        boll = -15.0
    elif b < 0:
        boll = 15.0
    else:
        boll = (0.5 - b) * 10
    signaux["bollinger"] = round(boll, 1)

    score = sum(signaux.values())
    return {
        "score": round(max(-100.0, min(100.0, score)), 1),
        "signaux": signaux,
        "close": round(float(last["close"]), 4),
        "rsi14": round(float(r), 1) if pd.notna(r) else None,
        "ret_20d": round(float(last["ret_20d"]) * 100, 2) if pd.notna(last["ret_20d"]) else None,
        "vol20": round(float(last["vol20"]) * 100, 1) if pd.notna(last["vol20"]) else None,
        "drawdown": round(float(last["dd"]) * 100, 1) if pd.notna(last["dd"]) else None,
    }


def label(score: float) -> str:
    if score >= 40:
        return "Achat fort"
    if score >= 15:
        return "Achat"
    if score > -15:
        return "Neutre"
    if score > -40:
        return "Vente"
    return "Vente forte"
