"""Méta-labeling : ne pas deviner la direction, décider s'il faut suivre le signal.

Idée (López de Prado, *Advances in Financial Machine Learning*) : séparer deux
problèmes que tout le monde mélange.

- Le **modèle primaire** dit dans quel SENS aller. C'est le problème difficile,
  sur lequel nos mesures montrent un avantage quasi nul (AUC ~0,50).
- Le **méta-modèle** dit s'il faut SUIVRE ce signal-là, maintenant. C'est un
  problème plus facile : filtrer les configurations défavorables (volatilité
  extrême, régime contraire, signal faible) est nettement plus abordable que
  prédire un sens.

Le gain ne vient donc pas d'une meilleure prédiction, mais d'un meilleur tri :
moins de trades, mieux choisis, et une taille de position proportionnelle à la
confiance.

**Étiquetage par triple barrière** — un simple « rendement à H jours » ignore le
chemin parcouru. Ici chaque signal est suivi jusqu'à ce qu'il touche :
- la barrière haute (objectif, k×ATR au-dessus) → label 1, le trade gagne ;
- la barrière basse (stop, k×ATR en dessous) → label 0, le trade perd ;
- la barrière temporelle (H séances) → label selon le signe du rendement.

Les barrières sont proportionnelles à l'ATR : elles s'adaptent donc à la
volatilité du moment, comme le ferait un vrai plan de trade.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from marketlab import indicators, ml, score_history

PERIODES_AN = 252


# --- Signal primaire --------------------------------------------------------

def signaux_primaires(df: pd.DataFrame, seuil: float = 25.0) -> pd.Series:
    """Signal de direction : score composite au-dessus du seuil → position longue.

    C'est délibérément le signal simple déjà utilisé partout dans MarketLab :
    l'objet du méta-modèle est de le filtrer, pas de le remplacer.
    """
    scores = score_history.score_series(df)
    return (scores >= seuil).astype(int)


# --- Étiquetage par triple barrière -----------------------------------------

def triple_barriere(df: pd.DataFrame, positions: np.ndarray, horizon: int = 20,
                    mult_objectif: float = 2.0, mult_stop: float = 1.5) -> dict:
    """Pour chaque entrée, quelle barrière est touchée en premier ?

    Renvoie labels (1 gagnant / 0 perdant), rendements réalisés et durées.
    """
    haut, bas, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    atr = df["atr14"].to_numpy()

    labels, rendements, durees, gardes = [], [], [], []
    for i in positions:
        if i + 1 >= len(close) or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        entree = close[i]
        cible = entree + mult_objectif * atr[i]
        stop = entree - mult_stop * atr[i]
        fin = min(i + horizon, len(close) - 1)

        label, sortie, duree = None, close[fin], fin - i
        for t in range(i + 1, fin + 1):
            # le stop est testé en premier : hypothèse prudente quand les deux
            # barrières sont franchies dans la même séance
            if bas[t] <= stop:
                label, sortie, duree = 0, stop, t - i
                break
            if haut[t] >= cible:
                label, sortie, duree = 1, cible, t - i
                break
        if label is None:  # barrière temporelle
            label = int(close[fin] > entree)
            sortie, duree = close[fin], fin - i

        labels.append(label)
        rendements.append(sortie / entree - 1)
        durees.append(duree)
        gardes.append(i)

    return {"positions": np.array(gardes), "labels": np.array(labels),
            "rendements": np.array(rendements), "durees": np.array(durees)}


# --- Méta-modèle ------------------------------------------------------------

CARACTERISTIQUES_META = ["score", "score_pente", "rsi_n", "bb_pct", "vol_rel",
                         "dist_sma50", "dist_sma200", "dd", "atr_n", "mom_20",
                         "mom_60", "vol20"]


def _caracteristiques(df: pd.DataFrame) -> pd.DataFrame:
    """Contexte du signal : sa force, sa dynamique, et l'état du marché."""
    close = df["close"]
    rendements = close.pct_change()
    scores = score_history.score_series(df)
    X = pd.DataFrame(index=df.index)
    X["score"] = scores / 100
    X["score_pente"] = (scores - scores.shift(5)) / 100
    X["rsi_n"] = df["rsi14"] / 100
    X["bb_pct"] = df["bb_pct"]
    X["vol_rel"] = rendements.rolling(20).std() / rendements.rolling(250).std()
    X["dist_sma50"] = close / df["sma50"] - 1
    X["dist_sma200"] = close / df["sma200"] - 1
    X["dd"] = df["dd"]
    X["atr_n"] = df["atr14"] / close
    X["mom_20"] = close.pct_change(20)
    X["mom_60"] = close.pct_change(60)
    X["vol20"] = df["vol20"]

    macro = ml._macro_features(df.index)
    if macro is not None:
        X = X.join(macro)
    return X


