"""Connecteur données premium (Twelve Data) — optionnel, activé par clé API.

Configuration : créer `data_local/providers.json` :

    {"twelvedata_api_key": "VOTRE_CLE"}

(offre gratuite sur twelvedata.com : 8 requêtes/min, 800/jour — suffisant en
complément ; les plans payants montent en fréquence et en profondeur.)

Périmètre : actions US et forex (symboles Yahoo convertis automatiquement,
ex. EURUSD=X → EUR/USD). Actions EU, crypto et BRVM restent sur leurs
fournisseurs dédiés. Sans clé, ou en cas d'erreur, le routeur retombe
silencieusement sur Yahoo — la clé améliore, ne conditionne jamais.
"""

import datetime as dt
import json

import pandas as pd
import requests

from marketlab import config
from marketlab.data import base

API = "https://api.twelvedata.com/time_series"
CONFIG_PATH = config.DATA_DIR / "providers.json"
INTERVAL_MAP = {"1d": "1day", "1h": "1h", "1wk": "1week"}


def api_key() -> str | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get(
            "twelvedata_api_key") or None
    except Exception:
        return None


def couvre(symbol: str) -> bool:
    """True si le symbole est dans le périmètre Twelve Data (US + forex)."""
    if symbol.endswith("USDT") or symbol in config.BRVM:
        return False
    if symbol.endswith((".PA", ".DE", ".AS")) or symbol.startswith("^"):
        return False
    return True


def _td_symbol(symbol: str) -> str:
    if symbol.endswith("=X"):  # EURUSD=X -> EUR/USD
        paire = symbol[:-2]
        return f"{paire[:3]}/{paire[3:]}"
    return symbol


def get_ohlcv(symbol: str, interval: str = "1d", lookback_days: int = 730) -> pd.DataFrame:
    key = api_key()
    if key is None:
        raise RuntimeError("Twelve Data non configuré (data_local/providers.json)")
    cached = base.load_cached("twelvedata", symbol, interval, lookback_days)
    if cached is not None:
        return cached

    start = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    resp = requests.get(API, params={
        "symbol": _td_symbol(symbol),
        "interval": INTERVAL_MAP.get(interval, "1day"),
        "start_date": start,
        "outputsize": 5000,
        "apikey": key,
    }, timeout=25)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") == "error" or "values" not in payload:
        raise RuntimeError(f"Twelve Data : {payload.get('message', 'réponse invalide')}")

    df = pd.DataFrame(payload["values"])
    df = df.rename(columns={"datetime": "date"}).set_index("date")
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = base.normalize(df)
    base.save_cache(df, "twelvedata", symbol, interval, lookback_days)
    return df
