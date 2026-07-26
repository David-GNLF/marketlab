"""Actualités et sentiment — Google News RSS (sans clé) + lexique financier.

Le score de sentiment est lexical (comptage de mots positifs/négatifs FR+EN
dans les titres) : c'est un thermomètre d'ambiance grossier, utile pour
repérer un flux d'actualités anormalement négatif, pas une analyse fine.
"""

import json
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from marketlab import config

RSS = "https://news.google.com/rss/search"
CACHE_TTL_S = 3600

POSITIFS = {
    # EN
    "surge", "soar", "soars", "rally", "rallies", "jump", "jumps", "beat", "beats",
    "record", "upgrade", "upgraded", "growth", "profit", "profits", "bullish",
    "gain", "gains", "strong", "outperform", "rebound", "boost", "boosts", "rise",
    "rises", "high", "wins", "win", "buy", "expansion", "dividend", "breakthrough",
    # FR
    "hausse", "progresse", "progression", "bondit", "bond", "record", "records",
    "dépasse", "croissance", "bénéfice", "bénéfices", "rebond", "gagne", "solide",
    "optimiste", "sommet", "relève", "succès", "envole", "envolée",
}
NEGATIFS = {
    # EN
    "plunge", "plunges", "crash", "crashes", "fall", "falls", "drop", "drops",
    "miss", "misses", "downgrade", "downgraded", "loss", "losses", "bearish",
    "weak", "cut", "cuts", "warning", "warns", "fraud", "lawsuit", "recession",
    "slump", "tumble", "tumbles", "fear", "fears", "sell-off", "selloff", "risk",
    "decline", "declines", "layoffs", "bankruptcy", "probe", "sink", "sinks",
    # FR
    "chute", "baisse", "recul", "recule", "perte", "pertes", "avertissement",
    "abaisse", "faillite", "effondre", "effondrement", "plonge", "inquiétude",
    "crise", "sanction", "amende", "licenciements", "enquête", "dégringole",
}


NOMS_ANGLAIS = {  # requêtes presse pour les contrats à terme
    "GC=F": "gold", "SI=F": "silver", "CL=F": "crude oil", "BZ=F": "brent oil",
    "NG=F": "natural gas", "HG=F": "copper", "CC=F": "cocoa", "CT=F": "cotton",
    "KC=F": "coffee", "ZW=F": "wheat",
}


def _query(symbol: str) -> str:
    """Transforme un symbole en requête de recherche d'actualités."""
    if symbol in NOMS_ANGLAIS:
        return f"{NOMS_ANGLAIS[symbol]} price"
    base = symbol.replace("=X", "").replace("USDT", "")
    for suffix in (".PA", ".DE", ".AS"):
        base = base.removesuffix(suffix)
    if symbol.endswith("USDT"):
        return f"{base} crypto"
    if symbol.endswith("=X"):
        return f"{base} forex"
    return f"{base} stock"


def headlines(symbol: str, max_items: int = 20) -> pd.DataFrame:
    """Derniers titres de presse pour un symbole (titre, source, date, score)."""
    cache = config.CACHE_DIR / f"news_{_query(symbol).replace(' ', '_')}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        items = json.loads(cache.read_text(encoding="utf-8"))
    else:
        resp = requests.get(
            RSS,
            params={"q": _query(symbol), "hl": "en-US", "gl": "US", "ceid": "US:en"},
            timeout=20, headers={"User-Agent": "MarketLab/0.1"},
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            items.append({
                "titre": (item.findtext("title") or "").strip(),
                "source": (item.findtext("source") or "").strip(),
                "date": (item.findtext("pubDate") or "").strip(),
            })
        cache.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    rows = []
    for it in items[:max_items]:
        mots = {m.strip(".,!?:;()'\"").lower() for m in it["titre"].split()}
        score = len(mots & POSITIFS) - len(mots & NEGATIFS)
        rows.append({**it, "sentiment": score})
    return pd.DataFrame(rows)


def sentiment(symbol: str) -> dict:
    """Sentiment agrégé sur les derniers titres : moyenne et répartition."""
    df = headlines(symbol)
    if df.empty:
        return {"symbole": symbol, "n_titres": 0, "score_moyen": None, "lecture": "aucune actualité"}
    moyen = float(df["sentiment"].mean())
    lecture = ("nettement positif" if moyen > 0.5 else
               "plutôt positif" if moyen > 0.1 else
               "nettement négatif" if moyen < -0.5 else
               "plutôt négatif" if moyen < -0.1 else "neutre")
    return {
        "symbole": symbol,
        "n_titres": len(df),
        "score_moyen": round(moyen, 2),
        "positifs": int((df["sentiment"] > 0).sum()),
        "negatifs": int((df["sentiment"] < 0).sum()),
        "lecture": lecture,
    }
