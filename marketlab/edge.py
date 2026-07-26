"""Renforts de position : les filtres qui améliorent réellement un trade.

Quatre outils documentés dans la littérature et intégrés à la stratégie —
chacun renvoie un constat transparent (favorable / défavorable + raison),
jamais un ordre :

- **Confluence multi-horizons** : le score technique est recalculé sur les
  bougies hebdomadaires ; un achat n'est renforcé que si le quotidien ET
  l'hebdomadaire pointent dans le même sens. Trader contre l'horizon
  supérieur est la première cause de faux départs.
- **Force relative** : performance 3 et 6 mois du titre RAPPORTÉE à son
  indice de référence. Le momentum relatif est l'anomalie la plus robuste
  de la recherche empirique (Jegadeesh & Titman, 1993, répliquée depuis sur
  la plupart des marchés).
- **Kelly fractionné** : la fraction optimale théorique de capital découle
  des probabilités du plan (f* = p − (1−p)/b) ; on en prend le QUART, car
  les probabilités sont estimées, et un Kelly plein ruine quiconque
  surestime son avantage. Sert de plafond, jamais de plancher.
- **Stop suiveur Chandelier** (Le Beau) : plus haut des 22 dernières séances
  moins 3×ATR — protège les gains d'une position qui fonctionne, sans
  l'étouffer dans le bruit.
"""

import numpy as np
import pandas as pd

from marketlab import cot, indicators, score_history
from marketlab.data import get_ohlcv

REFERENCES = {
    ".PA": "^FCHI", ".DE": "^GDAXI", ".AS": "^STOXX50E",
    ".T": "^N225", ".HK": "^HSI", ".KS": "^KS11", ".TW": "^TWII",
}
REFERENCE_DEFAUT = "^GSPC"


def _reference(symbole: str) -> str | None:
    if symbole.endswith(("=X", "=F", "USDT")) or symbole.startswith("^"):
        return None  # une devise ou une matière n'a pas d'indice de référence
    for suffixe, indice in REFERENCES.items():
        if symbole.endswith(suffixe):
            return indice
    return REFERENCE_DEFAUT


# --- 1. Confluence multi-horizons -------------------------------------------

def confluence(df: pd.DataFrame) -> dict:
    """Score technique quotidien vs hebdomadaire : concordent-ils ?"""
    quotidien = float(score_history.score_series(df).iloc[-1])
    hebdo_df = pd.DataFrame({
        "open": df["open"].resample("W").first(),
        "high": df["high"].resample("W").max(),
        "low": df["low"].resample("W").min(),
        "close": df["close"].resample("W").last(),
        "volume": df["volume"].resample("W").sum(),
    }).dropna()
    if len(hebdo_df) < 60:
        return {"favorable": None, "quotidien": round(quotidien, 1),
                "hebdomadaire": None,
                "raison": "historique hebdomadaire insuffisant"}
    hebdo = float(score_history.score_series(
        indicators.enrich(hebdo_df)).iloc[-1])
    accord = np.sign(quotidien) == np.sign(hebdo) or abs(hebdo) < 10
    return {
        "favorable": bool(accord),
        "quotidien": round(quotidien, 1),
        "hebdomadaire": round(hebdo, 1),
        "raison": (f"les horizons concordent (jour {quotidien:+.0f}, "
                   f"semaine {hebdo:+.0f})" if accord else
                   f"CONFLIT d'horizons : jour {quotidien:+.0f} mais semaine "
                   f"{hebdo:+.0f} — trader contre l'horizon supérieur est le "
                   "premier pourvoyeur de faux départs"),
    }


# --- 2. Force relative -------------------------------------------------------

