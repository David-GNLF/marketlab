"""Moteur de décision : fusion de toutes les analyses en un verdict motivé.

Chaque module de MarketLab éclaire une facette ; celui-ci les assemble en un
**dossier de décision** par titre : une note globale pondérée, un avis, la
liste explicite des raisons pour et contre, les vetos éventuels, et une
taille de position suggérée. Aucune boîte noire : chaque composante affiche
sa note, son poids et ses raisons.

Deux principes non négociables :

1. **Les vetos priment sur la note.** Une espérance négative du plan de
   trade, ou une publication de résultats dans l'horizon, dégradent ou
   bloquent le verdict quelle que soit l'allure du graphique.
2. **Le journal est tenu et le bilan publié.** Chaque verdict est consigné
   (date, avis, prix) ; une fois l'horizon écoulé, le résultat réel est
   mesuré et le taux de réussite par catégorie d'avis est affiché sur le
   site. L'outil rend des comptes.
"""

import numpy as np
import pandas as pd

from marketlab import (config, events, forecast, fundamentals, indicators,
                       levels, news, score_history, seasonality, signals)
from marketlab.data import get_ohlcv

JOURNAL = config.DATA_DIR / "journal_decisions.csv"

# Pondérations des composantes (renormalisées si une composante est absente —
# les fondamentaux n'existent pas pour une crypto ou une paire de devises).
POIDS = {
    "technique": 0.30,
    "prevision": 0.25,
    "analogues": 0.15,
    "fondamentaux": 0.20,
    "saisonnalite": 0.05,
    "sentiment": 0.05,
}

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


def _clip(x: float, borne: float = 100.0) -> float:
    return float(max(-borne, min(borne, x)))


# --- Composantes (chacune : note -100..100 + raisons) ------------------------

def _composante_technique(df: pd.DataFrame) -> dict:
    sig = signals.compute_signals(df)
    note = float(sig["score"])
    return {"note": note, "raisons": [f"score technique {note:+.0f} "
                                      f"({signals.label(note)})"]}


def _composante_prevision(proj: dict) -> dict:
    p = proj["proba_hausse_%"]
    note = _clip((p - 50) * 4)
    raisons = [f"P(hausse) simulée à {p} % sur l'horizon"]
    if proj["var_95_%"] < -12:
        note -= 15
        raisons.append(f"queue de risque lourde (VaR 95 % : {proj['var_95_%']} %)")
    return {"note": _clip(note), "raisons": raisons}


def _composante_analogues(ana: dict) -> dict:
    note = _clip((ana["proba_hausse_%"] - 50) * 2
                 + _clip(ana["rendement_median_%"] * 8, 40))
    return {"note": note, "raisons": [
        f"{ana['k']} configurations passées similaires : hausse dans "
        f"{ana['proba_hausse_%']} % des cas, médiane "
        f"{ana['rendement_median_%']:+.2f} %"]}


def _composante_fondamentaux(symbole: str) -> dict | None:
    if not fundamentals.est_action(symbole):
        return None
    try:
        n = fundamentals.noter(symbole)
    except RuntimeError:
        return None
    if n["score_global"] is None:
        return None
    note = _clip((n["score_global"] - 50) * 2)
    return {"note": note, "raisons": [
        f"fondamentaux {n['score_global']}/100 ({n['appreciation']})"]}


def _composante_saisonnalite(symbole: str) -> dict:
    try:
        table = seasonality.par_mois(symbole)
    except RuntimeError:
        return {"note": 0.0, "raisons": ["saisonnalité non mesurable"]}
    mois_courant = MOIS_FR[pd.Timestamp.now().month - 1]
    ligne = table[(table["mois"] == mois_courant) & table["retenu"]]
    if ligne.empty:
        return {"note": 0.0,
                "raisons": [f"aucun effet saisonnier retenu pour {mois_courant}"]}
    r = float(ligne.iloc[0]["rendement_moyen_%"])
    return {"note": _clip(r * 10, 30), "raisons": [
        f"effet {mois_courant} retenu ({r:+.2f} % en moyenne, "
        "significatif et stable)"]}


