"""Analyse fondamentale des actions : valorisation, qualité, croissance, solidité.

Source : Yahoo Finance via yfinance (gratuit, sans clé). Ne s'applique qu'aux
ACTIONS — une crypto, une paire de devises ou un indice n'ont pas de bilan.

Le score est volontairement transparent : quatre axes notés de 0 à 100 à
partir de seuils explicites (constante `SEUILS`), puis moyenne pondérée. Il
sert à comparer et à filtrer, pas à décider seul — un PER bas peut signaler
une décote comme une entreprise en difficulté.

Limites assumées : les données Yahoo sont parfois incomplètes ou décalées
d'un trimestre ; les critères ne sont pas normalisés par secteur (une banque
et un éditeur de logiciels n'ont pas les mêmes marges), d'où `comparer()` qui
met en regard des titres d'un même univers.
"""

import json
import time

import pandas as pd
import yfinance as yf

from marketlab import config

CACHE_TTL_H = 24
NON_ACTIONS = ("=X", "USDT")


def est_action(symbole: str) -> bool:
    """Les fondamentaux n'ont de sens que pour une action."""
    if symbole.startswith("^") or symbole in config.BRVM:
        return False
    return not symbole.endswith(NON_ACTIONS)


# Champ yfinance -> nom lisible
CHAMPS = {
    "shortName": "nom", "sector": "secteur", "industry": "industrie",
    "marketCap": "capitalisation", "currency": "devise",
    "trailingPE": "per", "forwardPE": "per_prevu", "priceToBook": "cours_sur_actif",
    "enterpriseToEbitda": "ev_sur_ebitda",
    "grossMargins": "marge_brute", "operatingMargins": "marge_operationnelle",
    "profitMargins": "marge_nette",
    "returnOnEquity": "rentabilite_capitaux", "returnOnAssets": "rentabilite_actifs",
    "revenueGrowth": "croissance_ca", "earningsGrowth": "croissance_benefice",
    "debtToEquity": "dette_sur_capitaux", "currentRatio": "liquidite_generale",
    "dividendYield": "rendement_dividende", "payoutRatio": "taux_distribution",
    "beta": "beta", "freeCashflow": "flux_tresorerie_libre",
    "targetMeanPrice": "objectif_analystes", "numberOfAnalystOpinions": "nb_analystes",
    "recommendationKey": "recommandation_analystes",
}


def _cache_path(symbole: str):
    return config.CACHE_DIR / f"fonda_{symbole.replace('.', '_')}.json"


def profil(symbole: str) -> dict:
    """Indicateurs fondamentaux normalisés (valeurs brutes, sans jugement)."""
    if not est_action(symbole):
        raise RuntimeError(f"{symbole} n'est pas une action : pas de fondamentaux")

    chemin = _cache_path(symbole)
    if chemin.exists() and time.time() - chemin.stat().st_mtime < CACHE_TTL_H * 3600:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
    else:
        try:
            brut = yf.Ticker(symbole).info or {}
        except Exception as exc:
            raise RuntimeError(f"Fondamentaux indisponibles pour {symbole} : {exc}")
        if not brut.get("shortName"):
            raise RuntimeError(f"Aucune donnée fondamentale pour {symbole}")
        try:
            chemin.write_text(json.dumps(brut, default=str), encoding="utf-8")
        except Exception:
            pass

    out = {"symbole": symbole}
    for cle_yf, nom in CHAMPS.items():
        valeur = brut.get(cle_yf)
        if isinstance(valeur, (int, float)) and pd.notna(valeur):
            out[nom] = float(valeur)
        elif isinstance(valeur, str) and valeur:
            out[nom] = valeur
        else:
            out[nom] = None
    return out


# --- Notation ---------------------------------------------------------------
# (seuil_excellent, seuil_mediocre) : la note décroît linéairement entre les deux.
# Un critère "plus c'est petit, mieux c'est" a excellent < mediocre.
SEUILS = {
    "per": (10, 40),                    # valorisation : bas = attractif
    "cours_sur_actif": (1.0, 6.0),
    "ev_sur_ebitda": (6, 20),
    "marge_operationnelle": (0.30, 0.03),   # qualité : haut = mieux
    "marge_nette": (0.20, 0.02),
    "rentabilite_capitaux": (0.25, 0.05),
    "croissance_ca": (0.20, -0.05),          # croissance
    "croissance_benefice": (0.25, -0.10),
    "dette_sur_capitaux": (30, 200),         # solidité : bas = mieux
    "liquidite_generale": (2.5, 0.8),
}

