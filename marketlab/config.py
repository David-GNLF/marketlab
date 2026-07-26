"""Configuration centrale : chemins et univers de titres suivis."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
DATA_DIR = ROOT / "data_local"  # fichiers importés à la main (ex. CSV BRVM)

CACHE_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# Durée de validité du cache en heures, par intervalle de bougie.
CACHE_TTL_HOURS = {"1d": 12, "1h": 2, "1wk": 48}

# ---------------------------------------------------------------------------
# Univers suivis (watchlists). Modifier librement : ce sont les listes que le
# screener et le dashboard balayent par défaut.
# ---------------------------------------------------------------------------

ACTIONS_US = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "V", "UNH", "XOM", "KO", "PG", "COST",
]

ACTIONS_EU = [
    "MC.PA",    # LVMH
    "TTE.PA",   # TotalEnergies
    "SAN.PA",   # Sanofi
    "AIR.PA",   # Airbus
    "BNP.PA",   # BNP Paribas
    "ASML.AS",  # ASML
    "SAP.DE",   # SAP
    "SIE.DE",   # Siemens
]

INDICES = ["^GSPC", "^NDX", "^FCHI", "^GDAXI", "^STOXX50E"]

FOREX = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "AUDUSD=X"]

CRYPTO = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

# BRVM : symboles officiels (données via import CSV ou scraping best-effort)
BRVM = ["SNTS", "SGBC", "BOAB", "ETIT", "ONTBF", "PALC"]

UNIVERS = {
    "Actions US": ACTIONS_US,
    "Actions EU": ACTIONS_EU,
    "Indices": INDICES,
    "Forex": FOREX,
    "Crypto": CRYPTO,
    "BRVM": BRVM,
}

# Séries FRED clés pour le tableau macro (id FRED -> libellé)
FRED_SERIES = {
    "CPIAUCSL": "Inflation US (CPI, indice)",
    "FEDFUNDS": "Taux directeur Fed (%)",
    "UNRATE": "Chômage US (%)",
    "T10Y2Y": "Courbe des taux 10a-2a (pts)",
    "DGS10": "Taux 10 ans US (%)",
    "DTWEXBGS": "Dollar index (large)",
    "VIXCLS": "VIX (volatilité implicite)",
}
