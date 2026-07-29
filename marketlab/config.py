"""Configuration centrale : chemins et univers de titres suivis."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
DATA_DIR = ROOT / "data_local"  # fichiers importés à la main (ex. CSV BRVM)

CACHE_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# Durée de validité du cache en heures, par intervalle de bougie.
#
# 12 h convient à la publication quotidienne : la dernière barre du jour est
# relue une fois, cela suffit. Mais la VEILLE des alertes rebalaye plusieurs
# fois par heure — avec ce délai, elle relirait le même instantané et ne
# détecterait jamais rien. D'où la surcharge par variable d'environnement :
# la veille abaisse le délai des barres quotidiennes juste sous son propre
# intervalle. C'est un compromis assumé, la contrepartie étant davantage de
# requêtes chez des fournisseurs gratuits qu'il ne faut pas épuiser.
CACHE_TTL_HOURS = {
    "1d": float(os.environ.get("MARKETLAB_TTL_1D_H", 12)),
    "1h": float(os.environ.get("MARKETLAB_TTL_1H_H", 2)),
    "1wk": 48,
}

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

ACTIONS_ASIE = [
    "7203.T",    # Toyota
    "6758.T",    # Sony
    "9984.T",    # SoftBank
    "0700.HK",   # Tencent
    "9988.HK",   # Alibaba
    "2330.TW",   # TSMC
    "005930.KS", # Samsung
]

INDICES = ["^GSPC", "^NDX", "^FCHI", "^GDAXI", "^STOXX50E",
           "^N225", "^HSI", "^KS11"]

FOREX = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", "AUDUSD=X",
         "USDCAD=X", "NZDUSD=X"]

CRYPTO = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

# Contrats à terme Yahoo (suffixe =F). Cacao et coton figurent en bonne place :
# matières clés pour l'Afrique de l'Ouest. NB : le franc CFA (XOF) étant
# arrimé à l'euro à parité fixe (655,957), suivre EUR/USD revient à suivre
# XOF/USD.
MATIERES = ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F",
            "CC=F", "CT=F", "KC=F", "ZW=F"]

# Libellés lisibles (affichage site + requêtes d'actualités)
NOMS_ACTIFS = {
    "GC=F": "Or", "SI=F": "Argent", "CL=F": "Pétrole WTI", "BZ=F": "Brent",
    "NG=F": "Gaz naturel", "HG=F": "Cuivre", "CC=F": "Cacao", "CT=F": "Coton",
    "KC=F": "Café", "ZW=F": "Blé",
    "EURUSD=X": "EUR/USD (≈ USD/XOF inversé)", "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY", "USDCHF=X": "USD/CHF", "AUDUSD=X": "AUD/USD",
    "USDCAD=X": "USD/CAD", "NZDUSD=X": "NZD/USD",
    "7203.T": "Toyota", "6758.T": "Sony", "9984.T": "SoftBank",
    "0700.HK": "Tencent", "9988.HK": "Alibaba", "2330.TW": "TSMC",
    "005930.KS": "Samsung",
    "^N225": "Nikkei 225", "^HSI": "Hang Seng", "^KS11": "KOSPI",
}

# BRVM : symboles officiels (données via import CSV ou scraping best-effort)
BRVM = ["SNTS", "SGBC", "BOAB", "ETIT", "ONTBF", "PALC"]

UNIVERS = {
    "Actions US": ACTIONS_US,
    "Actions EU": ACTIONS_EU,
    "Actions Asie": ACTIONS_ASIE,
    "Indices": INDICES,
    "Forex": FOREX,
    "Matières premières": MATIERES,
    "Crypto": CRYPTO,
    "BRVM": BRVM,
}

# ---------------------------------------------------------------------------
# SOURCES UNIQUES DE VÉRITÉ pour les périmètres. Tout module qui balaye des
# actifs DOIT partir d'ici — jamais d'une liste locale recopiée : c'est ainsi
# que le screener avait perdu les matières premières et l'Asie.
# ---------------------------------------------------------------------------

# Tous les actifs analysables automatiquement (BRVM exclue : données manuelles)
SUIVIS = (ACTIONS_US + ACTIONS_EU + ACTIONS_ASIE + INDICES + FOREX
          + MATIERES + CRYPTO)

# Actifs disposant d'une salle de marché complète (fiche + verdict + robot).
# Sous-ensemble volontaire : chaque fiche coûte ~1 min de calcul au CI.
FICHES = (ACTIONS_US[:8] + ACTIONS_EU[:4]
          + ["7203.T", "0700.HK", "2330.TW", "005930.KS"]   # Asie représentative
          + CRYPTO[:3] + FOREX + MATIERES)

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
