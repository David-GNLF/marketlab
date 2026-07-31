"""Stratégie de position : QUAND entrer, dans QUEL SENS, pour viser COMBIEN.

Ce module répond aux trois questions concrètes du positionnement, en
assemblant le verdict du moteur de décision, les niveaux techniques et les
simulations :

1. **Quel sens ?** — long si le verdict est Favorable, short s'il est
   Défavorable, à l'écart sinon. Le sens découle du verdict, jamais d'un
   indicateur isolé.
2. **Quand ?** — maintenant, ou attendre un repli/rebond vers une zone
   d'entrée chiffrée. Le critère : où est le prix par rapport à sa moyenne
   courte, aux bandes et au RSI. Acheter une surchauffe, c'est offrir un
   meilleur prix aux autres.
3. **Quelle marge de gain ?** — trois chiffres et non un seul : l'objectif du
   plan (avec sa probabilité d'être TOUCHÉ), le scénario médian simulé, et le
   scénario porteur (quartile haut). Un seul « prix cible » serait un mensonge
   de précision.

Chaque réponse vient avec sa justification, et le stop avec la perte maximale
acceptée. Aide à la décision — jamais un conseil en investissement.
"""

import pandas as pd

from marketlab import (config, decision, edge, forecast, har, indicators,
                       levels)
from marketlab.data import get_ohlcv


def _zone_entree_achat(df: pd.DataFrame, zones: dict) -> tuple[float, float]:
    """Zone de repli raisonnable : entre le support proche et la moyenne courte."""
    prix = float(df["close"].iloc[-1])
    sma20 = float(df["close"].rolling(20).mean().iloc[-1])
    supports = [z["niveau"] for z in zones.get("supports", [])]
    bas = max([s for s in supports if s < prix], default=prix * 0.97)
    haut = min(sma20, prix)
    if haut <= bas:
        haut = prix
    return round(bas, 4), round(haut, 4)


def _quand(df: pd.DataFrame, sens: str, zones: dict) -> dict:
    """Le moment est-il opportun, ou faut-il attendre une meilleure zone ?"""
    prix = float(df["close"].iloc[-1])
    rsi = float(df["rsi14"].iloc[-1]) if pd.notna(df["rsi14"].iloc[-1]) else 50.0
    bb_haut = df["bb_upper"].iloc[-1]
    bb_bas = df["bb_lower"].iloc[-1]
    sma20 = float(df["close"].rolling(20).mean().iloc[-1])

    if sens == "achat":
        surchauffe = rsi >= 70 or (pd.notna(bb_haut) and prix > float(bb_haut))
        etire = prix > sma20 * 1.03
        zone = _zone_entree_achat(df, zones)
        if surchauffe:
            return {"reponse": "attendre",
                    "zone_entree": zone,
                    "raison": f"surchauffe court terme (RSI {rsi:.0f}"
                              + (", au-dessus de la bande haute" if pd.notna(bb_haut)
                                 and prix > float(bb_haut) else "")
                              + f") : viser un repli vers {zone[0]}–{zone[1]}."}
        if etire:
            return {"reponse": "attendre ou fractionner",
                    "zone_entree": zone,
                    "raison": f"prix étiré à {((prix / sma20) - 1) * 100:+.1f} % de "
                              f"sa moyenne 20 séances : entrer en 2 fois, moitié "
                              f"maintenant, moitié vers {zone[0]}–{zone[1]}."}
        return {"reponse": "maintenant",
                "zone_entree": zone,
                "raison": f"prix proche de sa moyenne courte (RSI {rsi:.0f}, "
                          "ni surchauffe ni étirement) : la zone actuelle est "
                          "une zone d'entrée correcte."}

    if sens == "vente":
        survendu = rsi <= 30 or (pd.notna(bb_bas) and prix < float(bb_bas))
        resistances = [z["niveau"] for z in zones.get("resistances", [])]
        rebond_vers = min([r for r in resistances if r > prix],
                          default=round(prix * 1.03, 4))
        if survendu:
            return {"reponse": "attendre",
                    "zone_entree": (round(prix, 4), round(rebond_vers, 4)),
                    "raison": f"déjà survendu (RSI {rsi:.0f}) : vendre ici, c'est "
                              f"vendre au plus bas ; attendre un rebond vers "
                              f"{rebond_vers}."}
        return {"reponse": "maintenant",
                "zone_entree": (round(prix, 4), round(rebond_vers, 4)),
                "raison": f"pas de survente (RSI {rsi:.0f}) : une position "
                          "vendeuse peut s'initier dans la zone actuelle."}

    return {"reponse": "rester à l'écart", "zone_entree": None,
            "raison": "le verdict n'est ni Favorable ni Défavorable : pas de "
                      "position tant que les analyses ne convergent pas."}


