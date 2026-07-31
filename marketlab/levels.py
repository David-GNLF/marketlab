"""Niveaux techniques et plan de position chiffré.

Traduit l'analyse en nombres exploitables : où sont les zones de prix qui ont
compté historiquement, où placer un stop, quel objectif viser, et — via les
simulations de `forecast` — quelle est la probabilité réelle de toucher l'un
avant l'autre.

Le plan produit est une PROPOSITION à examiner, jamais un ordre.
"""

import numpy as np
import pandas as pd

from marketlab import couts, events, forecast, har, indicators
from marketlab.data import get_ohlcv


# --- Zones de prix ----------------------------------------------------------

def extremes_locaux(df: pd.DataFrame, fenetre: int = 5) -> tuple[pd.Series, pd.Series]:
    """Sommets et creux locaux : un point plus haut (bas) que ses voisins."""
    haut, bas = df["high"], df["low"]
    est_sommet = haut == haut.rolling(2 * fenetre + 1, center=True).max()
    est_creux = bas == bas.rolling(2 * fenetre + 1, center=True).min()
    return haut[est_sommet], bas[est_creux]


def zones(df: pd.DataFrame, fenetre: int = 5, tolerance: float = 0.015,
          min_touches: int = 2) -> list[dict]:
    """Regroupe les extrêmes proches en zones de support/résistance.

    Une zone touchée plusieurs fois pèse davantage : `touches` est l'indicateur
    de solidité. `tolerance` est la largeur relative du regroupement (1,5 %).
    """
    sommets, creux = extremes_locaux(df, fenetre)
    points = pd.concat([sommets, creux]).sort_values()
    if points.empty:
        return []

    groupes, courant = [], [float(points.iloc[0])]
    for p in points.iloc[1:]:
        p = float(p)
        if abs(p - np.mean(courant)) / np.mean(courant) <= tolerance:
            courant.append(p)
        else:
            groupes.append(courant)
            courant = [p]
    groupes.append(courant)

    prix = float(df["close"].iloc[-1])
    resultat = []
    for g in groupes:
        if len(g) < min_touches:
            continue
        niveau = float(np.mean(g))
        resultat.append({
            "niveau": round(niveau, 4),
            "touches": len(g),
            "type": "résistance" if niveau > prix else "support",
            "distance_%": round((niveau / prix - 1) * 100, 2),
        })
    return sorted(resultat, key=lambda z: z["niveau"])


def pivots(df: pd.DataFrame) -> dict:
    """Points pivots classiques, calculés sur la dernière séance complète."""
    d = df.iloc[-1]
    h, b, c = float(d["high"]), float(d["low"]), float(d["close"])
    pp = (h + b + c) / 3
    return {
        "PP": round(pp, 4),
        "R1": round(2 * pp - b, 4), "S1": round(2 * pp - h, 4),
        "R2": round(pp + (h - b), 4), "S2": round(pp - (h - b), 4),
        "R3": round(h + 2 * (pp - b), 4), "S3": round(b - 2 * (h - pp), 4),
    }


def zones_proches(df: pd.DataFrame, n: int = 3) -> dict:
    """Les n supports sous le cours et les n résistances au-dessus."""
    toutes = zones(df)
    prix = float(df["close"].iloc[-1])
    supports = [z for z in toutes if z["niveau"] < prix][-n:]
    resistances = [z for z in toutes if z["niveau"] > prix][:n]
    return {"prix": round(prix, 4), "supports": supports[::-1],
            "resistances": resistances}


# --- Plan de position -------------------------------------------------------

