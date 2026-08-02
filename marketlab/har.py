"""HAR-RV : prévision de la VOLATILITÉ à partir de la volatilité réalisée.

CE QUE CE MODULE PRÉTEND, ET CE QU'IL NE PRÉTEND PAS. Il prévoit la
volatilité — l'AMPLITUDE des mouvements à venir. Il ne dit rien de leur SENS.
Sur ce projet, la direction a été mesurée non prédictible (IC note↔rendement
relatif −0,087 sur 3 231 verdicts, réussite directionnelle 44-60 %), alors que
les intervalles de prévision sont bien calibrés (couverture réelle 80-87 % pour
80 % annoncé). Ce module travaille du côté qui marche.

LE MODÈLE. HAR-RV (Corsi, 2009) régresse la volatilité réalisée future sur
trois moyennes de la volatilité passée — la veille, la semaine, le mois :

    log RV(t+1..t+h) = b0 + b_j·log RV(t) + b_s·log RV(5j) + b_m·log RV(22j)

Trois horizons emboîtés au lieu d'une seule décroissance exponentielle : c'est
ce qui lui permet de reproduire la persistance très longue de la volatilité,
là où un EWMA à λ unique oublie trop vite. Régression sur le LOGARITHME de la
variance : la variance est positive et très asymétrique, la régresser en niveau
laisse les épisodes de stress dominer l'ajustement.

POURQUOI C'EST UTILE ICI, PRÉCISÉMENT. `forecast.projeter()` rééchantillonne
les rendements des 750 dernières séances : la largeur du cône reflète donc la
volatilité INCONDITIONNELLE sur trois ans. Elle ne se resserre pas quand le
marché est calme et ne s'élargit pas quand il se tend. Une prévision de
volatilité conditionnelle permet de recaler cette largeur (`vol_cible`).

GARDE-FOU. Le modèle n'est utilisé en production que s'il BAT ses concurrents
hors échantillon (marche au hasard et EWMA), sur les critères QLIKE et RMSE.
Sinon `data_local/har_modele.json` porte `retenu: false` et rien ne change.
C'est la même discipline que les pondérations apprises du verdict : aucune
brique ne pèse avant d'avoir fait ses preuves.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from marketlab import config, intraday

FENETRE_SEMAINE = 5
FENETRE_MOIS = 22

# Nombre minimal d'observations exploitables pour tenter un ajustement. Quatre
# coefficients à estimer : en dessous, l'ajustement décrirait le bruit.
OBS_MIN = 200

PERIODES_AN = 252
MODELE_PATH = config.DATA_DIR / "har_modele.json"

LAMBDA_EWMA = 0.94  # même λ que forecast.volatilite_ewma, pour comparer à armes égales


# ---------------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------------

def composantes(rv: pd.Series, horizon: int = 1) -> pd.DataFrame:
    """Régresseurs HAR et cible, sans aucune fuite d'information.

    `rv` — variance réalisée quotidienne, indexée par date et TRIÉE.
    `horizon` — la cible est la moyenne du log de la variance sur les
    `horizon` séances SUIVANTES (formulation directe, préférée à l'itération
    du modèle à un jour : elle évite de composer l'erreur h fois).

    Les moyennes glissantes s'arrêtent à t inclus, la cible commence à t+1 :
    aucune ligne ne peut voir son propre futur. Les fenêtres sont
    POSITIONNELLES, donc les week-ends et jours fériés ne créent pas de trous
    artificiels — les séances se suivent, c'est tout ce qui compte.
    """
    colonnes = ["rv_j", "rv_s", "rv_m", "cible"]
    serie = pd.to_numeric(pd.Series(rv), errors="coerce").dropna()
    serie = serie[serie > 0]
    if serie.empty:
        return pd.DataFrame(columns=colonnes)

    lg = np.log(serie)
    cadre = pd.DataFrame({
        "rv_j": lg,
        "rv_s": lg.rolling(FENETRE_SEMAINE).mean(),
        "rv_m": lg.rolling(FENETRE_MOIS).mean(),
        # moyenne de log RV sur t+1..t+horizon
        "cible": lg.rolling(horizon).mean().shift(-horizon),
    })
    return cadre.dropna()[colonnes]


def panel(releve: pd.DataFrame | None = None, horizon: int = 1,
          interval: str = intraday.INTERVALLE_DEFAUT) -> pd.DataFrame:
    """Empile les composantes de tous les titres du relevé de volatilité.

    Renvoie un panneau (date, symbole, rv_j, rv_s, rv_m, cible).
    """
    releve = intraday.charger_releve() if releve is None else releve
    colonnes = ["date", "symbole", "rv_j", "rv_s", "rv_m", "cible"]
    if releve is None or releve.empty:
        return pd.DataFrame(columns=colonnes)

    releve = releve[releve["interval"] == interval]
    morceaux = []
    for symbole, part in releve.groupby("symbole"):
        part = part.sort_values("date")
        comp = composantes(pd.Series(part["rv"].to_numpy(),
                                     index=part["date"].to_numpy()),
                           horizon=horizon)
        if comp.empty:
            continue
        comp = comp.reset_index(names="date")
        comp["symbole"] = symbole
        morceaux.append(comp[colonnes])
    if not morceaux:
        return pd.DataFrame(columns=colonnes)
    return pd.concat(morceaux, ignore_index=True).sort_values(["date", "symbole"])


# ---------------------------------------------------------------------------
# Ajustement
# ---------------------------------------------------------------------------

def _matrices(cadre: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    X = np.column_stack([
        np.ones(len(cadre)),
        cadre["rv_j"].to_numpy(float),
        cadre["rv_s"].to_numpy(float),
        cadre["rv_m"].to_numpy(float),
    ])
    return X, cadre["cible"].to_numpy(float)


def ajuster(cadre: pd.DataFrame) -> dict | None:
    """Régression HAR par moindres carrés. None si l'échantillon est trop mince.

    Estimation MISE EN COMMUN sur tous les titres, pas titre par titre. Deux
    raisons. La bonne : l'historique disponible (Yahoo ne sert les barres 5 min
    que sur 60 jours) ne permet pas encore d'estimer quatre coefficients par
    actif sans décrire le bruit. La solide : les régresseurs portent déjà le
    NIVEAU de volatilité propre à chaque actif, donc si les coefficients de
    pente somment à environ 1, le modèle est quasi invariant au niveau et se
    transpose d'un actif à l'autre. `somme_pentes` est renvoyée pour le
    vérifier au lieu de le supposer.
    """
    if cadre is None or len(cadre) < OBS_MIN:
        return None
    X, y = _matrices(cadre)
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    residus = y - X @ beta
    sst = float(((y - y.mean()) ** 2).sum())
    return {
        "b0": float(beta[0]),
        "b_jour": float(beta[1]),
        "b_semaine": float(beta[2]),
        "b_mois": float(beta[3]),
        "somme_pentes": float(beta[1] + beta[2] + beta[3]),
        "n_obs": int(len(cadre)),
        "r2": float(1 - (residus ** 2).sum() / sst) if sst > 0 else 0.0,
    }


def predire_log(modele: dict, rv_j: np.ndarray | float, rv_s: np.ndarray | float,
                rv_m: np.ndarray | float) -> np.ndarray:
    """log de la variance prévue, à partir des trois composantes."""
    return (modele["b0"] + modele["b_jour"] * np.asarray(rv_j, dtype=float)
            + modele["b_semaine"] * np.asarray(rv_s, dtype=float)
            + modele["b_mois"] * np.asarray(rv_m, dtype=float))


# ---------------------------------------------------------------------------
# Concurrents et arbitrage hors échantillon
# ---------------------------------------------------------------------------

def _qlike(vrai: np.ndarray, prevu: np.ndarray) -> float:
    """QLIKE : critère de référence pour comparer des prévisions de variance.

    Préféré à l'erreur quadratique parce que la variance réalisée n'est qu'un
    ESTIMATEUR BRUITÉ de la vraie variance ; QLIKE reste correctement ordonné
    malgré ce bruit, là où le RMSE favorise les prévisions trop lisses. Plus
    bas est meilleur, minimum atteint quand prevu = vrai.
    """
    ratio = np.asarray(vrai, float) / np.asarray(prevu, float)
    return float(np.mean(ratio - np.log(ratio) - 1))


def _ewma_sur_rv(rv: np.ndarray, lam: float = LAMBDA_EWMA) -> np.ndarray:
    """Prévision EWMA de la variance : v(t+1) = λ·v(t) + (1−λ)·rv(t).

    Aligné pour que la case t contienne la prévision FAITE en t pour t+1.
    """
    rv = np.asarray(rv, float)
    out = np.empty_like(rv)
    courant = rv[0]
    for i, x in enumerate(rv):
        courant = lam * courant + (1 - lam) * x
        out[i] = courant
    return out


def retenir(gagnant_qlike: str, gagnant_rmse: str) -> bool:
    """Le RMSE doit CONFIRMER le QLIKE : un désaccord ne retient rien.

    POURQUOI UNE FONCTION NOMMÉE. Cette règle tenait en une expression au
    milieu du dictionnaire de sortie, et son test la recopiait mot pour mot
    pour la réappliquer à sa propre sortie — une assertion vraie quelle que
    soit l'implémentation. Le passage de `and` à `or`, c'est-à-dire retenir un
    modèle sur un seul critère, ne l'aurait pas fait broncher. Isolée, la
    règle s'exerce sur ses quatre combinaisons possibles, sans avoir à
    fabriquer un jeu de données qui produise un désaccord.

    Un désaccord entre les deux critères signale un avantage fragile : le
    QLIKE punit surtout la sous-estimation de la variance, le RMSE traite les
    deux sens pareillement. Gagner l'un en perdant l'autre, c'est gagner sur
    une asymétrie, pas sur la prévision.
    """
    return gagnant_qlike == "har" and gagnant_rmse == "har"


def comparer(horizon: int = 1, part_test: float = 0.4,
             releve: pd.DataFrame | None = None,
             interval: str = intraday.INTERVALLE_DEFAUT) -> dict:
    """Compare HAR, marche au hasard et EWMA HORS ÉCHANTILLON.

    Découpage par DATE et non par ligne : sans cela, un titre pourrait être
    entraîné sur une date postérieure à celle testée chez un autre titre, et le
    modèle profiterait d'un futur commun (les volatilités sont corrélées entre
    actifs, surtout en stress).

    Concurrents :
      * marche au hasard — la variance de demain est celle d'aujourd'hui.
        Étonnamment dure à battre, c'est le juge de paix habituel.
      * EWMA λ=0,94 — ce que MarketLab utilise déjà (`forecast.volatilite_ewma`).
    """
    pan = panel(releve, horizon=horizon, interval=interval)
    vide = {"suffisant": False, "raison": "relevé de volatilité insuffisant",
            "n_entrainement": 0, "n_test": 0}
    if pan.empty:
        return vide

    dates = np.sort(pan["date"].unique())
    if len(dates) < 8:
        return {**vide, "raison": f"trop peu de dates ({len(dates)})"}
    coupe = dates[int(len(dates) * (1 - part_test))]
    entrainement = pan[pan["date"] < coupe]
    test = pan[pan["date"] >= coupe]

    modele = ajuster(entrainement)
    if modele is None or test.empty:
        return {**vide, "raison": f"échantillon d'entraînement trop mince "
                                  f"({len(entrainement)} obs, {OBS_MIN} requises)",
                "n_entrainement": len(entrainement), "n_test": len(test)}

    vrai = np.exp(test["cible"].to_numpy(float))
    prev = {
        "har": np.exp(predire_log(modele, test["rv_j"], test["rv_s"], test["rv_m"])),
        "hasard": np.exp(test["rv_j"].to_numpy(float)),
    }
    # EWMA reconstruit par titre : la récursion n'a de sens que dans le temps
    ewma = np.empty(len(test))
    for symbole, part in test.groupby("symbole"):
        ewma[test["symbole"].to_numpy() == symbole] = _ewma_sur_rv(
            np.exp(part["rv_j"].to_numpy(float)))
    prev["ewma"] = ewma

    scores = {
        nom: {"qlike": round(_qlike(vrai, p), 5),
              "rmse_log": round(float(np.sqrt(np.mean(
                  (np.log(p) - test["cible"].to_numpy(float)) ** 2))), 5)}
        for nom, p in prev.items()
    }
    gagnant = min(scores, key=lambda n: scores[n]["qlike"])
    gagnant_rmse = min(scores, key=lambda n: scores[n]["rmse_log"])

    return {
        "suffisant": True,
        "horizon": horizon,
        "modele": modele,
        "scores": scores,
        "gagnant": gagnant,
        "gagnant_rmse": gagnant_rmse,
        "har_retenu": retenir(gagnant, gagnant_rmse),
        "n_entrainement": int(len(entrainement)),
        "n_test": int(len(test)),
        "n_titres": int(pan["symbole"].nunique()),
        "coupe": str(coupe),
        "periode": [str(dates[0]), str(dates[-1])],
    }


# ---------------------------------------------------------------------------
# Modèle de production
# ---------------------------------------------------------------------------

def calibrer(horizons: tuple[int, ...] = (1, 20), ecrire: bool = True) -> dict:
    """Arbitre, puis réajuste sur TOUT l'échantillon le modèle retenu.

    Réajuster sur l'ensemble après l'arbitrage n'est pas de la triche : la
    décision de retenir HAR a été prise hors échantillon, seule l'estimation
    des coefficients profite ensuite de toutes les données disponibles.
    """
    resultat = {"horizons": {}, "retenu": False}
    for h in horizons:
        arbitrage = comparer(horizon=h)
        entree = {k: v for k, v in arbitrage.items() if k != "modele"}
        if arbitrage.get("har_retenu"):
            complet = ajuster(panel(horizon=h))
            entree["modele"] = complet or arbitrage["modele"]
            resultat["retenu"] = True
        else:
            entree["modele"] = None
        resultat["horizons"][str(h)] = entree

    if ecrire:
        MODELE_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODELE_PATH.write_text(json.dumps(resultat, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    return resultat


def charger_modele() -> dict | None:
    if not MODELE_PATH.exists():
        return None
    try:
        return json.loads(MODELE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def vol_cible(symbole: str) -> float | None:
    """Volatilité QUOTIDIENNE à passer à `forecast.projeter(vol_cible=…)`.

    LE CHAÎNON QUI MANQUAIT. Le modèle était calibré chaque nuit, arbitré hors
    échantillon contre la marche au hasard et l'EWMA, retenu — et appelé nulle
    part. `forecast.projeter` avait le paramètre et la documentation ; aucun
    appelant ne le passait. La console annonçait « HAR pilote la largeur du
    cône » à propos d'un branchement inexistant.

    Renvoie None si le modèle n'a pas été retenu ou si ce titre n'a pas assez
    de volatilité relevée — auquel cas l'appelant garde son comportement
    inconditionnel, surtout pas une valeur inventée.

    DEUX MODÈLES SE PRÉSENTENT, l'arbitrage est ici et nulle part ailleurs :

    1. HAR-sur-GKYZ à l'horizon 20 — celui du cône. Retenu sur les deux
       critères, vérifié sur les vrais cinq ans (45 528 observations, QLIKE
       0,138 contre 0,207 pour l'EWMA). Ses unités couvrent les nuits, comme
       les rendements quotidiens que le simulateur rééchantillonne — la
       volatilité 5 min, elle, les exclut et visait donc légèrement bas.
    2. le modèle 5 min à l'horizon 1, en repli — s'il est un jour retenu.

    None si aucun des deux n'a fait ses preuves : l'appelant garde son
    comportement inconditionnel, surtout pas une valeur inventée.
    """
    try:
        from marketlab import vol_ohlc
        p = vol_ohlc.prevoir(symbole, horizon=20)
        if p:
            return float(p["vol_jour"])
    except Exception:
        pass          # l'arbitre ne doit jamais faire tomber une fiche
    p = prevoir(symbole, horizon=1)
    return float(p["vol_jour"]) if p else None


def prevoir(symbole: str, horizon: int = 1,
            interval: str = intraday.INTERVALLE_DEFAUT) -> dict | None:
    """Volatilité prévue pour `symbole`, ou None si rien n'est utilisable.

    None a un sens précis et il faut le respecter : soit le modèle n'a pas
    battu ses concurrents, soit ce titre n'a pas 22 séances de volatilité
    relevée. Dans les deux cas l'appelant doit garder son comportement actuel,
    surtout pas se rabattre sur une valeur inventée.
    """
    enregistre = charger_modele()
    if not enregistre or not enregistre.get("retenu"):
        return None
    entree = (enregistre.get("horizons") or {}).get(str(horizon))
    modele = (entree or {}).get("modele")
    if not modele:
        return None

    releve = intraday.charger_releve()
    if releve.empty:
        return None
    part = releve[(releve["symbole"] == symbole)
                  & (releve["interval"] == interval)].sort_values("date")
    if len(part) < FENETRE_MOIS:
        return None

    lg = np.log(pd.to_numeric(part["rv"], errors="coerce").dropna())
    lg = lg[np.isfinite(lg)]
    if len(lg) < FENETRE_MOIS:
        return None

    log_rv = float(predire_log(
        modele,
        float(lg.iloc[-1]),
        float(lg.iloc[-FENETRE_SEMAINE:].mean()),
        float(lg.iloc[-FENETRE_MOIS:].mean()),
    ))
    variance = float(np.exp(log_rv))
    vol_jour = float(np.sqrt(variance))
    return {
        "symbole": symbole,
        "horizon": horizon,
        "variance_jour": variance,
        "vol_jour": vol_jour,
        "vol_annualisee_%": round(vol_jour * np.sqrt(PERIODES_AN) * 100, 2),
        "n_seances_relevees": int(len(lg)),
        "derniere_date": str(part["date"].iloc[-1]),
    }
