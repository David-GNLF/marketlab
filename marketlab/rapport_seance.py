"""Rapport de séance : confronter ce qui était prévu à ce qui est advenu.

Un P&L dit qu'on a perdu. Il ne dit pas POURQUOI, ni quoi corriger. Ce module
rejoue chaque trade clos sur les cours réels et en tire les deux mesures que
les praticiens utilisent pour régler stops et objectifs :

- **MAE** (Maximum Adverse Excursion) : jusqu'où le trade est allé CONTRE
  nous avant de se terminer. La MAE des trades GAGNANTS dit où un stop peut
  se placer sans tuer les bons trades : si les gagnants ne descendent jamais
  au-delà de −2 %, un stop à −5 % ne protège de rien et un stop à −1,5 %
  les tue tous.
- **MFE** (Maximum Favorable Excursion) : jusqu'où il est allé EN NOTRE
  FAVEUR. La MFE des trades perdants dit ce qu'on a laissé filer, et celle
  de tous les trades dit si l'objectif était atteignable ou fantaisiste.

S'y ajoute le diagnostic le plus actionnable : un stop touché a-t-il été
touché par du BRUIT ? Si le cours repasse au-dessus du prix d'entrée dans
l'horizon qui restait, le stop a coupé une position qui aurait fini gagnante.
Un taux élevé désigne des stops trop serrés — c'est une correction chiffrée,
pas une impression.

Tout est calculé, rien n'est raconté. Le récit peut venir ensuite ; les
chiffres d'abord.
"""

import numpy as np
import pandas as pd

from marketlab.data import get_ohlcv

SEUIL_ECHANTILLON = 8       # en deçà, on ne conclut rien
HORIZON_SUIVI_J = 30        # jours civils suivis après la sortie


def _bornes(trade: dict) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    try:
        ouvert = pd.Timestamp(trade["ouvert_le"])
        ferme = pd.Timestamp(trade.get("ferme_le") or trade["ouvert_le"])
    except Exception:
        return None
    return (ouvert, ferme) if ferme >= ouvert else None


def apprecier_trade(trade: dict) -> dict | None:
    """Rejoue un trade clos sur les cours réels.

    Renvoie les excursions extrêmes, et — pour un stop touché — si le cours
    est revenu au-dessus de l'entrée dans les semaines qui ont suivi.
    """
    bornes = _bornes(trade)
    if not bornes:
        return None
    ouvert, ferme = bornes
    symbole = trade.get("symbole")
    entree = float(trade.get("entree") or 0)
    if not symbole or entree <= 0:
        return None
    try:
        df = get_ohlcv(symbole, lookback_days=400)
    except Exception:
        return None
    if df.empty:
        return None

    sens = 1 if trade.get("sens", "long") == "long" else -1
    pendant = df[(df.index >= ouvert.normalize()) & (df.index <= ferme)]
    if pendant.empty:
        pendant = df.tail(1)

    if sens == 1:
        mae = (float(pendant["low"].min()) / entree - 1) * 100
        mfe = (float(pendant["high"].max()) / entree - 1) * 100
    else:
        mae = (1 - float(pendant["high"].max()) / entree) * 100
        mfe = (1 - float(pendant["low"].min()) / entree) * 100

    resultat = {
        "symbole": symbole,
        "motif": trade.get("motif"),
        "pnl": round(float(trade.get("pnl", 0)), 2),
        "gagnant": float(trade.get("pnl", 0)) > 0,
        "mae_%": round(mae, 2),      # négatif : le pire creux traversé
        "mfe_%": round(mfe, 2),      # positif : le meilleur point atteint
        "duree_seances": int(len(pendant)),
    }

    # Le stop a-t-il coupé du bruit ? On regarde APRÈS la sortie.
    if trade.get("motif") == "stop touché":
        apres = df[(df.index > ferme)
                   & (df.index <= ferme + pd.Timedelta(days=HORIZON_SUIVI_J))]
        if not apres.empty:
            revenu = (float(apres["high"].max()) >= entree if sens == 1
                      else float(apres["low"].min()) <= entree)
            resultat["stop_etait_du_bruit"] = bool(revenu)
            resultat["rebond_apres_stop_%"] = round(
                ((float(apres["high"].max()) / entree - 1) * 100) * sens, 2)

    # L'objectif était-il seulement approché ?
    objectif = trade.get("objectif")
    if objectif:
        distance = abs(float(objectif) / entree - 1) * 100
        if distance > 0:
            resultat["objectif_atteint_a_%"] = round(
                max(0.0, mfe) / distance * 100, 0)
    return resultat