def force_relative(symbole: str, df: pd.DataFrame) -> dict:
    """Le titre bat-il son indice sur 3 et 6 mois ?"""
    ref = _reference(symbole)
    if ref is None:
        return {"favorable": None,
                "raison": "pas d'indice de référence pour cet actif"}
    try:
        df_ref = get_ohlcv(ref, lookback_days=1825)
    except Exception:
        return {"favorable": None, "raison": f"indice {ref} indisponible"}
    aligne = pd.DataFrame({"titre": df["close"], "ref": df_ref["close"]}).dropna()
    if len(aligne) < 130:
        return {"favorable": None, "raison": "historique commun insuffisant"}

    ecarts = {}
    for nom, seances in (("3m", 63), ("6m", 126)):
        r_titre = float(aligne["titre"].iloc[-1] / aligne["titre"].iloc[-seances] - 1)
        r_ref = float(aligne["ref"].iloc[-1] / aligne["ref"].iloc[-seances] - 1)
        ecarts[nom] = round((r_titre - r_ref) * 100, 1)

    favorable = ecarts["3m"] > 0 and ecarts["6m"] > 0
    return {
        "favorable": bool(favorable),
        "vs": ref,
        "ecart_3m_pts": ecarts["3m"],
        "ecart_6m_pts": ecarts["6m"],
        "raison": (f"surperforme {ref} de {ecarts['3m']:+.1f} pts sur 3 mois et "
                   f"{ecarts['6m']:+.1f} pts sur 6 mois — le momentum relatif "
                   "est du bon côté" if favorable else
                   f"ne bat pas son indice ({ecarts['3m']:+.1f} pts/3 m, "
                   f"{ecarts['6m']:+.1f} pts/6 m) : acheter un retardataire, "
                   "c'est parier contre le momentum"),
    }


# --- 3. Kelly fractionné -----------------------------------------------------

def kelly_fractionne(plan: dict, fraction: float = 0.25) -> dict:
    """Plafond de taille issu des probabilités du plan (quart de Kelly)."""
    p_obj = plan["proba_toucher_objectif_%"] / 100
    p_stop = plan["proba_toucher_stop_%"] / 100
    if p_obj + p_stop == 0:
        return {"kelly_%": None, "raison": "probabilités indisponibles"}
    p = p_obj / (p_obj + p_stop)          # proba de gagner sachant qu'on sort
    b = plan["ratio_gain_risque"]
    if b <= 0:
        return {"kelly_%": None, "raison": "ratio gain/risque nul"}
    f_etoile = p - (1 - p) / b
    f_pratique = max(0.0, f_etoile * fraction)
    return {
        "p_gain_%": round(p * 100, 1),
        "kelly_plein_%": round(f_etoile * 100, 1),
        "kelly_fractionne_%": round(f_pratique * 100, 2),
        "raison": (f"probabilité de sortie gagnante {p * 100:.0f} %, ratio {b} : "
                   f"Kelly plein {f_etoile * 100:.1f} % du capital, quart de "
                   f"Kelly retenu {f_pratique * 100:.2f} % — plafond, pas objectif."
                   if f_etoile > 0 else
                   "Kelly NÉGATIF : à ces probabilités, la taille "
                   "mathématiquement justifiée est zéro."),
    }


# --- 4. Stop suiveur ---------------------------------------------------------

def stop_suiveur(df: pd.DataFrame, mult: float = 3.0,
                 fenetre: int = 22) -> dict:
    """Chandelier exit : accompagne une position gagnante sans l'étouffer."""
    if "atr14" not in df.columns:
        df = indicators.enrich(df)
    plus_haut = float(df["high"].tail(fenetre).max())
    atr = float(df["atr14"].iloc[-1])
    niveau = plus_haut - mult * atr
    prix = float(df["close"].iloc[-1])
    return {
        "niveau": round(niveau, 4),
        "distance_%": round((niveau / prix - 1) * 100, 2),
        "raison": (f"plus haut {fenetre} séances ({plus_haut:.2f}) − {mult}×ATR : "
                   f"une fois en position, remonter le stop à {niveau:.2f} "
                   "chaque fois que ce niveau progresse — jamais le redescendre."),
    }


# --- Synthèse ----------------------------------------------------------------

def renforts(symbole: str, df: pd.DataFrame, plan: dict | None,
             sens: str = "achat") -> dict:
    """Les contrôles de renfort, avec un décompte des feux verts applicables.

    Le COT (positionnement des spéculateurs, CFTC) s'ajoute quand l'actif est
    couvert : un positionnement extrême dans le sens du trade signale un
    marché encombré.
    """
    resultat = {
        "confluence": confluence(df),
        "force_relative": force_relative(symbole, df),
        "stop_suiveur": stop_suiveur(df),
    }
    if cot.couvre(symbole):
        resultat["cot"] = cot.controle(symbole, sens)
    if plan:
        resultat["kelly"] = kelly_fractionne(plan)
    applicables = [r for r in (resultat["confluence"],
                               resultat["force_relative"],
                               resultat.get("cot", {}))
                   if r.get("favorable") is not None]
    verts = sum(1 for r in applicables if r["favorable"])
    resultat["feux_verts"] = f"{verts}/{len(applicables)}" if applicables else "n/a"
    return resultat
