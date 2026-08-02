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
    from marketlab import har
    releve = _releve_gkyz(symboles, jours)
    if releve.empty:
        return {"suffisant": False, "raison": "aucun OHLC exploitable"}
    verdict = har.comparer(horizon=horizon, releve=releve, interval="1d-gkyz")
    verdict["matiere"] = f"GKYZ quotidien, {releve['symbole'].nunique()} titres"
    return verdict


def _releve_gkyz(symboles: list[str] | None, jours: int) -> pd.DataFrame:
    from marketlab import config
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
        return pd.DataFrame(columns=["date", "symbole", "interval", "rv",
                                     "n_barres", "vol_annualisee_%"])
    return pd.concat(morceaux, ignore_index=True)


# ---------------------------------------------------------------------------
# Production : le modèle HAR-sur-GKYZ, horizon du cône
# ---------------------------------------------------------------------------

# Décision utilisateur du 2026-08-02, prise sur mesure : à l'horizon 20 —
# celui du cône — HAR bat l'EWMA sur LES DEUX critères, vérifié d'abord sur
# ~2 ans (QLIKE 0,172 vs 0,204) puis sur les vrais 5 ans et 45 528
# observations (QLIKE 0,138 vs 0,207, RMSE 0,494 vs 0,744). À l'horizon 1,
# l'EWMA garde le QLIKE : le fichier n'encode QUE ce qui est prouvé.
MODELE_PATH = None      # défini après import de config (voir bas de module)

HORIZON_CONE = 20
JOURS_CALIBRATION = 1825          # les vrais cinq ans, en jours calendaires
FENETRE_SEMAINE, FENETRE_MOIS = 5, 22

_MEMO: dict = {}


def calibrer(jours: int = JOURS_CALIBRATION, ecrire: bool = True) -> dict:
    """Arbitre HAR-sur-GKYZ à l'horizon du cône, puis réajuste sur tout.

    Même contrat que har.calibrer : la décision de retenir se prend HORS
    échantillon sur les deux critères ; seuls les coefficients profitent
    ensuite de toutes les données. Et le verdict se rejoue chaque nuit — si
    l'avantage disparaît, le modèle se désarme tout seul, comme il s'est armé.
    """
    from marketlab import har
    releve = _releve_gkyz(None, jours)
    arbitrage = rejouer_arbitrage_har(horizon=HORIZON_CONE, jours=jours)         if releve.empty else None
    if arbitrage is None:
        arbitrage = har.comparer(horizon=HORIZON_CONE, releve=releve,
                                 interval="1d-gkyz")
    resultat = {"retenu": bool(arbitrage.get("har_retenu")),
                "horizon": HORIZON_CONE, "jours": jours,
                "arbitrage": {k: v for k, v in arbitrage.items()
                              if k != "modele"}}
    if resultat["retenu"] and not releve.empty:
        complet = har.ajuster(har.panel(releve=releve, horizon=HORIZON_CONE,
                                        interval="1d-gkyz"))
        resultat["modele"] = complet or arbitrage.get("modele")
        resultat["retenu"] = resultat["modele"] is not None
    if ecrire and MODELE_PATH is not None:
        import json
        MODELE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODELE_PATH.write_text(json.dumps(resultat, indent=2,
                                          ensure_ascii=False),
                               encoding="utf-8")
        _MEMO.pop("modele", None)
    return resultat


def charger_modele() -> dict | None:
    if "modele" in _MEMO:
        return _MEMO["modele"]
    try:
        import json
        _MEMO["modele"] = json.loads(MODELE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _MEMO["modele"] = None
    return _MEMO["modele"]


def prevoir(symbole: str, horizon: int = HORIZON_CONE) -> dict | None:
    """Volatilité quotidienne prévue sur l'horizon du cône. None sans preuve.

    Le modèle prédit la MOYENNE du log de la variance quotidienne sur les
    `horizon` prochaines séances — précisément le niveau qu'un cône de
    20 séances doit viser, là où une prévision à un jour décrirait demain et
    pas le trajet. Une prédiction aberrante n'est pas bridée ici : c'est le
    simulateur qui borne (facteur [0,33 ; 3] de `forecast.projeter`), une
    seule ceinture au bon endroit plutôt que deux qui se contredisent.
    """
    enregistre = charger_modele()
    if not enregistre or not enregistre.get("retenu")             or int(enregistre.get("horizon", -1)) != int(horizon):
        return None
    modele = enregistre.get("modele")
    if not modele:
        return None
    try:
        from marketlab.data import get_ohlcv
        var_jour = variance_gkyz(get_ohlcv(symbole, lookback_days=250))
    except Exception:
        return None
    var_jour = var_jour[var_jour > 0]
    if len(var_jour) < FENETRE_MOIS:
        return None
    from marketlab import har
    lg = np.log(var_jour)
    pred = float(har.predire_log(modele, float(lg.iloc[-1]),
                                 float(lg.tail(FENETRE_SEMAINE).mean()),
                                 float(lg.tail(FENETRE_MOIS).mean())))
    vol_jour = float(np.sqrt(np.exp(pred)))
    return {"symbole": symbole, "horizon": horizon, "vol_jour": vol_jour,
            "vol_annualisee_%": round(vol_jour * np.sqrt(PERIODES_AN) * 100, 2),
            "n_seances": int(len(var_jour))}


from marketlab import config as _config          # noqa: E402
MODELE_PATH = _config.DATA_DIR / "har_gkyz.json"