def _composante_sentiment(symbole: str) -> dict:
    try:
        s = news.sentiment(symbole)
    except Exception:
        return {"note": 0.0, "raisons": ["actualités indisponibles"]}
    if not s.get("n_titres"):
        return {"note": 0.0, "raisons": ["aucune actualité récente"]}
    note = _clip((s["score_moyen"] or 0) * 30, 30)
    return {"note": note,
            "raisons": [f"sentiment presse {s['lecture']} "
                        f"({s['positifs']}➕/{s['negatifs']}➖, indicatif)"]}


# --- Verdict -----------------------------------------------------------------

def dossier(symbole: str, horizon: int = 20, capital: float = 10_000.0,
            lookback_days: int = 1825) -> dict:
    """Dossier de décision complet pour un titre."""
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=lookback_days))
    prix = float(df["close"].iloc[-1])
    regime = forecast.regime(df)
    proj = forecast.projeter(df, horizon=horizon)

    composantes = {"technique": _composante_technique(df),
                   "prevision": _composante_prevision(proj)}
    try:
        composantes["analogues"] = _composante_analogues(
            forecast.analogues(df, horizon=horizon))
    except RuntimeError:
        pass
    fonda = _composante_fondamentaux(symbole)
    if fonda:
        composantes["fondamentaux"] = fonda
    composantes["saisonnalite"] = _composante_saisonnalite(symbole)
    composantes["sentiment"] = _composante_sentiment(symbole)

    poids_total = sum(POIDS[c] for c in composantes)
    note_globale = sum(composantes[c]["note"] * POIDS[c]
                       for c in composantes) / poids_total

    # --- vetos et modulateurs (ils priment sur la note) ---
    vetos, taille = [], 1.0
    plan = None
    if note_globale >= 15:  # un plan d'achat n'a de sens que si l'idée est haussière
        try:
            plan = levels.plan(symbole, sens="achat", horizon=horizon,
                               capital=capital, lookback_days=lookback_days)
            if plan["esperance_par_unite"] <= 0:
                vetos.append("VETO : le plan de trade a une espérance NÉGATIVE "
                             f"({plan['esperance_%']:+.2f} %) — les simulations "
                             "touchent le stop plus souvent que l'objectif.")
                taille = 0.0
            elif plan["ratio_gain_risque"] < 1.2:
                vetos.append(f"ratio gain/risque faible ({plan['ratio_gain_risque']}) : "
                             "demi-taille conseillée.")
                taille = min(taille, 0.5)
        except RuntimeError:
            plan = None

    if events.a_des_resultats(symbole):
        try:
            risque_evt = events.risque_evenement(symbole, horizon=horizon)
            if risque_evt.get("dans_horizon"):
                vetos.append("publication de résultats dans l'horizon "
                             f"({risque_evt['prochaine']['date']}) : demi-taille, "
                             "ou attendre la publication.")
                taille = min(taille, 0.5)
        except Exception:
            pass

    if regime["tendance"] == "sans direction" and regime["volatilite"] == "élevée":
        vetos.append("régime « agitation sans direction » : le pire contexte, "
                     "taille réduite.")
        taille = min(taille, 0.5)

    # --- avis final ---
    if taille == 0.0:
        avis = "S'abstenir"
    elif note_globale >= 30:
        avis = "Favorable"
    elif note_globale <= -30:
        avis = "Défavorable"
    else:
        avis = "Neutre"

    signes = [np.sign(c["note"]) for c in composantes.values()
              if abs(c["note"]) >= 5]
    concordance = (abs(sum(signes)) / len(signes) * 100) if signes else 0.0

    return {
        "symbole": symbole,
        "date": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
        "prix": round(prix, 4),
        "horizon": horizon,
        "note_globale": round(float(note_globale), 1),
        "avis": avis,
        "concordance_%": round(concordance, 0),
        "taille_multiplicateur": taille,
        "composantes": [
            {"nom": nom, "poids": POIDS[nom],
             "note": round(c["note"], 1), "raisons": c["raisons"]}
            for nom, c in composantes.items()],
        "vetos": vetos,
        "regime": regime,
        "plan": ({k: plan[k] for k in ("entree", "stop", "objectif",
                                       "ratio_gain_risque",
                                       "proba_toucher_stop_%",
                                       "proba_toucher_objectif_%",
                                       "esperance_%", "taille")}
                 if plan else None),
        "avertissement": "Aide à la décision, pas un conseil en investissement.",
    }


