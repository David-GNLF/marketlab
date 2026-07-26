"""Test de bout en bout : données, indicateurs, signaux, macro, backtest.

Lancer :  python scripts/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from marketlab import backtest, indicators, macro, signals
from marketlab.data import get_ohlcv

pd.set_option("display.width", 160)


def main() -> int:
    ok = True

    print("=== 1. Données multi-marchés ===")
    for sym in ("AAPL", "MC.PA", "EURUSD=X", "BTCUSDT"):
        try:
            df = get_ohlcv(sym)
            print(f"  {sym:10s} {len(df):4d} bougies, dernière {df.index[-1].date()} "
                  f"close={df['close'].iloc[-1]:.4f}")
        except Exception as exc:
            ok = False
            print(f"  {sym:10s} ECHEC: {exc}")

    print("\n=== 2. Signaux (AAPL) ===")
    try:
        df = indicators.enrich(get_ohlcv("AAPL"))
        sig = signals.compute_signals(df)
        print(f"  score={sig['score']} ({signals.label(sig['score'])})  "
              f"détail={sig['signaux']}")
    except Exception as exc:
        ok = False
        print(f"  ECHEC: {exc}")

    print("\n=== 3. Macro (FRED) ===")
    try:
        reg = macro.regime()
        print(f"  régime: {reg['lecture']} (score {reg['score']})")
        for note in reg["notes"]:
            print(f"   - {note}")
    except Exception as exc:
        ok = False
        print(f"  ECHEC: {exc}")

    print("\n=== 4. Backtest BTCUSDT (2 ans, 4 stratégies) ===")
    try:
        df = get_ohlcv("BTCUSDT")
        print(backtest.compare(df).to_string())
    except Exception as exc:
        ok = False
        print(f"  ECHEC: {exc}")

    print("\n" + ("TOUT EST OK" if ok else "DES ETAPES ONT ECHOUE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
