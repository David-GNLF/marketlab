"""Actions US/EU, indices et forex via Yahoo Finance (yfinance, sans clé)."""

import datetime as dt

import pandas as pd
import yfinance as yf

from marketlab.data import base

# Profondeur MAXIMALE servie par Yahoo, par intervalle, en jours.
#
# Ce n'est pas un réglage de confort : au-delà, Yahoo ne renvoie pas moins de
# données, il renvoie un tableau VIDE. Demander 730 jours de barres 5 min fait
# donc échouer l'appel avec « Aucune donnée Yahoo », ce qui ressemble à un
# titre retiré de la cote alors que seule la profondeur est en cause.
# D'où le bornage automatique plus bas.
#
# MARGE D'UN JOUR, et elle est indispensable. Les limites annoncées par Yahoo
# (60 jours en 5 min, 7 jours en 1 min) sont vérifiées côté serveur sur un
# HORODATAGE, pas sur une date. Demander pile 60 jours part donc de minuit et
# tombe hors fenêtre de quelques heures : mesuré à l'amorçage de l'historique,
# « 5m data not available […] must be within the last 60 days » — et comme
# l'appel est groupé, les 54 titres sont tombés d'un coup pendant que les
# cryptos (Binance, autre borne) passaient. D'où 59 et non 60.
PROFONDEUR_MAX_JOURS = {
    "1m": 6,
    "2m": 59,
    "5m": 59,
    "15m": 59,
    "30m": 59,
    "60m": 729,
    "90m": 59,
    "1h": 729,
}

# Intervalles pour lesquels l'horodatage porte une heure signifiante : ils
# doivent être ramenés en UTC (voir base.normalize). Pour les barres
# quotidiennes et au-delà, seule la date compte et le comportement historique
# est conservé.
_INTRADAY = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}


def profondeur_utile(interval: str, lookback_days: int) -> int:
    """Profondeur réellement demandable à Yahoo pour cet intervalle."""
    return min(lookback_days, PROFONDEUR_MAX_JOURS.get(interval, lookback_days))


def _fuseau(interval: str) -> str | None:
    return "UTC" if interval in _INTRADAY else None


def get_ohlcv(symbol: str, interval: str = "1d", lookback_days: int = 730) -> pd.DataFrame:
    cached = base.load_cached("yahoo", symbol, interval, lookback_days)
    if cached is not None:
        return cached

    jours = profondeur_utile(interval, lookback_days)
    start = dt.date.today() - dt.timedelta(days=jours)
    df = yf.download(
        symbol,
        start=start.isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"Aucune donnée Yahoo pour {symbol}")
    # yfinance renvoie des colonnes MultiIndex (champ, ticker) même pour un seul titre
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = base.normalize(df, tz=_fuseau(interval))
    base.save_cache(df, "yahoo", symbol, interval, lookback_days)
    return df


def get_ohlcv_multi(symbols: list[str], interval: str = "1d",
                    lookback_days: int = 730) -> dict[str, pd.DataFrame]:
    """Même chose, mais en UN SEUL appel réseau pour tous les symboles.

    POURQUOI. La veille balaie ~60 titres toutes les 10 minutes. Un appel par
    titre, c'est 60 requêtes par passage, soit plusieurs milliers par jour chez
    un fournisseur gratuit — la limitation de débit arrive vite et se manifeste
    par des trous silencieux. C'est exactement le motif que les plateformes
    professionnelles règlent avec un « feed handler » : UN abonnement, puis
    redistribution interne. `yf.download` accepte une liste, on s'en sert.

    Les titres déjà en cache ne sont pas redemandés ; seuls les manquants
    partent dans l'appel groupé. Un symbole que Yahoo ne renvoie pas est
    simplement absent du résultat — à l'appelant de le constater, jamais une
    exception qui ferait tomber les 59 autres.
    """
    resultats: dict[str, pd.DataFrame] = {}
    a_charger: list[str] = []
    for sym in symbols:
        cached = base.load_cached("yahoo", sym, interval, lookback_days)
        if cached is not None:
            resultats[sym] = cached
        else:
            a_charger.append(sym)
    if not a_charger:
        return resultats

    jours = profondeur_utile(interval, lookback_days)
    start = dt.date.today() - dt.timedelta(days=jours)
    brut = yf.download(
        a_charger,
        start=start.isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
    )
    if brut is None or brut.empty:
        return resultats

    fuseau = _fuseau(interval)
    for sym in a_charger:
        try:
            if isinstance(brut.columns, pd.MultiIndex):
                if sym not in brut.columns.get_level_values(0):
                    continue
                part = brut[sym]
            else:
                # liste d'un seul élément : yfinance peut aplatir les colonnes
                part = brut
            part = base.normalize(part, tz=fuseau)
            if part.empty:
                continue
            base.save_cache(part, "yahoo", sym, interval, lookback_days)
            resultats[sym] = part
        except Exception:
            continue  # un titre absent ne doit pas emporter le lot
    return resultats