def verdicts(symboles: list[str] | None = None, horizon: int = 20) -> list[dict]:
    """Dossiers pour un panier de titres (erreurs isolées par titre)."""
    symboles = symboles or (config.ACTIONS_US[:8] + config.ACTIONS_EU[:4]
                            + config.CRYPTO[:3])
    dossiers = []
    for s in symboles:
        try:
            dossiers.append(dossier(s, horizon=horizon))
        except Exception as exc:
            dossiers.append({"symbole": s, "erreur": str(exc)[:120]})
    return dossiers


# --- Journal et bilan --------------------------------------------------------

def journaliser(dossiers: list[dict]) -> int:
    """Consigne les verdicts du jour (un par titre et par date, idempotent)."""
    lignes = [{"date": d["date"], "symbole": d["symbole"], "avis": d["avis"],
               "note": d["note_globale"], "prix": d["prix"],
               "horizon": d["horizon"]}
              for d in dossiers if "erreur" not in d]
    if not lignes:
        return 0
    nouveau = pd.DataFrame(lignes)
    if JOURNAL.exists():
        journal = pd.concat([pd.read_csv(JOURNAL), nouveau], ignore_index=True)
        journal = journal.drop_duplicates(subset=["date", "symbole"], keep="last")
    else:
        journal = nouveau
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    journal.to_csv(JOURNAL, index=False)
    return len(nouveau)


def bilan() -> dict:
    """Le tribunal de l'outil : que valaient les verdicts passés ?

    Pour chaque verdict dont l'horizon est écoulé, mesure le rendement réel
    et agrège par catégorie d'avis. C'est CE tableau qui dit si l'outil
    mérite d'être écouté — pas ses raisonnements.
    """
    if not JOURNAL.exists():
        return {"verdicts_evalues": 0,
                "message": "Aucun verdict journalisé pour l'instant."}
    journal = pd.read_csv(JOURNAL)
    journal["date"] = pd.to_datetime(journal["date"])

    evalues = []
    for symbole, groupe in journal.groupby("symbole"):
        try:
            cours = get_ohlcv(symbole)["close"]
        except Exception:
            continue
        for _, ligne in groupe.iterrows():
            futurs = cours[cours.index > ligne["date"]]
            if len(futurs) < ligne["horizon"]:
                continue  # horizon pas encore écoulé
            realise = float(futurs.iloc[int(ligne["horizon"]) - 1]
                            / ligne["prix"] - 1) * 100
            evalues.append({**ligne.to_dict(), "rendement_reel_%": realise})

    if not evalues:
        return {"verdicts_evalues": 0,
                "message": "Aucun verdict n'a encore atteint son horizon — "
                           "le bilan se remplira avec le temps."}

    df = pd.DataFrame(evalues)
    par_avis = []
    for avis, groupe in df.groupby("avis"):
        r = groupe["rendement_reel_%"]
        attendu_hausse = avis == "Favorable"
        reussite = (r > 0).mean() if attendu_hausse else \
            (r < 0).mean() if avis == "Défavorable" else np.nan
        par_avis.append({
            "avis": avis, "n": len(groupe),
            "rendement_moyen_%": round(float(r.mean()), 2),
            "rendement_median_%": round(float(r.median()), 2),
            "taux_reussite_%": (round(float(reussite) * 100, 1)
                                if reussite == reussite else None),
        })
    return {
        "verdicts_evalues": len(df),
        "premiere_date": df["date"].min().strftime("%Y-%m-%d"),
        "par_avis": par_avis,
        "lecture": ("Un outil honnête montre son bilan, bon ou mauvais. "
                    "Tant que « Favorable » ne bat pas « Défavorable » sur la "
                    "durée, traiter les verdicts avec prudence."),
    }
