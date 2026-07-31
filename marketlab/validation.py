"""Mesurer un pouvoir prédictif sans se mentir sur le recouvrement.

LE PROBLÈME. Les verdicts sont rendus presque chaque jour, mais jugés sur
20 séances. Deux verdicts espacés d'un jour décrivent donc quasiment le même
bout de marché : les traiter comme deux observations indépendantes multiplie
artificiellement la certitude. Sur ce projet, 3 232 verdicts en deux ans ne
pèsent en réalité qu'une vingtaine d'épisodes distincts.

DEUX FAÇONS DE TRAITER ÇA, ET POURQUOI ON CHANGE.

La première — celle en place — garde toutes les dates et CORRIGE l'écart-type
(Newey-West). Elle utilise toute l'information, mais repose sur une
approximation qui suppose beaucoup d'observations. Sur échantillon court elle
se dégrade, et elle peut même annuler la variance : mesuré à t = −288 819 pour
un IC de −0,05.

La seconde — celle-ci — ÉLIMINE le recouvrement au lieu de le corriger : on ne
retient que des dates espacées d'au moins l'horizon, plus une marge. Les
observations retenues sont alors réellement indépendantes, et un test de
Student ordinaire redevient valide, sans correction ni approximation.

CE QU'ON PERD, ET COMMENT ON LE RÉCUPÈRE. Espacer de 20 séances ne laisse
qu'un vingtième des dates. Mais le point de départ est arbitraire : commencer
au premier jour, au deuxième, au troisième… donne autant d'échantillonnages
DIFFÉRENTS et chacun propre. On les parcourt tous, et l'on regarde combien
concluent dans le même sens.

C'EST LA MESURE LA PLUS UTILE DU LOT. Un résultat porté par une seule période
agitée apparaîtra significatif sur quelques départs et pas sur les autres.
« 3 échantillonnages sur 20 le confirment » est une phrase qu'aucun t unique ne
sait dire — et c'est exactement ce qu'il faut savoir avant d'agir.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Marge ajoutée à l'espacement, en séances. L'horizon seul suffirait en
# théorie ; en pratique la volatilité et les flux gardent une mémoire un peu
# plus longue que la fenêtre de mesure, et deux observations tout juste
# disjointes restent apparentées.
EMBARGO = 3

# En dessous, un test de Student n'a plus de sens : la moyenne d'une poignée de
# points ne se distingue de zéro que par accident.
OBS_MIN = 8


def dates_espacees(dates: list, ecart: int, depart: int = 0) -> list:
    """Dates retenues en partant de `depart`, espacées d'au moins `ecart`.

    Les dates sont prises dans l'ordre, et l'on saute tout ce qui tombe dans la
    fenêtre de la précédente : c'est la purge, appliquée aux observations
    plutôt qu'à un jeu d'entraînement — ici il n'y a pas de modèle à ajuster,
    seulement une corrélation à estimer.

    L'écart est compté en SÉANCES, pas en jours calendaires. La nuance
    décide de la validité de toute la mesure : avec un horizon de 20 et un
    embargo de 3, l'ancienne comparaison en jours calendaires laissait
    passer deux observations distantes de 23 jours, soit environ 16
    séances — donc encore quatre séances de recouvrement, exactement ce que
    cette fonction existe pour éliminer. Le veto de régime, seul mécanisme
    qui modifie aujourd'hui les avis, reposait sur cette ligne.

    `busday_count` compte les jours ouvrés et ignore les jours fériés : il
    surestime donc légèrement le nombre de séances autour d'un férié. Le
    biais résiduel est de l'ordre d'une séance sur vingt, contre quatre
    avant correction, et il va dans le sens prudent une fois l'embargo
    ajouté.
    """
    ordonnees = sorted(dates)
    if not ordonnees or ecart < 1 or depart >= len(ordonnees):
        return []
    gardees = [ordonnees[depart]]
    for d in ordonnees[depart + 1:]:
        seances = int(np.busday_count(
            pd.Timestamp(gardees[-1]).date(), pd.Timestamp(d).date()))
        if seances >= ecart:
            gardees.append(d)
    return gardees


def _ic_par_date(df: pd.DataFrame, colonne_note: str,
                 colonne_rendement: str, actifs_min: int = 8) -> pd.Series:
    """IC de Spearman transversal, une valeur par date."""
    valides = df.dropna(subset=[colonne_note, colonne_rendement])
    ics = {}
    for date, groupe in valides.groupby("date"):
        if len(groupe) < actifs_min:
            continue
        ic = groupe[colonne_note].rank().corr(groupe[colonne_rendement].rank())
        if ic == ic:
            ics[date] = float(ic)
    return pd.Series(ics, dtype=float).sort_index()


def ic_purge(df: pd.DataFrame, colonne_note: str = "note",
             colonne_rendement: str = "rendement_relatif_%",
             horizon: int = 20, embargo: int = EMBARGO) -> dict:
    """IC mesuré sur des observations SANS recouvrement, tous départs confondus.

    Renvoie la moyenne des estimations, et surtout la part des échantillonnages
    qui concluent. Un résultat solide est confirmé par la plupart des départs ;
    un résultat porté par un seul épisode ne l'est que par quelques-uns.
    """
    serie = _ic_par_date(df, colonne_note, colonne_rendement)
    if len(serie) < OBS_MIN:
        return {"mesurable": False,
                "raison": f"seulement {len(serie)} date(s) exploitable(s)"}

    ecart = max(int(horizon) + int(embargo), 1)
    dates = list(serie.index)
    # Autant de départs que l'espacement le permet : au-delà, on retomberait
    # sur des échantillonnages déjà parcourus.
    n_departs = min(ecart, len(dates))

    estimations, ts, tailles = [], [], []
    for depart in range(n_departs):
        retenues = dates_espacees(dates, ecart, depart)
        if len(retenues) < OBS_MIN:
            continue
        valeurs = serie.loc[retenues].to_numpy()
        moyenne = float(valeurs.mean())
        err = float(valeurs.std(ddof=1)) / math.sqrt(len(valeurs))
        estimations.append(moyenne)
        ts.append(moyenne / err if err > 0 else 0.0)
        tailles.append(len(valeurs))

    if not estimations:
        return {"mesurable": False,
                "raison": (f"aucun échantillonnage sans recouvrement n'atteint "
                           f"{OBS_MIN} observations (horizon {horizon})")}

    est = np.array(estimations)
    t = np.array(ts)
    concluants = int((np.abs(t) > 1.96).sum())
    meme_sens = int((np.sign(t) == np.sign(np.median(t))).sum())

    return {
        "mesurable": True,
        "horizon": horizon,
        "embargo": embargo,
        "n_echantillonnages": len(estimations),
        "obs_par_echantillonnage": int(np.median(tailles)),
        "ic_moyen": round(float(est.mean()), 4),
        "ic_min": round(float(est.min()), 4),
        "ic_max": round(float(est.max()), 4),
        "t_median": round(float(np.median(t)), 2),
        "part_concluante_%": round(concluants / len(t) * 100, 1),
        "part_meme_sens_%": round(meme_sens / len(t) * 100, 1),
        "verdict": _verdict(est, t, concluants, len(t)),
        "lecture": _lecture(est, t, concluants, len(t), int(np.median(tailles))),
    }


def _verdict(est, t, concluants: int, total: int) -> str:
    """Trois issues seulement — et « fragile » n'est pas « démontré »."""
    if concluants == 0:
        return "rien de démontré"
    part = concluants / total
    coherent = (np.sign(t) == np.sign(np.median(t))).mean()
    if part >= 0.6 and coherent >= 0.9:
        return "effet inversé confirmé" if est.mean() < 0 else "effet confirmé"
    return "fragile"


