"""Magasin de barres intrajournalières et volatilité réalisée.

POURQUOI CE MODULE. Jusqu'ici tout MarketLab tournait sur des bougies
QUOTIDIENNES : le socle `data/` acceptait bien un paramètre `interval`, mais
aucun module métier ne l'a jamais utilisé. On demandait donc de la réactivité
à un moteur qui ne voyait qu'un point par jour — structurellement impossible.

Le patron repris des plateformes professionnelles tient en trois étages :

  * capture   — UN appel réseau groupé par intervalle, pas un par titre
                (`yahoo.get_ohlcv_multi`), à la manière d'un « feed handler »
  * jour en cours  — les barres du jour, réécrites à chaque balayage
  * historique     — les jours passés, mêmes fichiers, partitionnés par date

Les barres brutes vivent dans `.cache/intraday/` : volumineuses, régénérables,
et le dépôt est public. Seul l'agrégat quotidien — la volatilité réalisée, une
ligne par titre et par jour — est versionné, parce qu'il est minuscule et
IRRÉCUPÉRABLE autrement : Yahoo ne sert les barres 5 min que sur 60 jours
glissants. Sans relevé conservé, l'historique ne dépassera jamais deux mois.

Ce module MESURE la volatilité. Il ne prédit rien : l'exploitation prédictive
(HAR-RV) est un lot distinct, et elle porte sur la volatilité, jamais sur la
direction — la direction a été mesurée non prédictible sur ce projet.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from marketlab import config
from marketlab.data import base, binance, yahoo

INTERVALLE_DEFAUT = "5m"

# Profondeur demandée pendant la veille. Deux jours suffisent : l'objet est la
# séance en cours, et la veille repasse toutes les 10 minutes. Demander 60
# jours à chaque balayage épuiserait les fournisseurs gratuits pour rien.
JOURS_VEILLE = 2

# Profondeur du relevé quotidien de volatilité. 5 jours, pas 1 : si un
# passage a été manqué (les exécutions planifiées de GitHub sont au
# mieux-effort), le relevé suivant rattrape les jours sautés au lieu de laisser
# un trou définitif dans l'historique.
JOURS_RELEVE = 5

# Plancher absolu : en dessous, une séance n'est pas mesurable du tout.
BARRES_MIN = 10

# Plancher RELATIF à l'instrument, en fraction de son nombre de barres médian.
#
# POURQUOI DEUX SEUILS. Un seul seuil absolu ne peut pas convenir : une séance
# actions complète fait 78 barres de 5 min, une séance forex 288. Mesuré au
# premier essai réel : EURUSD=X un dimanche soir → 11 barres et 0,68 % de vol
# annualisée (contre 4 à 7 % les jours pleins), GC=F un jour tronqué → 47
# barres contre 274 la veille et 8,7 % contre 16,5 %. Ces séances partielles
# ne sont pas des journées calmes, ce sont des journées MAL MESURÉES, et comme
# l'historique est immuable elles resteraient à fausser toute modélisation de
# la volatilité. Le seuil relatif se calibre tout seul, par instrument.
FRACTION_BARRES_MIN = 0.6

# 252 séances : convention de place pour annualiser une variance quotidienne.
SEANCES_AN = 252

COLONNES_RV = ["date", "symbole", "interval", "rv", "n_barres", "vol_annualisee_%"]


# ---------------------------------------------------------------------------
# Magasin de barres
# ---------------------------------------------------------------------------

def _dossier(symbole: str, interval: str) -> Path:
    return config.INTRADAY_DIR / base.nom_fichier(symbole) / interval


def ecrire_partition(symbole: str, interval: str, df: pd.DataFrame) -> int:
    """Écrit `df` en un fichier par journée. Renvoie le nombre de barres.

    Réécriture COMPLÈTE de chaque journée touchée, pas un ajout : les
    fournisseurs renvoient la séance entière à chaque appel, donc réécrire est
    idempotent alors qu'ajouter dupliquerait les barres à chaque balayage.
    """
    if df is None or df.empty:
        return 0
    dossier = _dossier(symbole, interval)
    dossier.mkdir(parents=True, exist_ok=True)
    ecrites = 0
    for jour, part in df.groupby(df.index.normalize()):
        chemin = dossier / f"date={pd.Timestamp(jour).date().isoformat()}.parquet"
        try:
            part.to_parquet(chemin)
            ecrites += len(part)
        except Exception:
            continue  # le magasin est un confort, jamais bloquant
    return ecrites


def lire(symbole: str, interval: str = INTERVALLE_DEFAUT,
         depuis: str | dt.date | None = None) -> pd.DataFrame:
    """Relit les barres archivées d'un titre, toutes journées confondues."""
    dossier = _dossier(symbole, interval)
    if not dossier.exists():
        return pd.DataFrame(columns=base.COLUMNS)
    borne = pd.Timestamp(depuis).normalize() if depuis is not None else None
    morceaux = []
    for chemin in sorted(dossier.glob("date=*.parquet")):
        if borne is not None:
            try:
                jour = pd.Timestamp(chemin.stem.split("=", 1)[1])
            except Exception:
                jour = None
            if jour is not None and jour < borne:
                continue
        try:
            morceaux.append(pd.read_parquet(chemin))
        except Exception:
            continue
    if not morceaux:
        return pd.DataFrame(columns=base.COLUMNS)
    df = pd.concat(morceaux).sort_index()
    # une même barre peut avoir été écrite par deux balayages : la dernière
    # version gagne (elle est au moins aussi complète)
    return df[~df.index.duplicated(keep="last")]


