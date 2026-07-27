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

from marketlab import (config, drivers, events, forecast, fundamentals,
                       indicators, levels, news, score_history, seasonality,
                       signals)
from marketlab.data import get_ohlcv

JOURNAL = config.DATA_DIR / "journal_decisions.csv"
POIDS_APPRIS = config.DATA_DIR / "poids_appris.json"

# Pondérations de BASE des composantes (renormalisées si une composante est
# absente — les fondamentaux n'existent pas pour une crypto ou une devise).
# Une fois le journal assez fourni, `calibrer()` les ajuste d'après le bilan
# réel et le résultat prime (voir poids_effectifs()).
POIDS = {
    "technique": 0.25,
    "prevision": 0.20,
    "analogues": 0.15,
    "fondamentaux": 0.15,   # actions uniquement
    "moteurs": 0.15,        # forex (carry), métaux (taux réels), matières (structure)
    "saisonnalite": 0.05,
    "sentiment": 0.05,
}


def poids_effectifs() -> tuple[dict, dict]:
    """Pondérations réellement utilisées : apprises si disponibles, base sinon.

    Renvoie (poids, meta) — meta documente la provenance pour l'affichage.
    """
    if POIDS_APPRIS.exists():
        try:
            import json
            appris = json.loads(POIDS_APPRIS.read_text(encoding="utf-8"))
            poids = appris.get("poids", {})
            if set(poids) == set(POIDS) and abs(sum(poids.values()) - 1) < 0.01:
                return poids, {"source": "apprise",
                               "n_evalues": appris.get("n_evalues"),
                               "lambda": appris.get("lambda"),
                               "date": appris.get("date")}
        except Exception:
            pass
    return dict(POIDS), {"source": "base",
                         "detail": "pas encore assez de verdicts évalués"}

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