def entrainer(df: pd.DataFrame, horizon: int = 20, seuil_signal: float = 25.0,
              mult_objectif: float = 2.0, mult_stop: float = 1.5,
              seuil_meta: float = 0.55, min_train: int = 300,
              test_size: int = 63, fee_bps: float = 10.0) -> dict:
    """Walk-forward du méta-modèle et comparaison avec le signal primaire seul.

    Le méta-modèle n'est entraîné QUE sur les dates où le signal primaire est
    actif : c'est tout son intérêt — il apprend à distinguer les bons des
    mauvais moments pour suivre ce signal.
    """
    if "sma200" not in df.columns:
        df = indicators.enrich(df)

    signaux = signaux_primaires(df, seuil_signal)
    X_tout = _caracteristiques(df)
    valides = X_tout.notna().all(axis=1)
    positions_signal = np.where((signaux.to_numpy() == 1) & valides.to_numpy())[0]
    if len(positions_signal) < min_train + test_size:
        raise RuntimeError(
            f"Trop peu de signaux primaires ({len(positions_signal)}) — "
            f"minimum {min_train + test_size}. Abaisser seuil_signal ou "
            "allonger l'historique.")

    barrieres = triple_barriere(df, positions_signal, horizon,
                                mult_objectif, mult_stop)
    positions = barrieres["positions"]
    y = barrieres["labels"]
    X = X_tout.iloc[positions]

    # walk-forward : entraînement sur le passé uniquement
    proba = np.full(len(y), np.nan)
    folds = []
    debut = min_train
    while debut < len(y):
        fin = min(debut + test_size, len(y))
        modele = HistGradientBoostingClassifier(
            max_iter=200, max_depth=3, learning_rate=0.05,
            l2_regularization=1.0, random_state=42)
        modele.fit(X.iloc[:debut], y[:debut])
        p = modele.predict_proba(X.iloc[debut:fin])[:, 1]
        proba[debut:fin] = p
        y_test = y[debut:fin]
        auc = roc_auc_score(y_test, p) if len(set(y_test)) > 1 else np.nan
        folds.append({
            "debut": df.index[positions[debut]].date().isoformat(),
            "fin": df.index[positions[fin - 1]].date().isoformat(),
            "n": int(fin - debut),
            "auc": round(float(auc), 3) if auc == auc else None,
            "taux_gagnants_reel_%": round(float(np.mean(y_test)) * 100, 1),
        })
        debut = fin

    hors_echantillon = ~np.isnan(proba)
    if hors_echantillon.sum() < 30:
        raise RuntimeError("Trop peu de points hors échantillon")

    rend = barrieres["rendements"][hors_echantillon]
    lab = y[hors_echantillon]
    p_oos = proba[hors_echantillon]
    pos_oos = positions[hors_echantillon]
    dur_oos = barrieres["durees"][hors_echantillon]
    frais = fee_bps / 10_000 * 2  # aller-retour

    # tous les signaux primaires suivis vs seulement ceux validés par le méta
    net_primaire = rend - frais
    retenus = p_oos >= seuil_meta
    net_meta = net_primaire[retenus]

    # séquences réellement exécutables (un seul trade à la fois)
    seq_p = _sequentiels(pos_oos, dur_oos)
    seq_m = _sequentiels(pos_oos[retenus], dur_oos[retenus])

    aucs = [f["auc"] for f in folds if f["auc"] is not None]
    return {
        "horizon": horizon,
        "seuil_signal": seuil_signal,
        "seuil_meta": seuil_meta,
        "n_signaux_primaires": int(hors_echantillon.sum()),
        "n_retenus_par_meta": int(retenus.sum()),
        "part_filtree_%": round(float(1 - retenus.mean()) * 100, 1),
        "meta_auc_moyen": round(float(np.mean(aucs)), 3) if aucs else None,
        "primaire": _stats(net_primaire, lab, net_primaire[seq_p]),
        "avec_meta": _stats(net_meta, lab[retenus], net_meta[seq_m]),
        "folds": folds,
        "_proba": proba,
        "_positions": positions,
        "_index": df.index,
    }


def _sequentiels(positions: np.ndarray, durees: np.ndarray) -> np.ndarray:
    """Masque des trades NON chevauchants (un seul en cours à la fois).

    Les signaux étant quotidiens, des centaines de trades se recouvrent :
    composer leurs rendements donnerait un cumul fantaisiste, impossible à
    réaliser avec un capital unique. On ne garde donc, pour la performance,
    que la séquence réellement exécutable.
    """
    masque = np.zeros(len(positions), dtype=bool)
    libre_a_partir_de = -1
    for i, (pos, duree) in enumerate(zip(positions, durees)):
        if pos >= libre_a_partir_de:
            masque[i] = True
            libre_a_partir_de = pos + duree
    return masque


