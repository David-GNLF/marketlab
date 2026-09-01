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

import math

import numpy as np
import pandas as pd

from marketlab import (broker_tools, calibration, config, contexte, drivers,
                       har,
                       events, forecast, fundamentals, indicators, levels,
                       news, score_history, seasonality, signals, surprise)
from marketlab.data import get_ohlcv

JOURNAL = config.DATA_DIR / "journal_decisions.csv"
POIDS_APPRIS = config.DATA_DIR / "poids_appris.json"

# Horizons auxquels chaque verdict est mesuré EN PARALLÈLE de son horizon
# officiel. Raccourcir l'horizon multiplie les épisodes de marché
# indépendants pour une même durée d'observation : c'est le seul levier
# rapide pour savoir si l'outil vaut quelque chose.
HORIZONS_MESURE = (3, 5, 10, 20)

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
            poids = appris.get("poids") or {}
            # « poids: null » = la calibration a conclu que rien n'est démontré.
            # Il faut alors revenir aux pondérations de BASE : sans ce chemin,
            # un ancien fichier de poids appris survivrait à la conclusion qui
            # l'invalide et continuerait de piloter le verdict.
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

def _regle_ecole(df: pd.DataFrame, symbole: str) -> float | None:
    """La « règle d'école » du swing trading — CANDIDATE, pas vérité.

    Demandée par l'utilisateur (2026-08-27) à partir des manuels : tendance
    EMA50 > EMA200, confirmation EMA20 > EMA50, déclencheur « le RSI se
    redresse en sortant de la zone basse (< 40) et le cours repasse sa
    MM50 » ; côté négatif symétrique (surachat qui retombe, perte de la
    MM50). Filtre qualité fondamental : une note ACHETEUSE est divisée par
    deux si la croissance est faible ou l'endettement lourd — « le
    fondamental filtre quoi, la technique dit quand ».

    POURQUOI candidate et pas composante : chaque brique prédictive de ce
    projet a dû gagner sa place, et la composante technique existante a un
    IC mesuré NÉGATIF (retour à la moyenne). Cette règle est donc journalisée
    (colonne c_regle_ecole), son IC se mesurera comme celui des autres, et
    l'apprentissage des pondérations la promouvra si — et seulement si —
    elle classe mieux que le bruit. None si l'historique ne couvre pas
    l'EMA200 : pas de note inventée.
    """
    if len(df) < 210:
        return None
    c, r = df["close"], df["rsi14"]
    e20, e50, e200 = df["ema20"], df["ema50"], df["ema200"]
    if pd.isna(e200.iloc[-1]) or pd.isna(r.iloc[-1]):
        return None
    note = 0.0
    # tendance de fond, puis confirmation courte
    note += 20 if e50.iloc[-1] > e200.iloc[-1] else -20
    note += 10 if e20.iloc[-1] > e50.iloc[-1] else -10
    # croisement RÉCENT de la MM50 (3 dernières séances) — l'événement, pas l'état
    au_dessus = (c > e50).iloc[-4:]
    if bool(au_dessus.iloc[-1]) and not bool(au_dessus.iloc[:-1].all()):
        note += 25
    elif not bool(au_dessus.iloc[-1]) and bool(au_dessus.iloc[:-1].any()):
        note -= 25
    # redressement du momentum : le RSI SORT de sa zone (fenêtre 5 séances)
    if r.iloc[-1] >= 40 and bool((r.iloc[-6:-1] < 40).any()):
        note += 35
    elif r.iloc[-1] <= 70 and bool((r.iloc[-6:-1] > 70).any()):
        note -= 35
    # le filtre qualité ne s'applique qu'aux notes acheteuses : on n'achète
    # pas une entreprise fragile sur un signal graphique
    if note > 0:
        try:
            p = fundamentals.profil(symbole)
            croissance = p.get("croissance_ca")
            dette = p.get("dette_sur_capitaux")     # yfinance : en %
            if (croissance is not None and croissance < 0.05) \
                    or (dette is not None and dette > 200):
                note *= 0.5
        except Exception:
            pass                    # pas une action, ou données absentes
    return round(note, 1)


