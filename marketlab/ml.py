"""Modèles ML de direction de marché, validés en walk-forward.

Cible : le rendement des `horizon` prochaines bougies est-il positif ?
Validation walk-forward : entraînement sur une fenêtre passée croissante,
test sur le bloc suivant, ré-entraînement à chaque pas — jamais de fuite du
futur. Les métriques à regarder : AUC moyen (0.50 = pile ou face ; en
finance, 0.53-0.55 stable est déjà notable) et la courbe d'équité nette de
frais face au Buy & Hold. Se méfier de tout résultat trop beau.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from marketlab import indicators

FEATURES = [
    "ret_1d", "ret_5d", "ret_20d", "ret_60d", "rsi_n", "macd_n", "bb_pct",
    "vol20", "dist_sma50", "dist_sma200", "dd", "atr_n", "vol_z",
]
MACRO_FEATURES = ["vix_n", "curve_10y2y", "dxy_ret20", "rate10_chg20"]


def _macro_features(index: pd.DatetimeIndex) -> pd.DataFrame | None:
    """Contexte macro US aligné sur l'index du titre (ffill : dernière valeur
    connue à chaque date, donc sans fuite du futur). None si FRED inaccessible."""
    from marketlab.data import fred
    try:
        vix = fred.get_series("VIXCLS", lookback_years=12)
        curve = fred.get_series("T10Y2Y", lookback_years=12)
        dxy = fred.get_series("DTWEXBGS", lookback_years=12)
        r10 = fred.get_series("DGS10", lookback_years=12)
    except Exception:
        return None
    m = pd.DataFrame(index=index)
    m["vix_n"] = vix.reindex(index, method="ffill") / 100
    m["curve_10y2y"] = curve.reindex(index, method="ffill")
    m["dxy_ret20"] = dxy.pct_change(20).reindex(index, method="ffill")
    m["rate10_chg20"] = r10.diff(20).reindex(index, method="ffill")
    return m


def make_dataset(df: pd.DataFrame, horizon: int = 5,
                 include_macro: bool = True) -> tuple[pd.DataFrame, pd.Series]:
    """Features normalisées par le prix (comparables entre titres) + cible.

    include_macro ajoute le contexte macro US (VIX, courbe des taux, dollar,
    taux 10 ans) — ignoré silencieusement si FRED est inaccessible."""
    if "sma200" not in df.columns:
        df = indicators.enrich(df)
    c = df["close"]
    X = pd.DataFrame(index=df.index)
    X["ret_1d"] = df["ret_1d"]
    X["ret_5d"] = c.pct_change(5)
    X["ret_20d"] = df["ret_20d"]
    X["ret_60d"] = df["ret_60d"]
    X["rsi_n"] = df["rsi14"] / 100
    X["macd_n"] = df["hist"] / c
    X["bb_pct"] = df["bb_pct"]
    X["vol20"] = df["vol20"]
    X["dist_sma50"] = c / df["sma50"] - 1
    X["dist_sma200"] = c / df["sma200"] - 1
    X["dd"] = df["dd"]
    X["atr_n"] = df["atr14"] / c
    vol = df["volume"].replace(0, np.nan)
    X["vol_z"] = ((vol - vol.rolling(20).mean()) / vol.rolling(20).std()).fillna(0.0)

    if include_macro:
        m = _macro_features(df.index)
        if m is not None:
            X = X.join(m)

    y = (c.pct_change(horizon).shift(-horizon) > 0).astype(int)
    valid = X.notna().all(axis=1) & c.pct_change(horizon).shift(-horizon).notna()
    return X[valid], y[valid]


def walk_forward(df: pd.DataFrame, horizon: int = 5, min_train: int = 250,
                 test_size: int = 63, threshold: float = 0.55,
                 fee_bps: float = 10.0, periods_per_year: int = 252) -> dict:
    """Walk-forward complet : probabilités hors échantillon + stratégie associée.

    Stratégie évaluée : long quand P(hausse) > threshold, cash sinon,
    exposition décalée d'une bougie, frais sur chaque changement.
    """
    X, y = make_dataset(df, horizon)
    if len(X) < min_train + test_size:
        raise RuntimeError(f"Historique insuffisant : {len(X)} points "
                           f"(minimum {min_train + test_size})")

    proba = pd.Series(np.nan, index=X.index)
    folds = []
    start = min_train
    while start < len(X):
        end = min(start + test_size, len(X))
        model = HistGradientBoostingClassifier(
            max_iter=200, max_depth=3, learning_rate=0.05,
            l2_regularization=1.0, random_state=42,
        )
        model.fit(X.iloc[:start], y.iloc[:start])
        p = model.predict_proba(X.iloc[start:end])[:, 1]
        proba.iloc[start:end] = p
        y_test = y.iloc[start:end]
        auc = roc_auc_score(y_test, p) if y_test.nunique() > 1 else np.nan
        folds.append({
            "debut": X.index[start].date().isoformat(),
            "fin": X.index[end - 1].date().isoformat(),
            "n": end - start,
            "auc": round(float(auc), 3) if auc == auc else None,
            "taux_hausse_reel_%": round(float(y_test.mean()) * 100, 1),
        })
        start = end

    proba = proba.dropna()
    y_oos = y.loc[proba.index]
    close = df["close"].loc[proba.index]

    pos = (proba > threshold).astype(float).shift(1).fillna(0.0)
    rets = close.pct_change().fillna(0.0)
    trades = pos.diff().abs().fillna(0.0)
    strat_rets = pos * rets - trades * fee_bps / 10_000
    equity = (1 + strat_rets).cumprod()
    bh_equity = (1 + rets).cumprod()

    vol = float(strat_rets.std()) * np.sqrt(periods_per_year)
    sharpe = (float(strat_rets.mean()) * periods_per_year) / vol if vol > 0 else 0.0
    aucs = [f["auc"] for f in folds if f["auc"] is not None]

    return {
        "proba": proba,
        "equity": equity,
        "bh_equity": bh_equity,
        "folds": pd.DataFrame(folds),
        "metrics": {
            "n_test_total": len(proba),
            "n_folds": len(folds),
            "auc_moyen": round(float(np.mean(aucs)), 3) if aucs else None,
            "auc_min": round(float(np.min(aucs)), 3) if aucs else None,
            "precision_%": round(float((proba.gt(0.5) == y_oos.astype(bool)).mean()) * 100, 1),
            "rendement_strategie_%": round((float(equity.iloc[-1]) - 1) * 100, 1),
            "rendement_buyhold_%": round((float(bh_equity.iloc[-1]) - 1) * 100, 1),
            "sharpe_strategie": round(sharpe, 2),
            "max_drawdown_%": round(float((equity / equity.cummax() - 1).min()) * 100, 1),
            "exposition_%": round(float(pos.mean()) * 100, 0),
            "nb_transactions": int((trades > 0).sum()),
        },
    }


def importance_features(df: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """Importance par permutation sur le dernier quart (indicatif)."""
    from sklearn.inspection import permutation_importance
    X, y = make_dataset(df, horizon)
    cut = int(len(X) * 0.75)
    model = HistGradientBoostingClassifier(max_iter=200, max_depth=3,
                                           learning_rate=0.05, random_state=42)
    model.fit(X.iloc[:cut], y.iloc[:cut])
    imp = permutation_importance(model, X.iloc[cut:], y.iloc[cut:],
                                 n_repeats=5, random_state=42)
    return pd.DataFrame({"feature": X.columns, "importance": imp.importances_mean}) \
        .sort_values("importance", ascending=False).reset_index(drop=True)
