"""Séries macro US via FRED.

Deux chemins d'accès, et la différence compte :

* le CSV public `fredgraph` (sans clé) ne sert que la DERNIÈRE version d'une
  série — les valeurs révisées, telles qu'on les lit aujourd'hui ;
* l'API avec clé donne en plus les millésimes (ALFRED) : le chiffre tel qu'il a
  été PUBLIÉ le jour J. C'est le seul qui ait fait bouger le marché, donc le
  seul dont on puisse tirer une surprise économique honnête.

La clé est facultative : sans elle tout continue de fonctionner sur le CSV.
La poser : `python scripts/configurer_fred.py`.
"""

import io
import json
import os
import time

import pandas as pd
import requests

from marketlab import config

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
API = "https://api.stlouisfed.org/fred"
CONFIG_PATH = config.DATA_DIR / "providers.json"


def api_key() -> str | None:
    """Clé d'API FRED, ou None. Variable d'environnement d'abord.

    L'ordre n'est pas arbitraire : sur les runners GitHub il n'y a pas de
    `data_local/providers.json` (il est ignoré par git, et le dépôt est
    public), seulement le secret `MARKETLAB_FRED_API_KEY`. En local c'est
    l'inverse. Même schéma que `ftps.py` et `notify.py`.
    """
    depuis_env = (os.environ.get("MARKETLAB_FRED_API_KEY") or "").strip()
    if depuis_env:
        return depuis_env
    if not CONFIG_PATH.exists():
        return None
    try:
        contenu = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return (contenu.get("fred_api_key") or "").strip() or None


def serie_millesime(series_id: str, date_vintage) -> pd.Series | None:
    """Série TELLE QU'ELLE ÉTAIT CONNUE le `date_vintage` (ALFRED).

    C'est la différence qui fait tout pour mesurer une surprise. `get_series()`
    renvoie les valeurs RÉVISÉES, telles qu'on les lit aujourd'hui ; ici on
    demande l'état de la série à une date passée, donc le chiffre exactement
    tel qu'il a été publié ce jour-là. L'emploi américain est révisé deux fois,
    le PIB davantage — et c'est la première estimation, pas la corrigée, qui a
    fait bouger le marché.

    Pourquoi la série entière et non la seule observation publiée : une
    variation mensuelle se calcule sur DEUX points, et le point précédent est
    lui-même dans l'état où il était connu ce jour-là. Reconstituer la
    variation à partir des valeurs d'aujourd'hui donnerait un chiffre que
    personne n'a jamais vu.

    Renvoie None si aucune clé d'API n'est configurée — l'appelant retombe
    alors sur `get_series()`.

    Le cache n'expire PAS : un millésime est par définition figé, l'état d'une
    série au 15 juillet ne changera plus jamais.
    """
    cle = api_key()
    if not cle:
        return None
    jour = pd.Timestamp(date_vintage).date().isoformat()
    cache = config.CACHE_DIR / f"fred_millesime_{series_id}_{jour}.csv"
    if cache.exists():
        texte = cache.read_text(encoding="utf-8")
    else:
        resp = requests.get(f"{API}/series/observations", timeout=25, params={
            "series_id": series_id, "api_key": cle, "file_type": "json",
            "realtime_start": jour, "realtime_end": jour,
        })
        if resp.status_code != 200:
            return None
        obs = resp.json().get("observations", [])
        lignes = [f"{o['date']},{o['value']}" for o in obs]
        texte = "date,value\n" + "\n".join(lignes)
        cache.write_text(texte, encoding="utf-8")

    df = pd.read_csv(io.StringIO(texte))
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    # FRED note les valeurs manquantes « . » : les convertir en NaN, surtout
    # pas en zéro
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    serie = df.dropna(subset=["date"]).set_index("date")["value"].dropna()
    return serie.sort_index() if not serie.empty else None


def get_series(series_id: str, lookback_years: int = 10) -> pd.Series:
    """Renvoie une série FRED (index datetime, valeurs float)."""
    cache = config.CACHE_DIR / f"fred_{series_id}.csv"
    if cache.exists() and time.time() - cache.stat().st_mtime < 24 * 3600:
        text = cache.read_text(encoding="utf-8")
    else:
        resp = requests.get(URL, params={"id": series_id}, timeout=20)
        resp.raise_for_status()
        text = resp.text
        cache.write_text(text, encoding="utf-8")

    df = pd.read_csv(io.StringIO(text))
    date_col, value_col = df.columns[0], df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    s = df.set_index(date_col)[value_col].dropna()
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=lookback_years)
    return s[s.index >= cutoff]
