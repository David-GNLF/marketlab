"""Saisonnalité : effets de calendrier, mesurés ET testés.

Attention, ce domaine est le terrain de jeu favori du sur-apprentissage. En
testant 12 mois, on trouve presque toujours un mois « significatif » par pur
hasard : au seuil de 5 %, l'espérance est de 0,6 faux positif par titre. Ce
module accompagne donc systématiquement chaque effet de trois garde-fous :

1. **Test de Student** sur la moyenne des rendements (p-value brute).
2. **Correction de Bonferroni** pour la multiplicité des tests : la p-value
   est multipliée par le nombre d'effets testés simultanément. C'est
   conservateur, et c'est voulu.
3. **Stabilité temporelle** : l'effet se retrouve-t-il sur la première ET la
   seconde moitié de l'historique ? Un effet vrai persiste ; un artefact
   disparaît.

Un effet n'est retenu que s'il franchit les trois. Dans la pratique, très peu
survivent — et c'est l'information utile.
"""

import calendar

import numpy as np
import pandas as pd

from marketlab.data import get_ohlcv

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]


def _rendements(symbole: str, lookback_days: int = 7300) -> pd.Series:
    """Rendements quotidiens sur l'historique le plus long disponible.

    Yahoo refuse parfois temporairement les requêtes profondes (« possibly
    delisted » alors que le titre est bien coté) : on retente avec des
    profondeurs décroissantes plutôt que d'échouer.
    """
    profondeurs = [p for p in (lookback_days, 5475, 3650, 1825)
                   if p <= lookback_days]
    derniere_erreur = None
    for p in profondeurs:
        try:
            df = get_ohlcv(symbole, lookback_days=p)
        except Exception as exc:
            derniere_erreur = exc
            continue
        r = df["close"].pct_change().dropna()
        if len(r) >= 500:
            return r
        derniere_erreur = RuntimeError(f"seulement {len(r)} séances")
    raise RuntimeError(f"Historique insuffisant pour la saisonnalité "
                       f"({derniere_erreur})")


def _test_student(echantillon: np.ndarray, n_tests: int = 1) -> dict:
    """t-stat, p-value brute et p-value corrigée (Bonferroni)."""
    from scipy import stats
    if len(echantillon) < 8 or np.std(echantillon) == 0:
        return {"t": None, "p": None, "p_corrigee": None, "significatif": False}
    t, p = stats.ttest_1samp(echantillon, 0.0)
    p_corr = min(1.0, float(p) * n_tests)
    return {"t": round(float(t), 2), "p": round(float(p), 4),
            "p_corrigee": round(p_corr, 4), "significatif": bool(p_corr < 0.05)}


