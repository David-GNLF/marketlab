"""Génération d'un instantané statique publiable sur un hébergement mutualisé.

Bascule d'architecture : au lieu de calculer pendant la requête web — ce qu'un
hébergement mutualisé ne permet pas (délais courts, pas de processus
permanent, quotas serrés) — tout est calculé **en amont** par une tâche
planifiée, puis publié sous forme de fichiers JSON.

Le site devient alors un ensemble de fichiers statiques : aucune dépendance
Python côté serveur, aucun calcul à la visite, un affichage instantané, et
un fonctionnement garanti sur n'importe quel hébergement — y compris depuis
un téléphone.

Sortie (dossier `site/`) :
    index.html, assets/…        le front React compilé
    donnees/meta.json           horodatage et inventaire
    donnees/screener.json       tableau de bord des signaux
    donnees/macro.json          régime macroéconomique
    donnees/calendrier.json     événements économiques à venir
    donnees/resultats.json      publications trimestrielles à venir
    donnees/correlations.json   matrice et risque du portefeuille
    donnees/fondamentaux.json   notation des actions
    donnees/titres/<SYM>.json   fiche complète par titre

Chaque bloc est calculé indépendamment : l'échec de l'un n'empêche pas la
publication des autres, et l'erreur est consignée dans meta.json.
"""

import json
import shutil
import traceback
from pathlib import Path

import pandas as pd

from marketlab import (broker_tools, config, correlations, cot, decision,
                       drivers, eco_calendar, events, forecast, fundamentals,
                       indicators, levels, macro, news, paper, position,
                       screener, seasonality, sentiment_marche, signals)
from marketlab.data import get_ohlcv

RACINE_SITE = config.ROOT / "site"
DOSSIER_DONNEES = RACINE_SITE / "donnees"

# Titres pour lesquels une fiche détaillée (salle de marché) est produite.
# Chaque fiche coûte des requêtes réseau et quelques secondes de calcul :
# la liste reste un choix, pas un « tout l'univers ».
TITRES_DETAILLES = (config.ACTIONS_US[:8] + config.ACTIONS_EU[:4]
                    + config.CRYPTO[:3] + config.FOREX + config.MATIERES)


def _ecrire(chemin: Path, donnees) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(donnees, ensure_ascii=False, default=str),
                      encoding="utf-8")


def _table(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> enregistrements JSON (NaN -> None, dates ISO)."""
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index(names="date")
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M")
    return out.astype(object).where(out.notna(), None).to_dict(orient="records")


# --- Blocs ------------------------------------------------------------------

def bloc_screener() -> list[dict]:
    symboles = [s for u in ("Actions US", "Actions EU", "Indices", "Forex",
                            "Crypto") for s in config.UNIVERS[u]]
    return _table(screener.scan(symboles))


def bloc_macro() -> dict:
    return {"regime": macro.regime(), "indicateurs": _table(macro.snapshot())}


def bloc_calendrier() -> list[dict]:
    df = eco_calendar.get_events(impacts=["High", "Medium"])
    df = df[df["quand"] >= pd.Timestamp.now().normalize()]
    return _table(df.head(60))


def bloc_resultats() -> list[dict]:
    symboles = config.ACTIONS_US + config.ACTIONS_EU
    return _table(events.prochaines_publications(symboles, jours=45))


def bloc_fondamentaux() -> list[dict]:
    return _table(fundamentals.comparer(config.ACTIONS_US + config.ACTIONS_EU))


def bloc_correlations() -> dict:
    symboles = config.ACTIONS_US[:10] + config.ACTIONS_EU[:4] + config.CRYPTO[:2]
    matrice = correlations.matrice(symboles)
    resultat = {
        "symboles": list(matrice.columns),
        "matrice": {c: {k: float(v) for k, v in matrice[c].items()}
                    for c in matrice.columns},
        "extremes": correlations.paires_extremes(symboles),
        "par_regime": correlations.correlation_par_regime(symboles),
    }
    try:
        resultat["portefeuille"] = correlations.analyser_paper()
    except Exception:
        resultat["portefeuille"] = None  # moins de 2 positions, ou données absentes
    return resultat


def bloc_paper() -> dict | None:
    try:
        etat = paper.etat()
    except RuntimeError:
        return None
    etat["positions"] = _table(etat["positions"]) if len(etat["positions"]) else []
    return etat


def fiche_titre(symbole: str) -> dict:
    """Fiche complète d'un titre : cours, signaux, prévision, niveaux, contexte."""
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=1825))
    fiche = {"symbole": symbole,
             "nom": config.NOMS_ACTIFS.get(symbole, symbole)}

    sig = signals.compute_signals(df)
    sig["avis"] = signals.label(sig["score"])
    fiche["signaux"] = sig

    colonnes = ["close", "sma50", "sma200", "rsi14", "bb_upper", "bb_lower"]
    historique = df[colonnes].tail(260).round(4)
    fiche["historique"] = _table(historique)

    fiche["brokers"] = broker_tools.analyse(df)

    for nom, calcul in (
        # la stratégie répond aux 3 questions (quand / quel sens / quelle
        # marge) et embarque le verdict complet du moteur de décision
        ("strategie", lambda: position.strategie(symbole)),
        # moteurs fondamentaux propres à la classe d'actif (carry, taux
        # réels, structure à terme) — liste vide pour une action, c'est normal
        ("moteurs", lambda: drivers.moteurs(symbole)),
        ("regime", lambda: forecast.regime(df)),
        ("projection", lambda: {k: v for k, v in
                                forecast.projeter(df, horizon=20).items()
                                if not k.startswith("_")}),
        ("analogues", lambda: forecast.analogues(df, horizon=20)),
        ("niveaux", lambda: {"zones": levels.zones_proches(df),
                             "pivots": levels.pivots(df)}),
        ("saisonnalite", lambda: seasonality.analyser(symbole)),
        ("sentiment", lambda: news.sentiment(symbole)),
    ):
        try:
            fiche[nom] = calcul()
        except Exception as exc:
            fiche[nom] = {"erreur": str(exc)[:120]}

    if events.a_des_resultats(symbole):
        try:
            fiche["resultats"] = events.risque_evenement(symbole, horizon=20)
        except Exception as exc:
            fiche["resultats"] = {"erreur": str(exc)[:120]}
    return fiche