def rapport(compte: dict) -> dict:
    """Appréciation d'ensemble d'un compte : ce qui va, et ce qui coûte."""
    hist = compte.get("historique", [])
    detail = [a for a in (apprecier_trade(t) for t in hist) if a]
    if not detail:
        return {"nom": compte.get("nom"), "n": 0,
                "message": "aucun trade clos rejouable pour l'instant"}

    gagnants = [d for d in detail if d["gagnant"]]
    perdants = [d for d in detail if not d["gagnant"]]
    stops = [d for d in detail if d["motif"] == "stop touché"]
    bruit = [d for d in stops if d.get("stop_etait_du_bruit")]

    constats = []
    if len(detail) < SEUIL_ECHANTILLON:
        constats.append(
            f"{len(detail)} trade(s) seulement : trop peu pour conclure quoi "
            f"que ce soit. Ce rapport devient exploitable vers "
            f"{SEUIL_ECHANTILLON} trades.")
    else:
        if gagnants:
            pire_creux = float(np.median([d["mae_%"] for d in gagnants]))
            constats.append(
                f"les trades gagnants ont traversé un creux médian de "
                f"{pire_creux:.2f} % : un stop plus serré que cela les aurait "
                f"tués avant qu'ils ne rapportent.")
        if stops:
            taux = len(bruit) / len(stops) * 100
            constats.append(
                f"{len(bruit)} stop(s) sur {len(stops)} ont été suivis d'un "
                f"retour au-dessus du prix d'entrée ({taux:.0f} %) : "
                + ("les stops sont trop serrés, ils coupent du bruit."
                   if taux >= 50 else
                   "les stops coupent des mouvements réels, pas du bruit."))
        approches = [d["objectif_atteint_a_%"] for d in detail
                     if d.get("objectif_atteint_a_%") is not None]
        if approches:
            med = float(np.median(approches))
            constats.append(
                f"le cours a parcouru en médiane {med:.0f} % du chemin vers "
                f"l'objectif" + (" : les objectifs sont hors de portée de "
                                 "l'horizon." if med < 40 else "."))
        if perdants:
            laisse = float(np.median([d["mfe_%"] for d in perdants]))
            if laisse > 1:
                constats.append(
                    f"les trades perdants avaient atteint {laisse:.2f} % de "
                    f"gain latent médian avant de se retourner : une prise de "
                    f"bénéfice partielle mérite d'être étudiée.")

    return {
        "nom": compte.get("nom"), "n": len(detail),
        "gagnants": len(gagnants), "perdants": len(perdants),
        "mae_mediane_%": round(float(np.median([d["mae_%"] for d in detail])), 2),
        "mfe_mediane_%": round(float(np.median([d["mfe_%"] for d in detail])), 2),
        "stops_touches": len(stops),
        "stops_qui_etaient_du_bruit": len(bruit),
        "echantillon_suffisant": len(detail) >= SEUIL_ECHANTILLON,
        "constats": constats,
        "trades": detail,
        "methode": ("MAE/MFE rejouées sur les cours réels entre l'ouverture et "
                    "la clôture ; pour un stop touché, on regarde si le cours "
                    "est revenu au-dessus de l'entrée dans les "
                    f"{HORIZON_SUIVI_J} jours suivants."),
    }


def rapport_global(comptes: list[dict]) -> dict:
    """Le rapport de séance publié : un bloc par robot."""
    return {
        "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "robots": [rapport(c) for c in comptes],
        "lecture": ("Ce rapport ne raconte rien : il rejoue les trades sur les "
                    "cours réels. La MAE des gagnants dit où placer un stop "
                    "sans tuer les bons trades ; le taux de stops suivis d'un "
                    "rebond dit s'ils sont trop serrés ; la part du chemin "
                    "parcourue vers l'objectif dit s'il était atteignable. "
                    "Ce sont ces chiffres, et non une impression, qui doivent "
                    "commander une modification des règles."),
    }