def _lecture(est, t, concluants: int, total: int, obs: int) -> str:
    sens = "inversé" if est.mean() < 0 else "positif"
    base = (f"{concluants} échantillonnage(s) sur {total} concluent, avec "
            f"environ {obs} observations réellement indépendantes chacun. "
            f"IC moyen {est.mean():+.3f} (de {est.min():+.3f} à {est.max():+.3f}).")
    if concluants == 0:
        return (base + " Aucun ne conclut : une fois le recouvrement éliminé, "
                "il ne reste pas de quoi affirmer un pouvoir de classement, "
                "dans un sens ou dans l'autre.")
    if concluants / total >= 0.6:
        return (base + f" La majorité converge : l'effet {sens} tient sans "
                f"dépendre du point de départ choisi.")
    return (base + f" Seule une minorité conclut : le signe {sens} revient "
            f"souvent, mais l'effet dépend trop du découpage pour qu'on s'y "
            f"fie. C'est la signature d'un résultat porté par un petit nombre "
            f"d'épisodes.")


def comparer_methodes(df: pd.DataFrame, mesure_corrigee: dict,
                      horizon: int = 20, **kwargs) -> dict:
    """Met côte à côte l'écart-type corrigé et l'échantillonnage purgé.

    Les deux répondent à la même question par des chemins opposés. Quand ils
    divergent, c'est presque toujours que la correction a été trop généreuse :
    l'écart lui-même est l'information.
    """
    purge = ic_purge(df, horizon=horizon, **kwargs)
    if not purge.get("mesurable") or not isinstance(mesure_corrigee, dict):
        return {"purge": purge, "accord": None}
    sens_corrige = mesure_corrigee.get("sens")
    conclut_corrige = sens_corrige in {"positif", "négatif", "negatif"}
    conclut_purge = purge["verdict"] not in {"rien de démontré", "fragile"}
    return {
        "purge": purge,
        "corrigee": {"ic": mesure_corrigee.get("ic_transversal_moyen"),
                     "t": mesure_corrigee.get("t"), "sens": sens_corrige},
        "accord": conclut_corrige == conclut_purge,
        "lecture": ("Les deux méthodes s'accordent."
                    if conclut_corrige == conclut_purge else
                    "Les deux méthodes DIVERGENT : l'écart-type corrigé conclut "
                    "là où l'échantillonnage sans recouvrement ne conclut pas. "
                    "C'est le signe que la correction a surestimé la certitude — "
                    "retenir la lecture la plus prudente."),
    }