def _stabilite(serie: pd.Series, masque, n_tests: int) -> dict:
    """L'effet tient-il sur les deux moitiés de l'historique ?"""
    milieu = serie.index[len(serie) // 2]
    m1 = serie[(serie.index < milieu) & masque(serie.index)]
    m2 = serie[(serie.index >= milieu) & masque(serie.index)]
    if len(m1) < 8 or len(m2) < 8:
        return {"stable": None, "moitie_1_%": None, "moitie_2_%": None}
    moy1, moy2 = float(m1.mean()) * 100, float(m2.mean()) * 100
    return {
        "stable": bool(np.sign(moy1) == np.sign(moy2)
                       and min(abs(moy1), abs(moy2)) > abs(max(moy1, moy2)) * 0.25),
        "moitie_1_%": round(moy1, 3),
        "moitie_2_%": round(moy2, 3),
    }


# --- Effets ------------------------------------------------------------------

def par_mois(symbole: str, lookback_days: int = 7300) -> pd.DataFrame:
    """Rendement mensuel moyen, avec tests de significativité et stabilité."""
    r = _rendements(symbole, lookback_days)
    # rendements mensuels composés (et non moyenne de quotidiens)
    mensuels = (1 + r).resample("ME").prod() - 1
    mensuels = mensuels.iloc[:-1]  # le mois courant est incomplet

    lignes = []
    for m in range(1, 13):
        ech = mensuels[mensuels.index.month == m]
        if len(ech) == 0:
            continue
        test = _test_student(ech.to_numpy(), n_tests=12)
        stab = _stabilite(mensuels, lambda idx, mm=m: idx.month == mm, 12)
        lignes.append({
            "mois": MOIS_FR[m - 1],
            "n_annees": len(ech),
            "rendement_moyen_%": round(float(ech.mean()) * 100, 2),
            "rendement_median_%": round(float(ech.median()) * 100, 2),
            "part_positifs_%": round(float((ech > 0).mean()) * 100, 1),
            "t": test["t"], "p_corrigee": test["p_corrigee"],
            "significatif": test["significatif"],
            "stable": stab["stable"],
            "retenu": bool(test["significatif"] and stab["stable"]),
        })
    return pd.DataFrame(lignes)


def par_jour_semaine(symbole: str, lookback_days: int = 7300) -> pd.DataFrame:
    """Effet jour de la semaine (lundi…vendredi)."""
    r = _rendements(symbole, lookback_days)
    lignes = []
    for j in range(5):
        ech = r[r.index.dayofweek == j]
        if len(ech) == 0:
            continue
        test = _test_student(ech.to_numpy(), n_tests=5)
        stab = _stabilite(r, lambda idx, jj=j: idx.dayofweek == jj, 5)
        lignes.append({
            "jour": JOURS_FR[j],
            "n_seances": len(ech),
            "rendement_moyen_%": round(float(ech.mean()) * 100, 3),
            "part_positifs_%": round(float((ech > 0).mean()) * 100, 1),
            "t": test["t"], "p_corrigee": test["p_corrigee"],
            "significatif": test["significatif"],
            "stable": stab["stable"],
            "retenu": bool(test["significatif"] and stab["stable"]),
        })
    return pd.DataFrame(lignes)


def par_periode_du_mois(symbole: str, lookback_days: int = 7300) -> pd.DataFrame:
    """Début / milieu / fin de mois — les flux de fonds suivent le calendrier."""
    r = _rendements(symbole, lookback_days)
    jours = r.index.day
    fins = np.array([calendar.monthrange(d.year, d.month)[1] for d in r.index])
    tranches = {
        "début (1-5)": jours <= 5,
        "milieu (6-20)": (jours > 5) & (jours <= 20),
        "fin (21-fin)": jours > 20,
        "3 derniers jours": jours > (fins - 3),
    }
    lignes = []
    for nom, masque in tranches.items():
        ech = r[masque]
        if len(ech) < 30:
            continue
        test = _test_student(ech.to_numpy(), n_tests=len(tranches))
        lignes.append({
            "periode": nom, "n_seances": len(ech),
            "rendement_moyen_%": round(float(ech.mean()) * 100, 3),
            "part_positifs_%": round(float((ech > 0).mean()) * 100, 1),
            "t": test["t"], "p_corrigee": test["p_corrigee"],
            "significatif": test["significatif"],
        })
    return pd.DataFrame(lignes)


def halloween(symbole: str, lookback_days: int = 7300) -> dict:
    """« Sell in May » : novembre-avril contre mai-octobre.

    L'effet le plus documenté de la littérature — et l'un des rares à avoir
    survécu à sa publication sur de nombreux marchés.
    """
    r = _rendements(symbole, lookback_days)
    hiver = r[r.index.month.isin([11, 12, 1, 2, 3, 4])]
    ete = r[r.index.month.isin([5, 6, 7, 8, 9, 10])]
    if len(hiver) < 100 or len(ete) < 100:
        raise RuntimeError("Historique insuffisant pour l'effet Halloween")

    from scipy import stats
    t, p = stats.ttest_ind(hiver.to_numpy(), ete.to_numpy(), equal_var=False)
    # annualisation approximative : ~126 séances par semestre
    rend_hiver = float((1 + hiver.mean()) ** 126 - 1) * 100
    rend_ete = float((1 + ete.mean()) ** 126 - 1) * 100
    return {
        "symbole": symbole,
        "novembre_avril_%": round(rend_hiver, 2),
        "mai_octobre_%": round(rend_ete, 2),
        "ecart_points": round(rend_hiver - rend_ete, 2),
        "t": round(float(t), 2), "p": round(float(p), 4),
        "significatif": bool(p < 0.05),
        "n_seances": {"hiver": len(hiver), "ete": len(ete)},
        "lecture": ("Écart conforme à l'effet Halloween (semestre d'hiver plus "
                    "porteur)." if rend_hiver > rend_ete and p < 0.05 else
                    "Écart non significatif : pas d'effet Halloween exploitable "
                    "sur ce titre."),
    }


def courbe_annuelle(symbole: str, lookback_days: int = 7300) -> dict:
    """Trajectoire moyenne du rendement cumulé au fil de l'année civile."""
    r = _rendements(symbole, lookback_days)
    cadre = pd.DataFrame({"r": r, "annee": r.index.year, "jour": r.index.dayofyear})
    complet = cadre.groupby("annee")["r"].count()
    annees_valides = complet[complet > 200].index
    cadre = cadre[cadre["annee"].isin(annees_valides)]
    if cadre.empty:
        raise RuntimeError("Aucune année complète exploitable")

    cadre["cumul"] = cadre.groupby("annee")["r"].transform(
        lambda x: (1 + x).cumprod() - 1)
    moyenne = cadre.groupby("jour")["cumul"].mean() * 100
    moyenne = moyenne.reindex(range(1, 367)).interpolate().ffill().bfill()
    return {
        "jours": moyenne.index.tolist(),
        "cumul_moyen_%": np.round(moyenne.to_numpy(), 3).tolist(),
        "n_annees": int(len(annees_valides)),
    }


# --- Synthèse ---------------------------------------------------------------

def analyser(symbole: str, lookback_days: int = 7300) -> dict:
    """Synthèse de tous les effets, avec la liste de ceux qui survivent."""
    resultat = {"symbole": symbole}
    retenus = []

    try:
        mois = par_mois(symbole, lookback_days)
        resultat["par_mois"] = mois.to_dict(orient="records")
        resultat["n_annees"] = int(mois["n_annees"].max()) if len(mois) else 0
        for _, l in mois[mois["retenu"]].iterrows():
            retenus.append(f"{l['mois']} ({l['rendement_moyen_%']:+.2f} % en moyenne)")
    except RuntimeError as exc:
        resultat["par_mois"] = {"erreur": str(exc)}

    for cle, fonction in (("par_jour_semaine", par_jour_semaine),
                          ("par_periode_du_mois", par_periode_du_mois)):
        try:
            table = fonction(symbole, lookback_days)
            resultat[cle] = table.to_dict(orient="records")
            if "retenu" in table.columns:
                for _, l in table[table["retenu"]].iterrows():
                    retenus.append(f"{l['jour']} ({l['rendement_moyen_%']:+.3f} %)")
        except RuntimeError as exc:
            resultat[cle] = {"erreur": str(exc)}

    try:
        resultat["halloween"] = halloween(symbole, lookback_days)
        if resultat["halloween"]["significatif"]:
            retenus.append("effet Halloween (novembre-avril)")
    except RuntimeError as exc:
        resultat["halloween"] = {"erreur": str(exc)}

    resultat["effets_retenus"] = retenus
    resultat["conclusion"] = (
        "Aucun effet saisonnier ne survit aux tests de significativité et de "
        "stabilité : ne pas fonder de décision sur le calendrier pour ce titre."
        if not retenus else
        "Effets survivant aux trois garde-fous : " + " · ".join(retenus)
        + ". À traiter comme un léger biais de contexte, jamais comme un signal "
          "à lui seul.")
    return resultat