def _classe_actif(symbole: str) -> str:
    if symbole.endswith("=X"):
        return "Forex"
    if symbole.endswith("=F"):
        return "Matières"
    if symbole.endswith("USDT"):
        return "Crypto"
    if symbole.startswith("^"):
        return "Indices"
    return "Actions"


def bloc_verdicts() -> dict:
    """Dossiers de décision + journalisation + bilan des verdicts passés.

    C'est le bloc central du site : la synthèse motivée de toutes les
    analyses, et le tableau qui mesure ce que valaient les verdicts
    précédents une fois leur horizon écoulé. Chaque dossier est enrichi de
    la classe d'actif (filtres du front) et du consensus des six outils
    brokers (ADX, Supertrend, Ichimoku, Fibonacci, Stochastique, OBV).
    """
    dossiers = decision.verdicts(TITRES_DETAILLES)
    for d in dossiers:
        if "erreur" in d:
            continue
        d["classe"] = _classe_actif(d["symbole"])
        d["nom"] = config.NOMS_ACTIFS.get(d["symbole"], d["symbole"])
        try:
            df = indicators.enrich(get_ohlcv(d["symbole"], lookback_days=1825))
            d["brokers"] = broker_tools.consensus(df)
        except Exception as exc:
            d["brokers"] = {"texte": f"indisponible : {str(exc)[:60]}"}
    decision.journaliser(dossiers)
    return {"dossiers": dossiers, "bilan": decision.bilan()}


# --- Orchestration ----------------------------------------------------------

def bloc_cot() -> list[dict]:
    """Panorama COT : positionnement hebdomadaire des spéculateurs (CFTC)."""
    return _table(cot.panorama())


BLOCS = {
    # le calibrage s'exécute AVANT les verdicts : les dossiers du jour
    # utilisent aussitôt les pondérations apprises du bilan réel
    "apprentissage": decision.calibrer,
    "verdicts": bloc_verdicts,
    "cot": bloc_cot,
    "sentiment_marche": sentiment_marche.indice,
    "barometres": drivers.barometres,
    "screener": bloc_screener,
    "macro": bloc_macro,
    "calendrier": bloc_calendrier,
    "resultats": bloc_resultats,
    "fondamentaux": bloc_fondamentaux,
    "correlations": bloc_correlations,
    "paper": bloc_paper,
}


def generer(titres: list[str] | None = None, blocs: list[str] | None = None,
            verbeux: bool = True) -> dict:
    """Produit l'instantané complet dans `site/`. Renvoie le bilan."""
    titres = titres if titres is not None else TITRES_DETAILLES
    noms_blocs = blocs if blocs is not None else list(BLOCS)
    DOSSIER_DONNEES.mkdir(parents=True, exist_ok=True)

    erreurs, produits = {}, []
    for nom in noms_blocs:
        if verbeux:
            print(f"  {nom}…", flush=True)
        try:
            _ecrire(DOSSIER_DONNEES / f"{nom}.json", BLOCS[nom]())
            produits.append(nom)
        except Exception as exc:
            erreurs[nom] = f"{type(exc).__name__}: {exc}"
            if verbeux:
                print(f"    ECHEC : {str(exc)[:120]}", flush=True)

    fiches = []
    for symbole in titres:
        if verbeux:
            print(f"  fiche {symbole}…", flush=True)
        try:
            _ecrire(DOSSIER_DONNEES / "titres" / f"{symbole}.json",
                    fiche_titre(symbole))
            fiches.append(symbole)
        except Exception as exc:
            erreurs[f"titre:{symbole}"] = f"{type(exc).__name__}: {exc}"
            if verbeux:
                print(f"    ECHEC : {str(exc)[:120]}", flush=True)

    meta = {
        # heure du Bénin calculée depuis UTC : la machine qui génère (poste
        # local ou runner GitHub) peut être sur n'importe quel fuseau
        "genere_le": (pd.Timestamp.utcnow() + pd.Timedelta(hours=1))
                     .strftime("%Y-%m-%d %H:%M"),
        "fuseau": "Bénin (UTC+1)",
        "blocs": produits,
        "titres": fiches,
        "univers": config.UNIVERS,
        "erreurs": erreurs,
        "avertissement": ("Analyses statistiques, pas des prédictions. "
                          "Aucun contenu ne constitue un conseil en "
                          "investissement."),
    }
    _ecrire(DOSSIER_DONNEES / "meta.json", meta)
    return meta


def copier_front() -> bool:
    """Copie le front React compilé (`front/dist`) à la racine du site."""
    dist = config.ROOT / "front" / "dist"
    if not dist.exists():
        return False
    for element in dist.iterdir():
        cible = RACINE_SITE / element.name
        if element.is_dir():
            shutil.rmtree(cible, ignore_errors=True)
            shutil.copytree(element, cible)
        else:
            RACINE_SITE.mkdir(parents=True, exist_ok=True)
            shutil.copy2(element, cible)
    return True