def _composante_moteurs(symbole: str) -> dict | None:
    """Moteurs fondamentaux de la classe d'actif, convertis en note.

    Barèmes explicites (clip ±100 au total) :
    - forex : +20 par point de carry, +40 par point d'élargissement sur 6 mois
      — la devise mieux rémunérée attire les capitaux ;
    - métaux précieux : −150 par point de HAUSSE des taux réels sur 3 mois,
      −10 par % de hausse du dollar — le coût d'opportunité de l'or ;
    - matières : −3 par % de base annualisée — une backwardation (base
      négative) est haussière, un contango marqué pèse sur le portage.
    """
    ms = drivers.moteurs(symbole)
    ms = [m for m in ms if "differentiel_pts" in m
          or m.get("outil") in ("taux réels + dollar", "structure à terme")]
    if not ms:
        return None  # action, indice, crypto : pas de moteur dédié

    note, raisons = 0.0, []
    for m in ms:
        if "differentiel_pts" in m:
            note += _clip(m["differentiel_pts"] * 20, 60)
            note += _clip((m.get("variation_6m_pts") or 0) * 40, 40)
        elif m["outil"] == "taux réels + dollar":
            note += _clip(-m["variation_3m_pts"] * 150, 70)
            note += _clip(-m["dollar_variation_3m_%"] * 10, 30)
        elif m["outil"] == "structure à terme":
            note += _clip(-m["base_annualisee_%"] * 3, 60)
        raisons.append(m["lecture"])
    return {"note": _clip(note), "raisons": raisons}


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
    try:
        moteurs = _composante_moteurs(symbole)
        if moteurs:
            composantes["moteurs"] = moteurs
    except Exception:
        pass  # moteur indisponible : la composante s'absente, pas d'échec
    composantes["saisonnalite"] = _composante_saisonnalite(symbole)
    composantes["sentiment"] = _composante_sentiment(symbole)

    poids, poids_meta = poids_effectifs()
    poids_total = sum(poids[c] for c in composantes)
    note_globale = sum(composantes[c]["note"] * poids[c]
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

    # --- conclusion synthétique : LA réponse hausse/baisse sur la période ---
    # Probabilité simulée légèrement inclinée par le verdict multi-analyses
    # (±8 pts au maximum) : les simulations restent la colonne vertébrale, le
    # verdict apporte ce que les prix seuls ne voient pas.
    p_simulee = proj["proba_hausse_%"]
    p_combinee = float(np.clip(p_simulee + note_globale * 0.08, 5, 95))
    if p_combinee >= 60:
        tendance_attendue = "HAUSSE plus probable que baisse"
    elif p_combinee <= 40:
        tendance_attendue = "BAISSE plus probable que hausse"
    else:
        tendance_attendue = "aucune direction nettement favorisée"
    conclusion = {
        "periode_seances": horizon,
        "proba_hausse_simulee_%": p_simulee,
        "proba_hausse_combinee_%": round(p_combinee, 1),
        "tendance_attendue": tendance_attendue,
        "amplitude_mediane_%": proj["rendement_median_%"],
        "intervalle_80": proj["intervalle_80"],
        "scenario_porteur_%": round(
            (proj["quantiles"]["q75"][-1] / prix - 1) * 100, 2),
        "scenario_adverse_%": round(
            (proj["quantiles"]["q25"][-1] / prix - 1) * 100, 2),
        "var_95_%": proj["var_95_%"],
        "texte": (
            f"Sur {horizon} séances : {tendance_attendue} "
            f"(probabilité de hausse {p_combinee:.0f} % en combinant "
            f"{p_simulee} % simulés et le verdict multi-analyses "
            f"{note_globale:+.0f}). Amplitude médiane attendue "
            f"{proj['rendement_median_%']:+.1f} %, scénario porteur "
            f"{(proj['quantiles']['q75'][-1] / prix - 1) * 100:+.1f} %, "
            f"adverse {(proj['quantiles']['q25'][-1] / prix - 1) * 100:+.1f} % ; "
            f"dans 80 % des simulations le prix finit entre "
            f"{proj['intervalle_80'][0]:,.4g} et {proj['intervalle_80'][1]:,.4g}. "
            f"Perte extrême (VaR 95 %) : {proj['var_95_%']} %."),
    }

    return {
        "symbole": symbole,
        "date": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
        "prix": round(prix, 4),
        "horizon": horizon,
        "note_globale": round(float(note_globale), 1),
        "avis": avis,
        "conclusion": conclusion,
        "concordance_%": round(concordance, 0),
        "taille_multiplicateur": taille,
        "composantes": [
            {"nom": nom, "poids": round(poids[nom], 3),
             "note": round(c["note"], 1), "raisons": c["raisons"]}
            for nom, c in composantes.items()],
        "ponderation": poids_meta,
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
    """Consigne les verdicts du jour (un par titre et par date, idempotent).

    Les notes de CHAQUE composante sont consignées (colonnes c_*) : c'est la
    matière première de l'apprentissage des pondérations — sans elles, on ne
    pourrait pas savoir quelle composante avait raison.
    """
    lignes = []
    for d in dossiers:
        if "erreur" in d:
            continue
        ligne = {"date": d["date"], "symbole": d["symbole"], "avis": d["avis"],
                 "note": d["note_globale"], "prix": d["prix"],
                 "horizon": d["horizon"]}
        for c in d.get("composantes", []):
            ligne[f"c_{c['nom']}"] = c["note"]
        lignes.append(ligne)
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
    df = _evaluer_journal()
    if df.empty:
        return {"verdicts_evalues": 0,
                "message": "Aucun verdict n'a encore atteint son horizon — "
                           "le bilan se remplira avec le temps."}
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


def _evaluer_journal() -> pd.DataFrame:
    """Verdicts arrivés à leur horizon, avec le rendement réellement advenu.

    Conserve toutes les colonnes du journal — dont les notes c_* par
    composante, matière première de l'apprentissage des pondérations.
    """
    if not JOURNAL.exists():
        return pd.DataFrame()
    journal = pd.read_csv(JOURNAL)
    journal["date"] = pd.to_datetime(journal["date"])

    evalues = []
    for symbole, groupe in journal.groupby("symbole"):
        try:
            # 1200 jours : il faut pouvoir évaluer des verdicts rétro-journalisés
            # jusqu'à 2 ans en arrière, plus leur horizon
            cours = get_ohlcv(symbole, lookback_days=1200)["close"]
        except Exception:
            continue
        for _, ligne in groupe.iterrows():
            futurs = cours[cours.index > ligne["date"]]
            if len(futurs) < ligne["horizon"]:
                continue  # horizon pas encore écoulé
            realise = float(futurs.iloc[int(ligne["horizon"]) - 1]
                            / ligne["prix"] - 1) * 100
            evalues.append({**ligne.to_dict(), "rendement_reel_%": realise})
    return pd.DataFrame(evalues)


# --- Apprentissage des pondérations ------------------------------------------

def _calculer_poids(df_evalues: pd.DataFrame, poids_base: dict | None = None,
                    lam_max: float = 0.5, min_par_composante: int = 30,
                    min_total: int = 60) -> dict:
    """Le calcul pur : du journal évalué aux pondérations ajustées.

    Méthode, volontairement conservatrice :
    - chaque composante est jugée par l'IC de Spearman entre ses notes au
      moment du verdict et les rendements réellement advenus ;
    - seule la part POSITIVE de l'IC compte (une composante anti-corrélée ne
      reçoit pas un poids négatif : elle tombe vers le plancher) ;
    - le mélange est progressif : poids = (1−λ)·base + λ·performance, avec
      λ = min(lam_max, n/400). À 60 verdicts, λ≈0,15 ; il faut 200 verdicts
      pour atteindre la demi-influence. L'outil ne retourne pas sa veste sur
      un petit échantillon ;
    - plancher de 2 % par composante : aucune n'est jamais réduite au silence,
      pour qu'elle puisse se racheter dans le bilan futur.
    """
    poids_base = poids_base or POIDS
    n = len(df_evalues)
    rapport = {"n_evalues": int(n), "ic_par_composante": {}, "poids": None}
    if n < min_total:
        rapport["statut"] = (f"échantillon insuffisant ({n} verdicts évalués, "
                             f"minimum {min_total}) : pondérations de base "
                             "conservées")
        return rapport

    rendement = df_evalues["rendement_reel_%"]
    ics = {}
    for nom in poids_base:
        colonne = f"c_{nom}"
        if colonne not in df_evalues.columns:
            continue
        paires = df_evalues[[colonne]].join(rendement).dropna()
        if len(paires) < min_par_composante:
            rapport["ic_par_composante"][nom] = {
                "ic": None, "n": len(paires),
                "note": "trop peu de données, poids de base conservé"}
            continue
        ic = float(paires[colonne].rank().corr(
            paires["rendement_reel_%"].rank()))
        ics[nom] = ic
        rapport["ic_par_composante"][nom] = {"ic": round(ic, 3),
                                             "n": int(len(paires))}

    if len(ics) < 3:
        rapport["statut"] = ("moins de 3 composantes mesurables : "
                             "pondérations de base conservées")
        return rapport

    # performance : part positive de l'IC, plancher epsilon
    perf = {nom: max(ics.get(nom, 0.0), 0.0) + 0.01 for nom in poids_base}
    total_perf = sum(perf.values())
    perf = {nom: v / total_perf for nom, v in perf.items()}

    lam = min(lam_max, n / 400)
    melange = {nom: (1 - lam) * poids_base[nom] + lam * perf[nom]
               for nom in poids_base}
    # plancher 2 % puis renormalisation
    melange = {nom: max(v, 0.02) for nom, v in melange.items()}
    total = sum(melange.values())
    rapport["poids"] = {nom: round(v / total, 4) for nom, v in melange.items()}
    rapport["lambda"] = round(lam, 3)
    rapport["statut"] = (f"pondérations ajustées sur {n} verdicts évalués "
                         f"(influence de l'apprentissage : {lam * 100:.0f} %)")
    return rapport


def calibrer() -> dict:
    """Ré-étalonne les pondérations d'après le bilan réel et les persiste.

    À exécuter avant la génération des verdicts du jour : les nouveaux
    dossiers utilisent aussitôt les poids appris (via poids_effectifs()).
    """
    import json
    df = _evaluer_journal()
    rapport = _calculer_poids(df)
    rapport["date"] = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M")
    rapport["poids_base"] = POIDS
    rapport["methode"] = (
        "IC de Spearman entre la note de chaque composante au moment du "
        "verdict et le rendement réellement advenu à l'horizon ; mélange "
        "progressif (1−λ)·base + λ·performance, λ = min(0,5, n/400) ; "
        "plancher 2 % par composante.")
    if rapport["poids"]:
        POIDS_APPRIS.parent.mkdir(parents=True, exist_ok=True)
        POIDS_APPRIS.write_text(
            json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
    return rapport
