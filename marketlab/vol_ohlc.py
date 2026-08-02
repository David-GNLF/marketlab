"""Volatilité estimée sur l'OHLC complet — 5 à 8 fois plus efficace qu'avec
les clôtures seules.

L'IDÉE, ET POURQUOI ELLE EST GRATUITE. Estimer la volatilité sur les clôtures
jette quatre cinquièmes de l'information : le plus haut et le plus bas d'une
séance racontent l'amplitude réellement parcourue, l'ouverture raconte le saut
de nuit. Parkinson (1980), Garman-Klass (1980), Rogers-Satchell (1991) puis
Yang-Zhang (2000) exploitent ces quatre prix — même donnée, déjà téléchargée,
estimation nettement plus précise. Sur une fenêtre de 21 séances, l'estimateur
aux clôtures a une erreur relative d'environ 15 % ; Yang-Zhang fait le travail
d'une fenêtre quatre fois plus longue.

OÙ ÇA COMPTE ICI, précisément :

* le « réalisé 21 séances » de la fiche implicite — 21 clôtures, c'est très
  bruité, et ce bruit se lisait comme de l'information ;
* la prime de variance (VIX vs réalisé) — même cible, estimation plus serrée ;
* le REJEU de l'arbitrage HAR : le premier arbitrage s'est joué sur 59 jours
  de volatilité 5 min (Yahoo ne sert pas plus profond) et s'est perdu à 0,002
  de QLIKE près. Le proxy quotidien GKYZ ouvre CINQ ANS d'historique — de quoi
  rejouer le duel avec un vrai échantillon.

DEUX FAMILLES À NE PAS CONFONDRE, et c'est un choix de conception :

* les estimateurs PAR SÉANCE (Parkinson, GK, GKYZ) donnent une variance par
  jour — la matière première d'un HAR ;
* Yang-Zhang proprement dit est une combinaison PAR FENÊTRE (nuit +
  ouverture-clôture + Rogers-Satchell pondérés) — la bonne mesure d'un niveau
  de volatilité sur 21 séances.

Le tout INCLUT les nuits, contrairement à la volatilité réalisée du magasin
intrajournalier qui les exclut par construction : ces estimateurs vivent dans
le même temps qu'une option, c'est pourquoi `implicite` les consomme.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PERIODES_AN = 252

# k de Yang-Zhang : minimise la variance de l'estimateur combiné.
def _k(n: int) -> float:
    return 0.34 / (1.34 + (n + 1) / max(n - 1, 1))


def _propre(df: pd.DataFrame) -> pd.DataFrame:
    """OHLC exploitable : colonnes présentes, prix positifs, high ≥ low.

    Yahoo sert parfois des séances dégénérées (high = low = close, zéros) sur
    les places fines : les garder fabriquerait des variances nulles qui, dans
    un log, deviennent des moins-l'infini.
    """
    colonnes = ["open", "high", "low", "close"]
    if df is None or any(c not in df.columns for c in colonnes):
        return pd.DataFrame(columns=colonnes)
    out = df[colonnes].apply(pd.to_numeric, errors="coerce").dropna()
    return out[(out > 0).all(axis=1) & (out["high"] >= out["low"])]


def variance_gkyz(df: pd.DataFrame) -> pd.Series:
    """Variance PAR SÉANCE (Garman-Klass–Yang-Zhang), saut de nuit inclus.

        σ²(jour) = ln(O/C₋₁)² + ½·ln(H/L)² − (2ln2−1)·ln(C/O)²

    Toujours positive : l'amplitude haut/bas majore le trajet
    ouverture-clôture, donc le terme soustrait ne peut pas l'emporter. C'est
    ce qui la rend utilisable telle quelle comme matière première d'un HAR
    (qui travaille en logarithme de la variance).
    """
    p = _propre(df)
    if p.empty:
        return pd.Series(dtype=float)
    nuit = np.log(p["open"] / p["close"].shift(1)) ** 2
    hl = 0.5 * np.log(p["high"] / p["low"]) ** 2
    co = (2 * np.log(2) - 1) * np.log(p["close"] / p["open"]) ** 2
    return (nuit + hl - co).dropna()


def vol_yang_zhang(df: pd.DataFrame, fenetre: int = 21) -> float | None:
    """Volatilité annualisée en % sur les `fenetre` dernières séances (YZ).

    None si l'OHLC ne le permet pas — l'appelant retombe alors sur les
    clôtures, et le dit.
    """
    p = _propre(df).tail(fenetre + 1)
    if len(p) < fenetre:
        return None
    o = np.log(p["open"] / p["close"].shift(1)).dropna()
    c = np.log(p["close"] / p["open"]).iloc[1:]
    rs = (np.log(p["high"] / p["open"]) * np.log(p["high"] / p["close"])
          + np.log(p["low"] / p["open"]) * np.log(p["low"] / p["close"])).iloc[1:]
    n = len(c)
    if n < 5:
        return None
    variance = (float(o.var(ddof=1)) + _k(n) * float(c.var(ddof=1))
                + (1 - _k(n)) * float(rs.mean()))
    if not np.isfinite(variance) or variance < 0:
        return None
    return float(np.sqrt(variance * PERIODES_AN) * 100)


def vol_future(df: pd.DataFrame, seances: int = 21) -> pd.Series:
    """Volatilité annualisée en % RÉALISÉE sur les `seances` suivant chaque
    date — la cible qu'une prévision (la nôtre ou celle du marché) doit viser.

    GKYZ par séance moyennée vers l'avant quand l'OHLC existe ; repli
    clôture-à-clôture sinon. Les fenêtres non finies restent NaN : une valeur
    partielle se lirait comme une valeur.
    """
    p = _propre(df)
    if not p.empty:
        var_jour = variance_gkyz(df)
        avenir = var_jour.shift(-1).iloc[::-1].rolling(seances).mean().iloc[::-1]
        return np.sqrt(avenir * PERIODES_AN) * 100
    if df is None or "close" not in getattr(df, "columns", []):
        return pd.Series(dtype=float)
    r = np.log(pd.to_numeric(df["close"], errors="coerce")).diff()
    avenir = r.shift(-1).iloc[::-1].rolling(seances).std().iloc[::-1]
    return avenir * np.sqrt(PERIODES_AN) * 100


def rejouer_arbitrage_har(symboles: list[str] | None = None,
                          horizon: int = 1, jours: int = 750) -> dict:
    """Le duel HAR contre EWMA, rejoué sur CINQ ANS de variance GKYZ.

    Le premier arbitrage (har.calibrer) s'est joué sur 59 jours de volatilité
    5 minutes — le maximum que Yahoo serve — et s'est perdu à 0,002 de QLIKE
    près, les deux critères en désaccord. Le proxy GKYZ n'a pas cette limite :
    l'OHLC quotidien remonte des années. Même machinerie (har.comparer, mêmes
    concurrents, même découpage par date), seule la matière première change.

    MESURE SEULEMENT : le verdict de production (har_modele.json) reste piloté
    par l'arbitrage sur la volatilité 5 min, parce que c'est elle que
    `har.prevoir` consomme. Brancher un modèle entraîné sur GKYZ exigerait
    d'aligner aussi ses entrées — une décision à prendre les yeux ouverts, pas
    un effet de bord d'un rejeu.
    """
    from marketlab import config, har
    from marketlab.data import get_ohlcv

    symboles = list(symboles or config.FICHES)
    morceaux = []
    for sym in symboles:
        try:
            var_jour = variance_gkyz(get_ohlcv(sym, lookback_days=jours))
        except Exception:
            continue
        var_jour = var_jour[var_jour > 0]
        if len(var_jour) < 100:
            continue
        morceaux.append(pd.DataFrame({
            "date": [d.date().isoformat() for d in var_jour.index],
            "symbole": sym, "interval": "1d-gkyz",
            "rv": var_jour.to_numpy(),
            "n_barres": 1, "vol_annualisee_%": np.sqrt(
                var_jour.to_numpy() * PERIODES_AN) * 100,
        }))
    if not morceaux:
        return {"suffisant": False, "raison": "aucun OHLC exploitable"}
    releve = pd.concat(morceaux, ignore_index=True)
    verdict = har.comparer(horizon=horizon, releve=releve, interval="1d-gkyz")
    verdict["matiere"] = f"GKYZ quotidien, {releve['symbole'].nunique()} titres"
    return verdict
