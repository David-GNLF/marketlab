"""Screener : balaye un univers de titres et classe par score composite."""

import pandas as pd

from marketlab import indicators, signals
from marketlab.data import get_ohlcv


def scan(symbols: list[str], interval: str = "1d") -> pd.DataFrame:
    """Renvoie un tableau (1 ligne par titre) trié du plus haussier au plus
    baissier. Les titres dont les données échouent sont listés avec l'erreur."""
    rows = []
    for sym in symbols:
        try:
            df = indicators.enrich(get_ohlcv(sym, interval))
            sig = signals.compute_signals(df)
            rows.append({
                "symbole": sym,
                "score": sig["score"],
                "avis": signals.label(sig["score"]),
                "cours": sig["close"],
                "rsi14": sig["rsi14"],
                "perf_20j_%": sig["ret_20d"],
                "vol_ann_%": sig["vol20"],
                "drawdown_%": sig["drawdown"],
                "erreur": "",
            })
        except Exception as exc:  # un titre en échec ne bloque pas le scan
            rows.append({"symbole": sym, "score": None, "avis": "-", "cours": None,
                         "rsi14": None, "perf_20j_%": None, "vol_ann_%": None,
                         "drawdown_%": None, "erreur": str(exc)[:80]})
    out = pd.DataFrame(rows)
    return out.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)