def _stats(rendements: np.ndarray, labels: np.ndarray,
           rendements_sequentiels: np.ndarray | None = None) -> dict:
    """Statistiques d'un ensemble de trades (rendements nets de frais).

    Les moyennes portent sur tous les signaux ; le cumul réalisable n'est
    calculé que sur la séquence non chevauchante.
    """
    if len(rendements) == 0:
        return {"n_trades": 0}
    gains = rendements[rendements > 0]
    pertes = rendements[rendements <= 0]
    facteur = (gains.sum() / abs(pertes.sum())) if len(pertes) and pertes.sum() != 0 \
        else float("inf")
    stats = {
        "n_trades": int(len(rendements)),
        "taux_reussite_%": round(float(np.mean(labels)) * 100, 1),
        "rendement_moyen_%": round(float(np.mean(rendements)) * 100, 2),
        "rendement_median_%": round(float(np.median(rendements)) * 100, 2),
        "facteur_profit": round(float(facteur), 2) if np.isfinite(facteur) else None,
        "esperance_%": round(float(np.mean(rendements)) * 100, 3),
        "pire_trade_%": round(float(np.min(rendements)) * 100, 2),
    }
    if rendements_sequentiels is not None and len(rendements_sequentiels):
        stats["n_trades_sequentiels"] = int(len(rendements_sequentiels))
        stats["cumule_sequentiel_%"] = round(
            (float(np.prod(1 + rendements_sequentiels)) - 1) * 100, 1)
    return stats


def synthese(res: dict) -> str:
    """Verdict lisible : le filtrage apporte-t-il quelque chose ?

    Le juge de paix est le **cumul de la séquence exécutable** (un seul trade à
    la fois), pas l'espérance par trade : celle-ci porte sur des signaux
    chevauchants et surpondère les périodes où ils sont denses. Un méta-modèle
    peut très bien relever l'espérance moyenne tout en dégradant ce qu'un
    capital unique aurait réellement gagné — le cas doit être signalé, pas
    masqué.
    """
    p, m = res["primaire"], res["avec_meta"]
    if m.get("n_trades", 0) == 0:
        return ("Le méta-modèle rejette tous les signaux au seuil retenu : "
                "abaisser seuil_meta.")

    gain_esperance = m["esperance_%"] - p["esperance_%"]
    cumul_p = p.get("cumule_sequentiel_%")
    cumul_m = m.get("cumule_sequentiel_%")
    if cumul_p is None or cumul_m is None:
        return (f"Espérance par trade : {p['esperance_%']:+.3f} % → "
                f"{m['esperance_%']:+.3f} %. Séquence exécutable non "
                "calculable : verdict indisponible.")

    detail = (f"Séquence réellement exécutable : {cumul_p:+.1f} % "
              f"({p['n_trades_sequentiels']} trades) → {cumul_m:+.1f} % "
              f"({m['n_trades_sequentiels']} trades). Espérance par trade "
              f"{p['esperance_%']:+.3f} % → {m['esperance_%']:+.3f} %. "
              f"{res['part_filtree_%']} % des signaux écartés.")

    if cumul_m > cumul_p and gain_esperance > 0:
        return ("Le filtrage améliore À LA FOIS la performance exécutable et "
                f"l'espérance par trade : le méta-modèle apporte de la valeur. {detail}")
    if cumul_m > cumul_p:
        return ("Le filtrage améliore la performance exécutable malgré une "
                f"espérance par trade en baisse. Gain plausible. {detail}")
    if gain_esperance > 0:
        return ("⚠️ CONTRADICTION : l'espérance par trade progresse, mais la "
                "performance réellement exécutable RECULE. Le méta-modèle "
                "écarte des signaux qui, dans la séquence, comptaient. Ne pas "
                f"s'en servir sur ce titre. {detail}")
    return ("Le filtrage dégrade tout : ni la performance exécutable, ni "
            f"l'espérance ne s'améliorent. Ne pas l'utiliser ici. {detail}")


def analyser(symbole: str, horizon: int = 20, seuil_meta: float = 0.55,
             lookback_days: int = 3650) -> dict:
    """Analyse complète pour un titre, prête à afficher."""
    from marketlab.data import get_ohlcv
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=lookback_days))
    res = entrainer(df, horizon=horizon, seuil_meta=seuil_meta)
    public = {k: v for k, v in res.items() if not k.startswith("_")}
    public["symbole"] = symbole
    public["synthese"] = synthese(res)
    return public