def dossier(symbole: str, horizon: int = 20, capital: float = 10_000.0,
            lookback_days: int = 1825) -> dict:
    """Dossier de décision complet pour un titre."""
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=lookback_days))
    prix = float(df["close"].iloc[-1])
    regime = forecast.regime(df)
    proj = forecast.projeter(df, horizon=horizon,
                             vol_cible=har.vol_cible(symbole))

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

    # Consensus des outils de courtier : CANDIDAT, pas encore une composante.
    # Il est journalisé (colonne c_brokers) pour que son IC se mesure comme
    # celui des autres, mais il ne pèse rien tant qu'il n'a pas fait ses
    # preuves. Une brique doit gagner sa place, pas la recevoir.
    candidats = {}
    try:
        cons = broker_tools.consensus(df)
        if cons.get("total"):
            candidats["brokers"] = round(
                (cons["haussiers"] - cons["baissiers"]) / cons["total"] * 100, 1)
    except Exception:
        pass
    # La « règle d'école » (EMA + RSI + filtre qualité) : journalisée pour
    # être jugée, muette dans la note tant qu'elle n'a rien prouvé.
    try:
        ecole = _regle_ecole(df, symbole)
        if ecole is not None:
            candidats["regle_ecole"] = ecole
    except Exception:
        pass
    # Trois briques de CONTEXTE DE MARCHÉ, candidates elles aussi : filtre de
    # tendance du marché de rattachement, force relative contre son indice,
    # alignement des échelles hebdomadaire et quotidienne. Journalisées et
    # mesurées comme les autres, sans peser tant qu'elles n'ont rien prouvé.
    contexte_detail = contexte.candidats(symbole, df)
    for nom, mesure in contexte_detail.items():
        candidats[nom] = round(float(mesure["note"]), 1)
    # Surprise économique : les statistiques d'un pays sortent-elles mieux ou
    # moins bien qu'attendu, comparées à celles d'en face. Candidate elle
    # aussi, et sur le FOREX uniquement — c'est un moteur de devise.
    # Elle restera muette un à deux mois : il faut deux publications d'un même
    # indicateur pour en déduire une seule surprise (cf. marketlab/surprise.py).
    try:
        eco = surprise.note(symbole)
        if eco:
            candidats["surprise"] = eco["note"]
    except Exception:
        pass  # brique candidate : elle s'absente, elle ne fait jamais échouer

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

    # VETO DE RÉGIME. Mesuré sur 3 232 verdicts : en marché tendu, le
    # classement est INVERSÉ — IC purgé −0,254, confirmé par 83 % des
    # découpages sans recouvrement. Dans ce régime, l'avis directionnel ne vaut
    # rien : il est donc suspendu.
    #
    # SUSPENDU, PAS RETOURNÉ. Retourner l'avis reviendrait à parier que l'effet
    # persiste, alors qu'il repose sur une dizaine d'observations réellement
    # indépendantes et qu'une seule période agitée peut le porter. Suspendre ne
    # coûte qu'une occasion manquée ; retourner à tort coûte de l'argent.
    # La note retournée est journalisée comme CANDIDATE : si elle fait ses
    # preuves sur des données fraîches, elle gagnera sa place.
    suspension = None
    taille_hors_suspension = taille
    try:
        # Import TARDIF, et c'est nécessaire : `regimes` importe `decision`
        # pour réutiliser sa mesure de compétence. Un import croisé en tête de
        # fichier fonctionnerait tant que l'ordre de chargement reste le bon,
        # et casserait le jour où quelqu'un importe l'autre module en premier.
        from marketlab import regimes
        suspension = regimes.avis_suspendu()
    except Exception:
        suspension = None      # garde-fou muet plutôt que publication bloquée
    if suspension:
        # DEUX PHRASES, PARCE QU'IL Y A DEUX MOTIFS. La formulation unique
        # affirmait « mesuré INVERSÉ … confirmé par X % des découpages » — et
        # publiait, pour une suspension prudentielle, « confirmé par 0,0 % ».
        # Une phrase qui se contredit dans sa propre parenthèse est pire qu'un
        # silence : elle apprend au lecteur à ne plus lire les vetos.
        etiquette = regimes.ETIQUETTES.get(suspension["regime"],
                                           suspension["regime"])
        if suspension.get("motif") == "démontré":
            vetos.append(
                f"VETO DE RÉGIME : en {etiquette}, le classement de cet outil "
                f"a été mesuré INVERSÉ (IC {suspension.get('ic_purge')}, "
                f"confirmé par {suspension.get('part_concluante_%')} % des "
                f"découpages sans recouvrement). L'avis directionnel est "
                f"suspendu — il n'est pas retourné, l'effet reposant sur trop "
                f"peu d'épisodes distincts.")
        else:
            vetos.append(
                f"ABSTENTION PRUDENTIELLE : en {etiquette}, la mesure penche "
                f"du mauvais côté (IC {suspension.get('ic_purge')}) sans le "
                f"démontrer — {suspension.get('part_concluante_%')} % des "
                f"découpages sans recouvrement concluent. Rien n'établit donc "
                f"que l'avis vaille quelque chose, et s'abstenir ne coûte "
                f"qu'une occasion manquée là où suivre coûte le spread, le "
                f"portage et le risque. Ce n'est PAS une inversion démontrée.")
        taille_hors_suspension = taille
        taille = 0.0
        candidats["note_retournee"] = round(-float(note_globale), 1)

    # DIMENSIONNEMENT PAR LE RISQUE. Recommandation seulement : le robot garde
    # sa taille fixe de 5 %, parce que ses trois variantes forment une
    # expérience contrôlée et que changer leur dimensionnement rendrait
    # ininterprétable tout l'historique déjà accumulé. Ce chiffre est là pour
    # être lu et comparé, pas pour agir dans le dos de l'expérience.
    try:
        from marketlab import dimensionnement
        taille_risque = dimensionnement.dimensionner(
            symbole, plan or {}, capital, horizon=horizon,
            avis_suspendu=suspension)
        taille_risque["comparaison"] = dimensionnement.comparer_a_taille_fixe(
            taille_risque, capital)
    except Exception as exc:
        taille_risque = {"retenue": False, "erreur": str(exc)[:80]}

    # --- avis final ---
    def _avis(t: float) -> str:
        if t == 0.0:
            return "S'abstenir"
        if note_globale >= 30:
            return "Favorable"
        if note_globale <= -30:
            return "Défavorable"
        return "Neutre"

    avis = _avis(taille)

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
        # Probabilité CALIBRÉE : celle que l'historique autorise réellement
        # pour une note de ce niveau. La combinée ci-dessus s'est révélée
        # pire qu'un tirage à pile ou face (Brier 0,297 contre 0,250) —
        # elle annonçait 83 % là où il montait 54 % du temps. On garde les
        # deux côte à côte pour que l'écart reste visible.
        "proba_calibree": calibration.proba_calibree(note_globale),
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
        # CE QUE L'AVIS SERAIT SANS LA SUSPENSION DE RÉGIME. Le site
        # affiche `avis` — il s'abstient, c'est la posture retenue. Mais
        # les robots sont l'APPAREIL DE MESURE : c'est leur activité qui
        # accumule les épisodes indépendants dont on a besoin pour un jour
        # prouver, ou réfuter, un avantage. Les faire taire refermerait la
        # boucle qui pourrait justifier de lever le silence : l'outil
        # s'interdirait de trader jusqu'à avoir prouvé qu'il sait trader,
        # sans plus jamais produire la preuve.
        #
        # Ces deux champs n'apparaissent QUE lorsqu'une suspension a
        # effectivement joué : leur présence signale l'écart, leur absence
        # dit qu'il n'y en a pas.
        **({"avis_hors_suspension": _avis(taille_hors_suspension),
            "taille_hors_suspension": taille_hors_suspension}
           if suspension else {}),
        "conclusion": conclusion,
        "concordance_%": round(concordance, 0),
        "taille_multiplicateur": taille,
        "dimensionnement": taille_risque,
        "composantes": [
            {"nom": nom, "poids": round(poids[nom], 3),
             "note": round(c["note"], 1), "raisons": c["raisons"]}
            for nom, c in composantes.items()],
        "candidats": candidats,
        "contexte_marche": contexte_detail,
        "ponderation": poids_meta,
        "vetos": vetos,
        "regime": regime,
        # « couts » et « esperance_nette_% » sont calculés par levels.py et
        # attendus par le front (App.jsx lit plan.couts). Les omettre ici
        # revenait à calculer le coût du trade, à l'afficher nulle part, et
        # à laisser l'utilisateur lire une espérance BRUTE en croyant
        # qu'elle était nette — l'écart exact que couts.py existe pour
        # combler.
        "plan": ({k: plan[k] for k in ("entree", "stop", "objectif",
                                       "ratio_gain_risque",
                                       "proba_toucher_stop_%",
                                       "proba_toucher_objectif_%",
                                       "esperance_%", "taille")
                  if k in plan}
                 | {k: plan[k] for k in ("couts", "esperance_nette_%")
                    if k in plan}
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
        # candidats : journalisés comme les autres pour pouvoir être jugés,
        # même s'ils ne pèsent encore rien dans la note
        for nom, note in (d.get("candidats") or {}).items():
            ligne[f"c_{nom}"] = note
        lignes.append(ligne)
    if not lignes:
        return 0
    nouveau = pd.DataFrame(lignes)
    if JOURNAL.exists():
        journal = pd.concat([pd.read_csv(JOURNAL), nouveau], ignore_index=True)
        # La clé inclut l'HORIZON : le même titre, le même jour, produit un
        # verdict par horizon suivi, et chacun doit garder sa propre trace.
        journal = journal.drop_duplicates(subset=["date", "symbole", "horizon"],
                                          keep="last")
    else:
        journal = nouveau
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    journal.to_csv(JOURNAL, index=False)
    return len(nouveau)


def bilan(horizon: int | None = None) -> dict:
    """Le tribunal de l'outil : que valaient les verdicts passés ?

    Pour chaque verdict dont l'horizon est écoulé, mesure le rendement réel
    et agrège par catégorie d'avis. C'est CE tableau qui dit si l'outil
    mérite d'être écouté — pas ses raisonnements.

    `horizon` limite le bilan aux verdicts produits pour cet horizon.
    """
    if not JOURNAL.exists():
        return {"verdicts_evalues": 0,
                "message": "Aucun verdict journalisé pour l'instant."}
    df = _evaluer_journal(horizon)
    if df.empty:
        return {"verdicts_evalues": 0,
                "message": "Aucun verdict n'a encore atteint son horizon — "
                           "le bilan se remplira avec le temps."}
    par_avis = []
    for avis, groupe in df.groupby("avis"):
        r = groupe["rendement_reel_%"]
        rel = groupe.get("rendement_relatif_%", r)
        attendu_hausse = avis == "Favorable"
        reussite = (r > 0).mean() if attendu_hausse else \
            (r < 0).mean() if avis == "Défavorable" else np.nan
        par_avis.append({
            "avis": avis, "n": len(groupe),
            "rendement_moyen_%": round(float(r.mean()), 2),
            "rendement_median_%": round(float(r.median()), 2),
            "relatif_moyen_%": round(float(rel.mean()), 2),
            "relatif_median_%": round(float(rel.median()), 2),
            "taux_reussite_%": (round(float(reussite) * 100, 1)
                                if reussite == reussite else None),
        })
    return {
        "verdicts_evalues": len(df),
        "premiere_date": df["date"].min().strftime("%Y-%m-%d"),
        "par_avis": par_avis,
        "competence": _mesurer_competence(df),
        "calibration": calibration.apprendre(df),
        "fiabilite_probas": calibration.courbe_fiabilite(df),
        "competence_par_horizon": [
            {**_mesurer_competence(df, f"rendement_h{h}_%", horizon_force=h),
             "horizon": h}
            for h in HORIZONS_MESURE if f"rendement_h{h}_%" in df.columns],
        "lecture": ("Un outil honnête montre son bilan, bon ou mauvais. "
                    "Deux colonnes à ne pas confondre : le rendement BRUT "
                    "dit ce qu'a fait le marché, le rendement RELATIF (dérive "
                    "propre de chaque actif retirée) dit ce qu'a apporté le "
                    "verdict lui-même. C'est le second qui juge l'outil."),
    }


def _mesurer_competence(df: pd.DataFrame, colonne: str = "rendement_reel_%",
                        horizon_force: int | None = None) -> dict:
    """La note globale prédit-elle quoi que ce soit — et peut-on le prouver ?

    Méthode : IC de Spearman TRANSVERSAL, calculé date par date entre la note
    et le rendement advenu, puis moyenné (Fama-MacBeth) avec une erreur-type
    de Newey-West.

    Pourquoi pas un simple IC sur toutes les lignes : les verdicts se
    recouvrent. Avec un horizon de 20 séances et des verdicts quasi
    quotidiens, deux lignes voisines parlent presque du même bout de marché.
    Les traiter comme indépendantes multiplie artificiellement la certitude —
    3 232 verdicts sur 2 ans ne pèsent en réalité qu'une vingtaine
    d'épisodes indépendants. Une première version de cette fonction est
    tombée dans ce piège et a annoncé comme « démontré » un résultat qui ne
    l'était pas.

    Ce que l'IC transversal mesure : la capacité à CLASSER les actifs entre
    eux à une date donnée — la question à laquelle l'outil sert vraiment.
    """
    if colonne not in df.columns or len(df) < 60:
        return {"statut": "pas encore mesurable"}
    horizon = horizon_force or (int(df["horizon"].median())
                                if "horizon" in df.columns else 20)

    ics = []
    for _, groupe in df.dropna(subset=["note", colonne]).groupby("date"):
        if len(groupe) < 8:          # un classement demande assez d'actifs
            continue
        ic = groupe["note"].rank().corr(groupe[colonne].rank())
        if ic == ic:
            ics.append(float(ic))
    n_dates = len(ics)
    if n_dates < 20:
        return {"statut": "pas encore assez de dates comparables",
                "n_dates": n_dates}

    serie = np.array(ics)
    moyenne = float(serie.mean())
    # Newey-West : les IC de dates proches sont corrélés entre eux
    retard = min(horizon, n_dates - 1)
    var = float(serie.var(ddof=1))
    var_simple = var
    for k in range(1, retard + 1):
        var += 2 * (1 - k / (retard + 1)) * float(
            np.cov(serie[:-k], serie[k:])[0, 1])

    # GARDE-FOU SUR LA CORRECTION. Newey-West additionne des autocovariances
    # estimées : sur un échantillon court, leur somme peut annuler la variance,
    # voire la rendre négative. Le plancher `max(var, 1e-12)` transformait
    # alors une variance nulle en certitude absolue — mesuré sur un
    # sous-échantillon de 63 dates : t = −288 819 pour un IC de −0,05.
    # Un t de cet ordre n'est pas un signal, c'est une division par zéro.
    #
    # La correction est donc REJETÉE quand elle détruit plus des trois quarts
    # de la variance : on retombe sur la variance simple, qui surestime la
    # certitude mais reste finie — et l'on signale que la correction n'a pas
    # tenu, plutôt que de publier un chiffre invraisemblable.
    correction_degeneree = var <= 0 or var < var_simple / 4
    if correction_degeneree:
        var = var_simple

    err = math.sqrt(max(var, 1e-12) / n_dates)
    t = moyenne / err if err > 0 else 0.0
    episodes = max(int(n_dates / max(horizon, 1)), 1)

    if t > 1.96:
        sens = "positif"
        lecture = ("la note classe les actifs dans le bon sens : à une date "
                   "donnée, les mieux notés font ensuite mieux que les autres.")
    elif t < -1.96:
        sens = "négatif"
        lecture = ("ATTENTION : la note classe les actifs à l'ENVERS de façon "
                   "démontrée. Les mieux notés font ensuite MOINS BIEN que "
                   "les moins bien notés.")
    else:
        sens = "indéterminé"
        lecture = (
            f"Aucune compétence démontrée, ni dans un sens ni dans l'autre. "
            f"L'écart mesuré ({moyenne:+.3f}) n'est pas distinguable du "
            f"hasard : sur {horizon} séances d'horizon, ces {n_dates} dates ne "
            f"valent qu'environ {episodes} épisodes de marché réellement "
            f"indépendants. Il faut plusieurs années de recul pour trancher — "
            f"d'ici là, les verdicts sont des points d'attention, pas des "
            f"recommandations.")
    # Puissance : quel IC réel serait détectable avec ce recul ? Un outil
    # « non concluant » sur un échantillon qui ne peut rien détecter n'est
    # pas la même chose qu'un outil non concluant sur un échantillon capable.
    detectable = round(2.8 * err, 3) if err > 0 else None
    return {"n_dates": n_dates, "episodes_independants": episodes,
            # Signalé et non tu : une correction rejetée veut dire que
            # l'échantillon est trop court pour la mériter, donc que le t
            # affiché SURESTIME la certitude.
            "correction_rejetee": bool(correction_degeneree),
            "ic_transversal_moyen": round(moyenne, 4), "t": round(t, 2),
            "horizon_seances": horizon, "sens": sens, "lecture": lecture,
            "ic_detectable": detectable,
            "methode": ("IC de Spearman transversal par date (Fama-MacBeth), "
                        "erreur-type de Newey-West au retard de l'horizon.")}


def _evaluer_journal(horizon: int | None = None) -> pd.DataFrame:
    """Verdicts arrivés à leur horizon, avec le rendement réellement advenu.

    `horizon` restreint aux verdicts PRODUITS pour cet horizon — un verdict
    à 5 séances et un verdict à 20 séances sur le même titre le même jour
    sont deux paris différents, et chacun mérite son propre bilan.

    Conserve toutes les colonnes du journal — dont les notes c_* par
    composante, matière première de l'apprentissage des pondérations.
    """
    if not JOURNAL.exists():
        return pd.DataFrame()
    journal = pd.read_csv(JOURNAL)
    journal["date"] = pd.to_datetime(journal["date"])
    if horizon is not None:
        journal = journal[journal["horizon"] == horizon]
        if journal.empty:
            return pd.DataFrame()

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
            ligne_evaluee = {**ligne.to_dict(), "rendement_reel_%": realise}
            # Le MÊME verdict est aussi mesuré à d'autres horizons. C'est
            # gratuit (les notes et le prix d'entrée sont déjà là) et c'est le
            # seul moyen d'obtenir vite une réponse : à 20 séances, deux ans
            # ne pèsent qu'une dizaine d'épisodes indépendants ; à 5 séances,
            # quatre fois plus.
            for h in HORIZONS_MESURE:
                ligne_evaluee[f"rendement_h{h}_%"] = (
                    float(futurs.iloc[h - 1] / ligne["prix"] - 1) * 100
                    if len(futurs) >= h else None)
            evalues.append(ligne_evaluee)
    df = pd.DataFrame(evalues)
    if df.empty:
        return df

    # Rendement RELATIF : on retire de chaque verdict la dérive propre de son
    # actif (médiane de tous les verdicts portant sur lui). Sans cela on
    # confond deux choses très différentes : « le marché est monté » et « le
    # verdict a bien choisi ». Un outil qui note tout à la hausse pendant un
    # marché haussier paraît bon sur le rendement brut ; c'est le rendement
    # relatif qui mesure la part réellement attribuable au verdict.
    derive = df.groupby("symbole")["rendement_reel_%"].transform("median")
    df["rendement_relatif_%"] = df["rendement_reel_%"] - derive
    return df


# --- Apprentissage des pondérations ------------------------------------------

def _calculer_poids(df_evalues: pd.DataFrame, poids_base: dict | None = None,
                    lam_max: float = 0.5, min_par_composante: int = 30,
                    min_total: int = 60, cible: str = "rendement_relatif_%") -> dict:
    """Le calcul pur : du journal évalué aux pondérations ajustées.

    Méthode, volontairement conservatrice :
    - chaque composante est jugée par l'IC de Spearman entre ses notes au
      moment du verdict et le rendement RELATIF advenu — dérive propre de
      l'actif retirée. Juger sur le rendement brut récompenserait un outil
      qui se contente de suivre un marché haussier ;
    - l'IC est borné par le bas à 95 % : une composante ne gagne du poids que
      si sa valeur est DÉMONTRÉE, jamais sur un IC de ±0,02 qui n'est que du
      bruit. Si aucune ne démontre rien, les pondérations ne bougent pas ;
    - seule la part POSITIVE compte (une composante anti-corrélée ne
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

    if cible not in df_evalues.columns:
        cible = "rendement_reel_%"
    rendement = df_evalues[cible]
    horizon = (int(df_evalues["horizon"].median())
               if "horizon" in df_evalues.columns else 20)
    rapport["cible"] = cible
    rapport["horizon_seances"] = horizon
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
        ic = float(paires[colonne].rank().corr(paires[cible].rank()))
        # Un IC nu ne dit pas s'il est réel : ±0,02 sur 3 000 verdicts est du
        # bruit. On borne l'IC par le bas à 95 % (transformée de Fisher) et
        # c'est CETTE borne qui donne droit à du poids : une composante doit
        # PROUVER sa valeur, pas simplement avoir eu de la chance.
        #
        # Taille d'échantillon EFFECTIVE : avec un horizon de 20 séances et
        # des verdicts quasi quotidiens, 20 lignes voisines décrivent presque
        # le même bout de marché. Compter n lignes indépendantes gonflerait la
        # certitude d'un facteur √20 ≈ 4,5 — de quoi « prouver » du bruit.
        n_c = max(len(paires) / max(horizon, 1), 10)
        err = 1.0 / math.sqrt(max(n_c - 3, 1))
        borne_basse = math.tanh(math.atanh(max(min(ic, 0.999), -0.999))
                                - 1.96 * err)
        ics[nom] = borne_basse
        rapport["ic_par_composante"][nom] = {
            "ic": round(ic, 3), "n": int(len(paires)),
            "n_effectif": int(n_c),
            "ic_borne_basse_95": round(borne_basse, 3),
            "prouve": bool(borne_basse > 0)}

    # Candidats : briques journalisées qui ne pèsent pas encore. On mesure
    # leur IC exactement comme les autres — c'est leur seul chemin vers une
    # place dans le verdict.
    rapport["candidats"] = {}
    for colonne in [c for c in df_evalues.columns
                    if c.startswith("c_") and c[2:] not in poids_base]:
        paires = df_evalues[[colonne]].join(rendement).dropna()
        if len(paires) < min_par_composante:
            rapport["candidats"][colonne[2:]] = {
                "ic": None, "n": int(len(paires)),
                "note": "pas encore assez de verdicts évalués"}
            continue
        ic = float(paires[colonne].rank().corr(paires[cible].rank()))
        n_eff = max(len(paires) / max(horizon, 1), 10)   # fenêtres recouvrantes
        err = 1.0 / math.sqrt(max(n_eff - 3, 1))
        bas = math.tanh(math.atanh(max(min(ic, 0.999), -0.999)) - 1.96 * err)
        rapport["candidats"][colonne[2:]] = {
            "ic": round(ic, 3), "n": int(len(paires)),
            "n_effectif": int(n_eff),
            "ic_borne_basse_95": round(bas, 3), "prouve": bool(bas > 0)}

    if len(ics) < 3:
        rapport["statut"] = ("moins de 3 composantes mesurables : "
                             "pondérations de base conservées")
        return rapport

    if not any(v > 0 for v in ics.values()):
        # Cas honnête et fréquent : rien n'est encore démontré. Bouger les
        # pondérations reviendrait à ajuster du bruit — on ne bouge pas.
        rapport["statut"] = (
            f"aucune composante n'a démontré de pouvoir prédictif sur "
            f"{n} verdicts (aucun IC significativement positif à 95 %) : "
            "pondérations de base conservées")
        return rapport

    # performance : part POUVANT être prouvée de l'IC, plancher epsilon
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
    rapport["date"] = pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M")
    rapport["poids_base"] = POIDS
    rapport["methode"] = (
        "IC de Spearman entre la note de chaque composante au moment du "
        "verdict et le rendement RELATIF advenu à l'horizon (dérive propre "
        "de l'actif retirée, pour ne pas récompenser un simple marché "
        "haussier). Une composante ne gagne du poids que si la borne basse "
        "de son IC à 95 % est positive — autrement dit si sa valeur est "
        "démontrée, pas seulement observée. La borne est calculée sur la "
        "taille d'échantillon EFFECTIVE (nombre de verdicts ÷ horizon) : les "
        "fenêtres se recouvrent, et les compter comme indépendantes "
        "gonflerait la certitude d'un facteur √20. Mélange progressif "
        "(1−λ)·base + λ·performance, λ = min(0,5, n/400) ; plancher 2 %.")
    # Le rapport est écrit dans TOUS les cas, y compris « rien n'est
    # démontré » : c'est la conclusion du jour, et elle doit remplacer celle
    # de la veille. Ne l'écrire qu'en cas de succès laisserait d'anciennes
    # pondérations piloter le verdict après que la mesure les a invalidées.
    POIDS_APPRIS.parent.mkdir(parents=True, exist_ok=True)
    POIDS_APPRIS.write_text(
        json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")

    # La calibration des probabilités s'apprend ICI, dans le même passage :
    # `calibrer()` s'exécute avant la génération des verdicts, donc le modèle
    # est en place quand les dossiers du jour appellent proba_calibree().
    try:
        modele = calibration.apprendre(df)
        if modele.get("paliers"):
            calibration.enregistrer(modele)
        rapport["calibration_probas"] = modele.get("statut")
    except Exception as exc:
        rapport["calibration_probas"] = f"non calculée : {str(exc)[:80]}"
    return rapport
