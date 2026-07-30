"""Prévision probabiliste : où le cours peut aller, avec quelles probabilités.

Principe assumé : la DIRECTION d'un cours est quasi imprévisible (marchés
proches de l'efficience), mais trois choses le sont bien davantage :

1. **La volatilité** — elle est persistante (clustering) et se prévoit
   correctement par EWMA/GARCH. C'est ce qui dimensionne une position.
2. **La distribution des prix futurs** — simulable par bootstrap par blocs des
   rendements passés, qui préserve queues épaisses et clustering (contrairement
   à une loi normale qui sous-estime gravement les mouvements extrêmes).
3. **Les analogues historiques** — « quand la configuration ressemblait à
   aujourd'hui, qu'est-il arrivé ensuite ? » : distribution empirique, sans
   modèle paramétrique.

Ce module produit donc des CÔNES et des PROBABILITÉS, jamais un prix cible
unique. La qualité se juge par `calibration()` : un intervalle annoncé à 80 %
doit contenir la réalité ~80 % du temps. Un intervalle mal calibré est pire
qu'inutile — il donne une fausse confiance.
"""

import numpy as np
import pandas as pd

from marketlab import indicators
from marketlab.data import get_ohlcv

PERIODES_AN = 252


# --- Volatilité -------------------------------------------------------------

def volatilite_ewma(rendements: pd.Series, lam: float = 0.94) -> float:
    """Volatilité journalière prévue (RiskMetrics, λ=0.94).

    Moyenne exponentiellement pondérée du carré des rendements : les jours
    récents pèsent plus, ce qui capture le clustering de volatilité.
    """
    r = rendements.dropna().to_numpy()
    if len(r) < 20:
        raise RuntimeError("Historique insuffisant pour estimer la volatilité")
    poids = lam ** np.arange(len(r))[::-1]
    poids /= poids.sum()
    return float(np.sqrt(np.sum(poids * r**2)))