def plan(symbole: str, sens: str = "achat", horizon: int = 20,
         risque_pct: float = 1.0, capital: float | None = None,
         lookback_days: int = 1825) -> dict:
    """Plan chiffré : entrée, stop, objectif, probabilités, taille.

    Le stop s'appuie sur la volatilité réelle (2×ATR) ET sur le support/la
    résistance la plus proche : le plus protecteur des deux l'emporte, pour
    éviter un stop posé au milieu d'une zone de bruit.
    """
    if sens not in ("achat", "vente"):
        raise RuntimeError("sens doit être 'achat' ou 'vente'")
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=lookback_days))
    prix = float(df["close"].iloc[-1])
    atr = float(df["atr14"].iloc[-1])
    zp = zones_proches(df)
    proj = forecast.projeter(df, horizon=horizon,
                             vol_cible=har.vol_cible(symbole))

    # Le stop doit être à la mesure de la DURÉE de détention : la volatilité
    # croît comme la racine du temps. Un stop de 2×ATR convient à 20 séances ;
    # appliqué tel quel à 5 séances, il laisserait courir une perte bien plus
    # longue que le pari lui-même. Le multiplicateur suit donc √(horizon/20)
    # — et vaut exactement 2 à 20 séances : l'historique est préservé.
    mult = 2.0 * np.sqrt(max(horizon, 1) / 20)
    # Borne : même un support éloigné ne doit pas produire un stop que la
    # fenêtre n'a aucune chance d'atteindre autrement que par accident.
    mult_max = 1.5 * mult

    if sens == "achat":
        stop_atr = prix - mult * atr
        appui = zp["supports"][0]["niveau"] if zp["supports"] else None
        stop = min(stop_atr, appui * 0.995) if appui else stop_atr
        stop = max(stop, prix - mult_max * atr)
        cible_zone = zp["resistances"][0]["niveau"] if zp["resistances"] else None
        cible_proj = proj["intervalle_80"][1]
        objectif = min(cible_zone, cible_proj) if cible_zone else cible_proj
    else:
        stop_atr = prix + mult * atr
        appui = zp["resistances"][0]["niveau"] if zp["resistances"] else None
        stop = max(stop_atr, appui * 1.005) if appui else stop_atr
        stop = min(stop, prix + mult_max * atr)
        cible_zone = zp["supports"][0]["niveau"] if zp["supports"] else None
        cible_proj = proj["intervalle_80"][0]
        objectif = max(cible_zone, cible_proj) if cible_zone else cible_proj

    risque_unitaire = abs(prix - stop)
    gain_unitaire = abs(objectif - prix)
    ratio = gain_unitaire / risque_unitaire if risque_unitaire > 0 else 0.0

    p_stop = forecast.proba_atteindre(proj, stop)
    p_objectif = forecast.proba_atteindre(proj, objectif)
    esperance = (p_objectif / 100 * gain_unitaire) - (p_stop / 100 * risque_unitaire)

    plan_resultat = {
        "symbole": symbole, "sens": sens, "horizon": horizon,
        "entree": round(prix, 4),
        "stop": round(stop, 4),
        "objectif": round(objectif, 4),
        "risque_%": round(risque_unitaire / prix * 100, 2),
        "gain_potentiel_%": round(gain_unitaire / prix * 100, 2),
        "ratio_gain_risque": round(ratio, 2),
        "proba_toucher_stop_%": p_stop,
        "proba_toucher_objectif_%": p_objectif,
        "esperance_par_unite": round(esperance, 4),
        "esperance_%": round(esperance / prix * 100, 2),
        "zones": zp,
        "regime": forecast.regime(df),
    }
    if capital:
        quantite = (capital * risque_pct / 100) / risque_unitaire \
            if risque_unitaire > 0 else 0
        plan_resultat["taille"] = {
            "capital": capital, "risque_pct": risque_pct,
            "quantite": round(quantite, 6),
            "montant": round(quantite * prix, 2),
            "perte_si_stop": round(quantite * risque_unitaire, 2),
        }
    # risque événementiel : une publication de résultats dans l'horizon peut
    # provoquer un écart qui traverse le stop, quelle que soit l'analyse technique
    try:
        plan_resultat["risque_evenement"] = events.risque_evenement(symbole, horizon)
    except Exception as exc:
        plan_resultat["risque_evenement"] = {"concerne": None,
                                             "message": f"indisponible : {str(exc)[:60]}"}

    # COÛT RÉEL. L'espérance ci-dessus est BRUTE : elle ignore le spread payé
    # deux fois et le portage de la part empruntée. Sur un horizon de 20
    # séances à effet 5, cela représente couramment plus d'un pour cent — soit
    # davantage que l'espérance de bien des idées « favorables ». Publier le
    # chiffre brut sans le net revient à annoncer un salaire avant charges.
    try:
        plan_resultat["couts"] = couts.net(
            plan_resultat["esperance_%"], symbole, horizon=horizon)
        plan_resultat["esperance_nette_%"] = plan_resultat["couts"]["esperance_nette_%"]
    except Exception as exc:
        plan_resultat["couts"] = {"erreur": str(exc)[:80]}

    plan_resultat["lecture"] = _lecture(ratio, esperance, p_stop)
    if plan_resultat.get("couts", {}).get("lecture"):
        plan_resultat["lecture"] += " " + plan_resultat["couts"]["lecture"]
    if plan_resultat["risque_evenement"].get("dans_horizon"):
        # le message porte déjà son propre pictogramme
        plan_resultat["lecture"] += " " + plan_resultat["risque_evenement"]["message"]
    return plan_resultat


def _lecture(ratio: float, esperance: float, p_stop: float) -> str:
    if esperance <= 0:
        return ("Espérance NÉGATIVE d'après les simulations : le stop est plus "
                "susceptible d'être touché que l'objectif. Configuration à écarter.")
    if ratio < 1.5:
        return (f"Espérance positive mais ratio gain/risque faible ({ratio:.2f}) : "
                "il faut un taux de réussite élevé pour que ça paie.")
    if p_stop > 55:
        return (f"Ratio correct ({ratio:.2f}) mais stop touché dans {p_stop} % des "
                "simulations : envisager un stop plus large et une taille réduite.")
    return (f"Configuration cohérente : ratio {ratio:.2f}, stop touché dans "
            f"{p_stop} % des simulations.")