def journees_archivees(symbole: str, interval: str = INTERVALLE_DEFAUT) -> list[str]:
    dossier = _dossier(symbole, interval)
    if not dossier.exists():
        return []
    return sorted(c.stem.split("=", 1)[1] for c in dossier.glob("date=*.parquet"))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _router(symboles: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Répartit les symboles par fournisseur. Renvoie (yahoo, binance, écartés).

    La BRVM est écartée : ses cours arrivent par CSV manuel, en quotidien
    seulement — il n'y a pas d'intrajournalier à capturer.

    Twelve Data est volontairement contourné ici : l'offre gratuite n'a pas
    d'appel groupé, et l'intérêt de ce module est justement de n'émettre qu'une
    requête. Le chemin quotidien continue de le préférer quand une clé existe.
    """
    yahoos, binances, ecartes = [], [], []
    for sym in symboles:
        if sym in getattr(config, "BRVM", []):
            ecartes.append(sym)
        elif sym in getattr(config, "CRYPTO", []) or sym.endswith("USDT"):
            binances.append(sym)
        else:
            yahoos.append(sym)
    return yahoos, binances, ecartes


def capturer(symboles: list[str], interval: str = INTERVALLE_DEFAUT,
             jours: int = JOURS_VEILLE) -> dict:
    """Capture et archive les barres de `symboles`. Ne lève jamais.

    Renvoie un bilan : titres archivés, barres écrites, titres écartés (pas
    d'intrajournalier disponible) et titres en échec.
    """
    yahoos, binances, ecartes = _router(symboles)
    bilan = {"titres": 0, "barres": 0, "ecartes": ecartes, "echecs": []}

    if yahoos:
        try:
            lots = yahoo.get_ohlcv_multi(yahoos, interval=interval, lookback_days=jours)
        except Exception as exc:
            lots = {}
            bilan["echecs"].append(f"yahoo groupé: {str(exc)[:60]}")
        for sym, df in lots.items():
            n = ecrire_partition(sym, interval, df)
            if n:
                bilan["titres"] += 1
                bilan["barres"] += n
        for sym in yahoos:
            if sym not in lots:
                bilan["echecs"].append(sym)

    for sym in binances:
        try:
            df = binance.get_ohlcv(sym, interval=interval, lookback_days=jours)
            n = ecrire_partition(sym, interval, df)
            if n:
                bilan["titres"] += 1
                bilan["barres"] += n
        except Exception:
            bilan["echecs"].append(sym)

    return bilan


def capturer_pour_veille(interval: str = INTERVALLE_DEFAUT) -> dict:
    """Capture appelée à chaque balayage de la veille, sur tout le périmètre.

    Le périmètre vient de `config.SUIVIS` et de nulle part ailleurs : recréer
    une liste locale d'actifs est précisément la dérive corrigée en phase 22.
    """
    return capturer(list(config.SUIVIS), interval=interval, jours=JOURS_VEILLE)


# ---------------------------------------------------------------------------
# Volatilité réalisée
# ---------------------------------------------------------------------------

def volatilite_realisee(df: pd.DataFrame) -> pd.DataFrame:
    """Variance réalisée par journée, à partir de barres intrajournalières.

    RV = somme des carrés des rendements logarithmiques INTRA-séance. Les
    rendements sont calculés à l'intérieur de chaque journée, ce qui exclut
    mécaniquement le saut de cotation d'une clôture à l'ouverture suivante —
    c'est la définition usuelle, et l'inclure ferait passer une nuit calme
    pour un épisode de volatilité.

    Colonnes : date, rv, n_barres, vol_annualisee_%.
    """
    vide = pd.DataFrame(columns=["date", "rv", "n_barres", "vol_annualisee_%"])
    if df is None or df.empty or "close" not in df.columns:
        return vide

    serie = pd.to_numeric(df["close"], errors="coerce").dropna()
    serie = serie[serie > 0]
    if serie.empty:
        return vide

    cadre = pd.DataFrame({"close": serie})
    cadre["jour"] = pd.DatetimeIndex(cadre.index).normalize()
    cadre["r"] = np.log(cadre["close"]).groupby(cadre["jour"]).diff()
    cadre = cadre.dropna(subset=["r"])
    if cadre.empty:
        return vide

    groupes = cadre.groupby("jour")["r"]
    out = pd.DataFrame({
        "rv": groupes.apply(lambda s: float((s ** 2).sum())),
        "n_barres": groupes.size().astype(int),
    }).reset_index()
    out["date"] = out["jour"].dt.date.astype(str)
    out["vol_annualisee_%"] = np.sqrt(out["rv"] * SEANCES_AN) * 100
    return out[["date", "rv", "n_barres", "vol_annualisee_%"]]


def journees_completes(rv: pd.DataFrame, aujourdhui: dt.date | None = None,
                       barres_min: int = BARRES_MIN,
                       fraction_min: float = FRACTION_BARRES_MIN) -> pd.DataFrame:
    """Ne garde que les séances TERMINÉES et suffisamment garnies.

    Deux filtres, pour deux pièges distincts.

    1. LA SÉANCE DU JOUR. Elle est partielle : à 10 h, elle ne contient qu'une
       poignée de barres, donc une variance faible. Enregistrée telle quelle,
       elle se lirait plus tard comme une journée exceptionnellement calme — un
       faux souvenir, injecté chaque jour dans l'historique, qui biaiserait vers
       le bas tout modèle de volatilité construit dessus. Le jour en cours n'est
       donc JAMAIS écrit dans le relevé versionné ; il reste lisible en direct
       dans le magasin de barres.

    2. LES SÉANCES TRONQUÉES. Même problème, en moins visible : ouverture du
       dimanche soir sur le forex, jour férié écourté, premier jour d'une
       fenêtre de récupération, trou du fournisseur. Le seuil est donc aussi
       RELATIF au nombre de barres médian de l'instrument (voir
       `FRACTION_BARRES_MIN`), ce qui le calibre sans réglage par actif.
       La médiane demande au moins trois séances pour valoir quelque chose ;
       en dessous, seul le plancher absolu s'applique.
    """
    if rv is None or rv.empty:
        return rv if rv is not None else pd.DataFrame()
    limite = (aujourdhui or dt.datetime.now(dt.timezone.utc).date()).isoformat()
    termine = rv[rv["date"] < limite].copy()
    if termine.empty:
        return termine
    seuil = barres_min
    if len(termine) >= 3:
        seuil = max(barres_min, fraction_min * float(termine["n_barres"].median()))
    return termine[termine["n_barres"] >= seuil].copy()


def rv_par_jour(symbole: str, interval: str = INTERVALLE_DEFAUT,
                jours: int = JOURS_RELEVE, depuis_magasin: bool = False) -> pd.DataFrame:
    """Volatilité réalisée d'un titre sur les `jours` derniers jours.

    `depuis_magasin=True` relit les barres déjà archivées au lieu d'interroger
    le fournisseur — utile pour recalculer sans charge réseau.
    """
    if depuis_magasin:
        df = lire(symbole, interval)
    else:
        yahoos, binances, ecartes = _router([symbole])
        if ecartes:
            return pd.DataFrame(columns=["date", "rv", "n_barres", "vol_annualisee_%"])
        if binances:
            df = binance.get_ohlcv(symbole, interval=interval, lookback_days=jours)
        else:
            lots = yahoo.get_ohlcv_multi([symbole], interval=interval, lookback_days=jours)
            df = lots.get(symbole)
        if df is not None and not df.empty:
            ecrire_partition(symbole, interval, df)
    return volatilite_realisee(df)


def charger_releve() -> pd.DataFrame:
    """Relève versionné de la volatilité réalisée (vide si absent)."""
    if not config.RV_PATH.exists():
        return pd.DataFrame(columns=COLONNES_RV)
    try:
        df = pd.read_csv(config.RV_PATH)
    except Exception:
        return pd.DataFrame(columns=COLONNES_RV)
    for col in COLONNES_RV:
        if col not in df.columns:
            df[col] = np.nan
    return df[COLONNES_RV]


def fusionner_releve(ancien: pd.DataFrame, nouveau: pd.DataFrame,
                     recalculer: bool = False) -> pd.DataFrame:
    """Fusionne un relevé existant et des mesures fraîches.

    Par défaut l'ANCIEN gagne : une journée déjà relevée n'est pas réécrite.
    Deux raisons — l'historique reste stable (un même jour ne change pas de
    valeur au fil des semaines), et le fichier ne bouge que quand une VRAIE
    journée s'ajoute, donc le workflow ne produit pas un commit par passage.
    `recalculer=True` inverse la priorité, pour une correction assumée.
    """
    cadres = [c for c in (ancien, nouveau) if c is not None and not c.empty]
    if not cadres:
        return pd.DataFrame(columns=COLONNES_RV)
    if recalculer:
        cadres = list(reversed(cadres))
    fusion = pd.concat(cadres, ignore_index=True)[COLONNES_RV]
    fusion = fusion.drop_duplicates(subset=["date", "symbole", "interval"], keep="first")
    return fusion.sort_values(["date", "symbole"]).reset_index(drop=True)


def mettre_a_jour_releve(symboles: list[str] | None = None,
                         interval: str = INTERVALLE_DEFAUT,
                         jours: int = JOURS_RELEVE,
                         recalculer: bool = False,
                         ecrire: bool = True) -> dict:
    """Relevé quotidien : mesure les séances terminées et les conserve.

    Appelé une fois par jour depuis la publication. Ne lève jamais : un titre
    indisponible chez le fournisseur ne doit pas priver les 58 autres de leur
    mesure.
    """
    symboles = list(symboles or config.SUIVIS)
    lignes = []
    echecs = []
    for sym in symboles:
        try:
            rv = journees_completes(rv_par_jour(sym, interval=interval, jours=jours))
        except Exception as exc:
            echecs.append(f"{sym}: {str(exc)[:50]}")
            continue
        if rv is None or rv.empty:
            continue
        rv = rv.copy()
        rv["symbole"] = sym
        rv["interval"] = interval
        lignes.append(rv[COLONNES_RV])

    nouveau = (pd.concat(lignes, ignore_index=True) if lignes
               else pd.DataFrame(columns=COLONNES_RV))
    ancien = charger_releve()
    fusion = fusionner_releve(ancien, nouveau, recalculer=recalculer)

    if ecrire:
        config.RV_PATH.parent.mkdir(parents=True, exist_ok=True)
        # float_format borné : sans lui, le moindre bruit de représentation
        # ferait apparaître le fichier comme modifié à chaque passage.
        fusion.to_csv(config.RV_PATH, index=False, float_format="%.10g")

    return {
        "titres": int(nouveau["symbole"].nunique()) if not nouveau.empty else 0,
        "mesures": len(nouveau),
        "ajoutees": len(fusion) - len(ancien),
        "total": len(fusion),
        "echecs": echecs,
    }