def garch11(rendements: pd.Series, max_iter: int = 200) -> dict | None:
    """GARCH(1,1) estimé par maximum de vraisemblance (scipy).

    Renvoie les paramètres, la volatilité prévue à 1 jour et la volatilité
    long terme. None si l'optimisation ne converge pas — l'appelant retombe
    alors sur EWMA.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        return None
    r = rendements.dropna().to_numpy()
    if len(r) < 250:
        return None
    r = r - r.mean()
    var_ech = float(np.var(r))

    def neg_log_vraisemblance(p):
        omega, alpha, beta = p
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.999:
            return 1e10
        var = np.empty(len(r))
        var[0] = var_ech
        for t in range(1, len(r)):
            var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
        var = np.maximum(var, 1e-12)
        return float(0.5 * np.sum(np.log(var) + r**2 / var))

    depart = [var_ech * 0.05, 0.08, 0.90]
    res = minimize(neg_log_vraisemblance, depart, method="Nelder-Mead",
                   options={"maxiter": max_iter, "fatol": 1e-6})
    if not res.success:
        return None
    omega, alpha, beta = res.x
    if alpha + beta >= 0.999 or omega <= 0:
        return None
    # variance conditionnelle du jour suivant
    var = var_ech
    for t in range(1, len(r)):
        var = omega + alpha * r[t - 1] ** 2 + beta * var
    var_suivante = omega + alpha * r[-1] ** 2 + beta * var
    return {
        "omega": float(omega), "alpha": float(alpha), "beta": float(beta),
        "persistance": float(alpha + beta),
        "vol_jour": float(np.sqrt(var_suivante)),
        "vol_long_terme_jour": float(np.sqrt(omega / (1 - alpha - beta))),
    }


# --- Projection Monte Carlo -------------------------------------------------

def _bootstrap_blocs(rendements: np.ndarray, horizon: int, n_sim: int,
                     taille_bloc: int, rng: np.random.Generator) -> np.ndarray:
    """Trajectoires de rendements par ré-échantillonnage de blocs consécutifs.

    Le bloc préserve l'autocorrélation locale et le clustering de volatilité,
    que le tirage indépendant détruirait.
    """
    n = len(rendements)
    n_blocs = int(np.ceil(horizon / taille_bloc))
    departs = rng.integers(0, n - taille_bloc, size=(n_sim, n_blocs))
    traj = np.empty((n_sim, n_blocs * taille_bloc))
    for j in range(n_blocs):
        idx = departs[:, j][:, None] + np.arange(taille_bloc)[None, :]
        traj[:, j * taille_bloc:(j + 1) * taille_bloc] = rendements[idx]
    return traj[:, :horizon]


# Bornes du recalage de volatilité. Une prévision aberrante (titre nouvellement
# coté, relevé incomplet, séance de krach) ne doit jamais pouvoir tripler ou
# diviser par trois la largeur du cône : au-delà, on préfère la volatilité
# historique, moins fine mais jamais absurde.
FACTEUR_VOL_MIN = 0.33
FACTEUR_VOL_MAX = 3.0


def projeter(df: pd.DataFrame, horizon: int = 20, n_sim: int = 20_000,
             taille_bloc: int = 5, lookback: int = 750,
             seed: int = 42, vol_cible: float | None = None) -> dict:
    """Cône de prix simulé à `horizon` séances.

    Renvoie les quantiles de prix par séance, les probabilités utiles et les
    mesures de risque (VaR, perte moyenne dans le pire décile).

    `vol_cible` — écart-type QUOTIDIEN visé pour les rendements simulés.

    À QUOI ÇA SERT. Sans `vol_cible`, la largeur du cône vient du
    rééchantillonnage des 750 dernières séances : c'est la volatilité
    INCONDITIONNELLE sur trois ans. Le cône ne se resserre donc pas quand le
    marché est calme et ne s'élargit pas quand il se tend, alors que c'est
    précisément l'information qui dimensionne une position. Avec `vol_cible`,
    les rendements simulés sont recentrés puis redimensionnés pour atteindre
    cette volatilité, ce qui laisse INTACTES la structure de blocs (donc le
    clustering) et l'épaisseur des queues : seule l'échelle change.

    La dérive par séance est préservée : on ne redimensionne que les écarts à
    la moyenne, jamais la moyenne elle-même — sinon changer la volatilité
    déplacerait aussi le prix médian attendu.
    """
    close = df["close"].dropna()
    rendements = np.log(close / close.shift(1)).dropna()
    if len(rendements) < 120:
        raise RuntimeError(f"Historique insuffisant ({len(rendements)} séances)")
    r = rendements.tail(lookback).to_numpy()
    prix0 = float(close.iloc[-1])

    rng = np.random.default_rng(seed)
    traj = _bootstrap_blocs(r, horizon, n_sim, taille_bloc, rng)

    facteur = None
    if vol_cible is not None and vol_cible > 0:
        vol_echantillon = float(r.std(ddof=1))
        if vol_echantillon > 0:
            facteur = float(np.clip(vol_cible / vol_echantillon,
                                    FACTEUR_VOL_MIN, FACTEUR_VOL_MAX))
            derive = float(r.mean())
            traj = derive + (traj - derive) * facteur

    chemins = prix0 * np.exp(np.cumsum(traj, axis=1))  # (n_sim, horizon)

    niveaux = [5, 10, 25, 50, 75, 90, 95]
    quantiles = {f"q{n}": np.percentile(chemins, n, axis=0) for n in niveaux}
    final = chemins[:, -1]
    rend_final = final / prix0 - 1

    # probabilité de toucher un niveau à un moment quelconque (pas seulement à l'échéance)
    plus_bas = chemins.min(axis=1)
    plus_haut = chemins.max(axis=1)

    return {
        "prix_actuel": round(prix0, 4),
        "horizon": horizon,
        "n_simulations": n_sim,
        "vol_cible_jour": round(vol_cible, 6) if vol_cible else None,
        "facteur_volatilite": round(facteur, 3) if facteur else None,
        "quantiles": {k: np.round(v, 4).tolist() for k, v in quantiles.items()},
        "prix_median": round(float(np.median(final)), 4),
        "intervalle_80": [round(float(np.percentile(final, 10)), 4),
                          round(float(np.percentile(final, 90)), 4)],
        "intervalle_50": [round(float(np.percentile(final, 25)), 4),
                          round(float(np.percentile(final, 75)), 4)],
        "proba_hausse_%": round(float((rend_final > 0).mean()) * 100, 1),
        "proba_gain_5_%": round(float((rend_final > 0.05).mean()) * 100, 1),
        "proba_perte_5_%": round(float((rend_final < -0.05).mean()) * 100, 1),
        "rendement_median_%": round(float(np.median(rend_final)) * 100, 2),
        "var_95_%": round(float(np.percentile(rend_final, 5)) * 100, 2),
        "perte_moyenne_pire_5_%": round(
            float(rend_final[rend_final <= np.percentile(rend_final, 5)].mean()) * 100, 2),
        "_chemins_min": plus_bas,   # usage interne (probas de toucher)
        "_chemins_max": plus_haut,
        "_rend_final": rend_final,
    }


def proba_atteindre(projection: dict, niveau: float, sens: str = "auto") -> float:
    """Probabilité que le cours TOUCHE `niveau` pendant l'horizon (%).

    Distinct de « finir au-dessus » : un stop est touché en séance, même si le
    cours referme plus haut.
    """
    prix0 = projection["prix_actuel"]
    if sens == "auto":
        sens = "haut" if niveau > prix0 else "bas"
    if sens == "haut":
        return round(float((projection["_chemins_max"] >= niveau).mean()) * 100, 1)
    return round(float((projection["_chemins_min"] <= niveau).mean()) * 100, 1)


# --- Régime de marché -------------------------------------------------------

def regime(df: pd.DataFrame) -> dict:
    """Régime courant : direction de tendance + niveau de volatilité.

    La stratégie qui marche dépend du régime : suivre la tendance en marché
    directionnel, jouer les extrêmes en range.
    """
    if "sma200" not in df.columns:
        df = indicators.enrich(df)
    close = df["close"]
    rendements = close.pct_change().dropna()
    vol_courante = float(rendements.tail(20).std() * np.sqrt(PERIODES_AN))
    vol_historique = float(rendements.tail(500).std() * np.sqrt(PERIODES_AN))
    ratio_vol = vol_courante / vol_historique if vol_historique > 0 else 1.0

    s50, s200 = df["sma50"].iloc[-1], df["sma200"].iloc[-1]
    prix = float(close.iloc[-1])
    pente50 = float(df["sma50"].iloc[-1] / df["sma50"].iloc[-20] - 1) \
        if len(df) > 20 and pd.notna(df["sma50"].iloc[-20]) else 0.0

    if pd.notna(s200) and prix > s200 and pd.notna(s50) and s50 > s200 and pente50 > 0:
        tendance = "haussier"
    elif pd.notna(s200) and prix < s200 and pd.notna(s50) and s50 < s200 and pente50 < 0:
        tendance = "baissier"
    else:
        tendance = "sans direction"

    niveau_vol = ("élevée" if ratio_vol > 1.3 else
                  "faible" if ratio_vol < 0.75 else "normale")
    conseils = {
        ("haussier", "faible"): "Contexte porteur et calme : suivi de tendance favorisé.",
        ("haussier", "normale"): "Tendance haussière : privilégier les replis vers les moyennes.",
        ("haussier", "élevée"): "Hausse nerveuse : réduire la taille, élargir les stops.",
        ("baissier", "faible"): "Baisse ordonnée : rebonds à vendre plutôt qu'à acheter.",
        ("baissier", "normale"): "Tendance baissière : prudence sur les achats à contre-courant.",
        ("baissier", "élevée"): "Baisse violente : capital à protéger, taille réduite.",
        ("sans direction", "faible"): "Range calme : stratégies de retour à la moyenne.",
        ("sans direction", "normale"): "Pas de direction claire : patience, attendre la sortie.",
        ("sans direction", "élevée"): "Agitation sans direction : le pire contexte, s'abstenir.",
    }
    return {
        "tendance": tendance,
        "volatilite": niveau_vol,
        "vol_annuelle_courante_%": round(vol_courante * 100, 1),
        "vol_annuelle_moyenne_%": round(vol_historique * 100, 1),
        "ratio_vol": round(ratio_vol, 2),
        "lecture": conseils[(tendance, niveau_vol)],
    }


# --- Analogues historiques --------------------------------------------------

CARACTERISTIQUES = ["rsi14", "bb_pct", "dist_sma50", "dist_sma200", "vol_rel",
                    "mom_20"]


def _caracteristiques(df: pd.DataFrame) -> pd.DataFrame:
    if "sma200" not in df.columns:
        df = indicators.enrich(df)
    close = df["close"]
    rendements = close.pct_change()
    X = pd.DataFrame(index=df.index)
    X["rsi14"] = df["rsi14"] / 100
    X["bb_pct"] = df["bb_pct"]
    X["dist_sma50"] = close / df["sma50"] - 1
    X["dist_sma200"] = close / df["sma200"] - 1
    X["vol_rel"] = (rendements.rolling(20).std()
                    / rendements.rolling(250).std())
    X["mom_20"] = close.pct_change(20)
    return X


def analogues(df: pd.DataFrame, horizon: int = 20, k: int = 60) -> dict:
    """Que s'est-il passé quand la configuration ressemblait à aujourd'hui ?

    Cherche les `k` séances passées les plus proches de la configuration
    actuelle (indicateurs normalisés), puis renvoie la distribution empirique
    des rendements survenus dans les `horizon` séances suivantes.
    Aucune extrapolation : ce sont des faits historiques observés.
    """
    X = _caracteristiques(df).dropna()
    if len(X) < 250 + horizon:
        raise RuntimeError(f"Historique insuffisant pour les analogues ({len(X)})")
    close = df["close"]
    futur = (close.pct_change(horizon).shift(-horizon)).reindex(X.index)

    # normalisation par écart-type pour rendre les distances comparables
    ecarts = X.std().replace(0, 1e-9)
    Xn = X / ecarts
    courant = Xn.iloc[-1]

    # candidats : tout sauf les `horizon` dernières séances (futur inconnu)
    candidats = Xn.iloc[:-horizon]
    futur_c = futur.iloc[:-horizon]
    valides = futur_c.notna()
    candidats, futur_c = candidats[valides], futur_c[valides]
    if len(candidats) < k:
        raise RuntimeError("Pas assez de configurations comparables")

    distances = np.sqrt(((candidats - courant) ** 2).sum(axis=1))
    proches = distances.nsmallest(k).index
    rend = futur_c.loc[proches] * 100

    return {
        "horizon": horizon,
        "k": k,
        "proba_hausse_%": round(float((rend > 0).mean()) * 100, 1),
        "rendement_median_%": round(float(rend.median()), 2),
        "rendement_moyen_%": round(float(rend.mean()), 2),
        "meilleur_%": round(float(rend.max()), 2),
        "pire_%": round(float(rend.min()), 2),
        "quartile_bas_%": round(float(rend.quantile(0.25)), 2),
        "quartile_haut_%": round(float(rend.quantile(0.75)), 2),
        "dates_proches": [d.strftime("%Y-%m-%d") for d in proches[:8]],
        "distance_moyenne": round(float(distances.loc[proches].mean()), 3),
    }


# --- Calibration (contrôle qualité) -----------------------------------------

def calibration(df: pd.DataFrame, horizon: int = 20, n_tests: int = 120,
                n_sim: int = 4000, seed: int = 42) -> dict:
    """Les intervalles annoncés tiennent-ils leurs promesses ?

    Rejoue la projection à `n_tests` dates passées (uniquement avec les données
    disponibles à l'époque) et compte la fréquence réelle de couverture.
    Un intervalle à 80 % doit couvrir ~80 % : nettement moins = fausse
    confiance, nettement plus = intervalles trop larges donc peu utiles.
    """
    close = df["close"].dropna()
    besoin = 300
    if len(close) < besoin + horizon + n_tests:
        n_tests = max(20, len(close) - besoin - horizon)
    positions = np.linspace(besoin, len(close) - horizon - 1, n_tests, dtype=int)

    couvert_80 = couvert_50 = 0
    dans_le_bon_sens = 0
    total = 0
    for pos in positions:
        histo = df.iloc[:pos + 1]
        reel = float(close.iloc[pos + horizon])
        try:
            p = projeter(histo, horizon=horizon, n_sim=n_sim, seed=seed)
        except RuntimeError:
            continue
        b80, h80 = p["intervalle_80"]
        b50, h50 = p["intervalle_50"]
        couvert_80 += int(b80 <= reel <= h80)
        couvert_50 += int(b50 <= reel <= h50)
        hausse_reelle = reel > p["prix_actuel"]
        hausse_prevue = p["proba_hausse_%"] > 50
        dans_le_bon_sens += int(hausse_reelle == hausse_prevue)
        total += 1

    if total == 0:
        raise RuntimeError("Aucun test de calibration exploitable")
    return {
        "n_tests": total,
        "horizon": horizon,
        "couverture_80_%": round(couvert_80 / total * 100, 1),
        "couverture_50_%": round(couvert_50 / total * 100, 1),
        "direction_correcte_%": round(dans_le_bon_sens / total * 100, 1),
        "verdict": _verdict_calibration(couvert_80 / total * 100),
    }


def _verdict_calibration(couverture_80: float) -> str:
    if 72 <= couverture_80 <= 88:
        return "Intervalles fiables (couverture proche des 80 % annoncés)."
    if couverture_80 < 72:
        return ("Intervalles TROP ÉTROITS : le risque réel dépasse l'annonce. "
                "Élargir les stops en conséquence.")
    return ("Intervalles trop larges : prudents mais peu informatifs pour "
            "dimensionner finement.")


# --- Synthèse ---------------------------------------------------------------

def analyser(symbole: str, horizon: int = 20, lookback_days: int = 1825) -> dict:
    """Analyse prévisionnelle complète d'un titre."""
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=lookback_days))
    rendements = df["close"].pct_change().dropna()

    proj = projeter(df, horizon=horizon)
    vol_ewma_j = volatilite_ewma(rendements)
    g = garch11(rendements)

    resultat = {
        "symbole": symbole,
        "regime": regime(df),
        "volatilite": {
            "ewma_jour_%": round(vol_ewma_j * 100, 2),
            "ewma_annuelle_%": round(vol_ewma_j * np.sqrt(PERIODES_AN) * 100, 1),
            "garch": ({"vol_annuelle_%": round(g["vol_jour"] * np.sqrt(PERIODES_AN) * 100, 1),
                       "vol_long_terme_%": round(g["vol_long_terme_jour"] * np.sqrt(PERIODES_AN) * 100, 1),
                       "persistance": round(g["persistance"], 3)} if g else None),
        },
        "projection": {k: v for k, v in proj.items() if not k.startswith("_")},
    }
    try:
        resultat["analogues"] = analogues(df, horizon=horizon)
    except RuntimeError as exc:
        resultat["analogues"] = {"erreur": str(exc)}
    return resultat
