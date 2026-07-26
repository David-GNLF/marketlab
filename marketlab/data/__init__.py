"""Fournisseurs de données. Point d'entrée unique : get_ohlcv()."""

import pandas as pd

from marketlab import config
from marketlab.data import binance, brvm, premium, yahoo


def get_ohlcv(symbol: str, interval: str = "1d", lookback_days: int = 730) -> pd.DataFrame:
    """Route un symbole vers le bon fournisseur et renvoie un DataFrame OHLCV
    normalisé (index datetime, colonnes open/high/low/close/volume).

    Si une clé Twelve Data est configurée, elle est tentée en premier sur son
    périmètre (actions US, forex) ; tout échec retombe sur Yahoo."""
    if symbol in config.CRYPTO or symbol.endswith("USDT"):
        return binance.get_ohlcv(symbol, interval, lookback_days)
    if symbol in config.BRVM:
        return brvm.get_ohlcv(symbol)
    if premium.api_key() and premium.couvre(symbol):
        try:
            return premium.get_ohlcv(symbol, interval, lookback_days)
        except Exception:
            pass  # le premium améliore, ne conditionne jamais
    return yahoo.get_ohlcv(symbol, interval, lookback_days)