def strategie(symbole: str, horizon: int = 20, capital: float = 10_000.0,
              risque_pct: float = 1.0) -> dict:
    """Réponse aux trois questions pour un actif, avec dossiers à l'appui."""
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=1825))
    prix = float(df["close"].iloc[-1])
    zones = levels.zones_proches(df)
    proj = forecast.projeter(df, horizon=horizon,
                             vol_cible=har.vol_cible(symbole))
    verdict = decision.dossier(symbole, horizon=horizon, capital=capital)

    # --- 1. quel sens ? ---
    if verdict["avis"] == "Favorable":
        sens = "achat"
    elif verdict["avis"] == "Défavorable":
        sens = "vente"
    else:
        sens = "aucun"

    # --- 2. quand ? ---
    quand = _quand(df, sens if sens != "aucun" else "", zones)

    # --- 3. quelle marge de gain ? ---
    marge, plan = None, None
    if sens in ("achat", "vente"):
        try:
            plan = levels.plan(symbole, sens=sens, horizon=horizon,
                               capital=capital, risque_pct=risque_pct)
            q = proj["quantiles"]
            if sens == "achat":
                porteur = (q["q75"][-1] / prix - 1) * 100
            else:
                porteur = (prix / q["q25"][-1] - 1) * 100
            marge = {
                "objectif_%": plan["gain_potentiel_%"],
                "proba_toucher_objectif_%": plan["proba_toucher_objectif_%"],
                "scenario_median_%": (proj["rendement_median_%"] if sens == "achat"
                                      else -proj["rendement_median_%"]),
                "scenario_porteur_%": round(float(porteur), 2),
                "risque_stop_%": plan["risque_%"],
                "proba_toucher_stop_%": plan["proba_toucher_stop_%"],
                "lecture": (f"viser {plan['gain_potentiel_%']} % (objectif touché "
                            f"dans {plan['proba_toucher_objectif_%']} % des "
                            f"simulations) ; scénario médian "
                            f"{proj['rendement_median_%']:+.1f} % ; en acceptant "
                            f"de perdre au plus {plan['risque_%']} % au stop."),
            }
        except RuntimeError:
            pass

    # --- renforts : confluence, force relative, COT, Kelly, stop suiveur ---
    controles = edge.renforts(symbole, df,
                              plan if plan else verdict.get("plan"),
                              sens=sens if sens != "aucun" else "")
    if sens in ("achat", "vente") and \
            controles.get("cot", {}).get("favorable") is False:
        verdict["vetos"].append("positionnement COT : "
                                + controles["cot"]["raison"])
        verdict["taille_multiplicateur"] = min(
            verdict["taille_multiplicateur"], 0.5)
    if sens == "achat":
        if controles["confluence"].get("favorable") is False:
            quand = {"reponse": "attendre",
                     "zone_entree": quand.get("zone_entree"),
                     "raison": controles["confluence"]["raison"]}
        if controles["force_relative"].get("favorable") is False:
            verdict["vetos"].append("force relative défavorable : "
                                    + controles["force_relative"]["raison"])
            verdict["taille_multiplicateur"] = min(
                verdict["taille_multiplicateur"], 0.5)

    taille = None
    if plan and verdict["taille_multiplicateur"] > 0:
        quantite = plan["taille"]["quantite"] * verdict["taille_multiplicateur"]
        montant = quantite * prix
        # le quart de Kelly sert de PLAFOND au montant engagé
        kelly = controles.get("kelly", {})
        plafond_kelly = None
        if kelly.get("kelly_fractionne_%") is not None:
            plafond_kelly = capital * kelly["kelly_fractionne_%"] / 100
            if plafond_kelly < montant:
                quantite = plafond_kelly / prix
                montant = plafond_kelly
        taille = {
            "multiplicateur": verdict["taille_multiplicateur"],
            "quantite": round(quantite, 6),
            "montant": round(montant, 2),
            "perte_max": round(plan["taille"]["perte_si_stop"]
                               * verdict["taille_multiplicateur"], 2),
            "plafond_kelly": (round(plafond_kelly, 2)
                              if plafond_kelly is not None else None),
        }

    return {
        "symbole": symbole,
        "nom": config.NOMS_ACTIFS.get(symbole, symbole),
        "date": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M"),
        "prix": round(prix, 4),
        "sens": {"reponse": {"achat": "POSITION LONGUE (achat)",
                             "vente": "POSITION COURTE (vente)",
                             "aucun": "PAS DE POSITION"}[sens],
                 "raison": f"verdict {verdict['avis']} "
                           f"(note {verdict['note_globale']:+.1f}, concordance "
                           f"{verdict['concordance_%']:.0f} %)"},
        "quand": quand,
        "marge": marge,
        "taille": taille,
        "plan": ({"entree": plan["entree"], "stop": plan["stop"],
                  "objectif": plan["objectif"],
                  "ratio_gain_risque": plan["ratio_gain_risque"],
                  "esperance_%": plan["esperance_%"],
                  "stop_suiveur": controles["stop_suiveur"]} if plan else None),
        "renforts": controles,
        "verdict": verdict,
        "avertissement": "Aide à la décision, pas un conseil en investissement.",
    }