AXES = {
    "valorisation": ["per", "cours_sur_actif", "ev_sur_ebitda"],
    "qualite": ["marge_operationnelle", "marge_nette", "rentabilite_capitaux"],
    "croissance": ["croissance_ca", "croissance_benefice"],
    "solidite": ["dette_sur_capitaux", "liquidite_generale"],
}
POIDS = {"valorisation": 0.30, "qualite": 0.30, "croissance": 0.25, "solidite": 0.15}


def _noter(critere: str, valeur: float | None) -> float | None:
    if valeur is None:
        return None
    excellent, mediocre = SEUILS[critere]
    if critere in ("per", "cours_sur_actif", "ev_sur_ebitda") and valeur <= 0:
        return 0.0  # PER négatif = perte : pas une bonne affaire
    pente = (valeur - mediocre) / (excellent - mediocre)
    return float(max(0.0, min(1.0, pente)) * 100)


def noter(symbole: str) -> dict:
    """Note 0-100 par axe + score global, avec le détail des critères notés."""
    p = profil(symbole)
    detail, notes_axes = {}, {}
    for axe, criteres in AXES.items():
        notes = []
        for c in criteres:
            n = _noter(c, p.get(c))
            detail[c] = {"valeur": p.get(c), "note": None if n is None else round(n, 1)}
            if n is not None:
                notes.append(n)
        notes_axes[axe] = round(sum(notes) / len(notes), 1) if notes else None

    disponibles = {a: n for a, n in notes_axes.items() if n is not None}
    if disponibles:
        poids_total = sum(POIDS[a] for a in disponibles)
        global_ = sum(n * POIDS[a] for a, n in disponibles.items()) / poids_total
    else:
        global_ = None

    couverture = sum(1 for d in detail.values() if d["note"] is not None) / len(detail)
    return {
        "symbole": symbole,
        "nom": p.get("nom"), "secteur": p.get("secteur"),
        "capitalisation": p.get("capitalisation"), "devise": p.get("devise"),
        "axes": notes_axes,
        "score_global": round(global_, 1) if global_ is not None else None,
        "appreciation": _appreciation(global_),
        "couverture_donnees_%": round(couverture * 100),
        "detail": detail,
        # yfinance renvoie dividendYield DÉJÀ en pourcentage (1.70 = 1,70 %) :
        # ne pas multiplier par 100, sous peine d'afficher 170 %.
        "dividende_%": (round(p["rendement_dividende"], 2)
                        if p.get("rendement_dividende") else None),
        "beta": p.get("beta"),
        "objectif_analystes": p.get("objectif_analystes"),
        "nb_analystes": p.get("nb_analystes"),
    }


def _appreciation(score: float | None) -> str:
    if score is None:
        return "données insuffisantes"
    if score >= 70:
        return "profil fondamental solide"
    if score >= 55:
        return "profil correct"
    if score >= 40:
        return "profil moyen"
    return "profil fondamental faible"


def comparer(symboles: list[str]) -> pd.DataFrame:
    """Tableau comparatif classé par score global.

    À utiliser au sein d'un même secteur : les seuils ne sont pas normalisés
    par industrie.
    """
    lignes = []
    for s in symboles:
        if not est_action(s):
            continue
        try:
            n = noter(s)
            lignes.append({
                "symbole": s, "nom": n["nom"], "secteur": n["secteur"],
                "score": n["score_global"],
                "valorisation": n["axes"]["valorisation"],
                "qualite": n["axes"]["qualite"],
                "croissance": n["axes"]["croissance"],
                "solidite": n["axes"]["solidite"],
                "per": n["detail"]["per"]["valeur"],
                "marge_nette_%": (round(n["detail"]["marge_nette"]["valeur"] * 100, 1)
                                  if n["detail"]["marge_nette"]["valeur"] else None),
                "croissance_ca_%": (round(n["detail"]["croissance_ca"]["valeur"] * 100, 1)
                                    if n["detail"]["croissance_ca"]["valeur"] else None),
                "dividende_%": n["dividende_%"],
                "couverture_%": n["couverture_donnees_%"],
            })
        except RuntimeError as exc:
            lignes.append({"symbole": s, "nom": None, "score": None,
                           "erreur": str(exc)[:60]})
    return (pd.DataFrame(lignes)
            .sort_values("score", ascending=False, na_position="last")
            .reset_index(drop=True))
