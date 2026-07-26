"""Corrélations et risque de portefeuille.

Deux titres qui montent et descendent ensemble ne diversifient rien : on croit
détenir deux positions, on en détient une en double. Ce module mesure ça.

Points clés :
- Les corrélations ne sont pas stables : elles **augmentent en période de
  stress**, précisément quand la diversification serait utile. D'où
  `correlation_glissante()` et la comparaison calme / agité.
- Le risque d'un portefeuille n'est PAS la somme des risques : il dépend des
  corrélations. Le « bénéfice de diversification » chiffre l'écart.
- La contribution au risque révèle qu'une petite ligne très volatile ou très
  corrélée peut peser bien plus que son poids en capital.

Note : les rendements sont calculés en devise locale. Pour un portefeuille
mixte USD/EUR, l'effet de change n'est pas isolé ici.
"""

import numpy as np
import pandas as pd

from marketlab import config
from marketlab.data import get_ohlcv

PERIODES_AN = 252


def rendements(symboles: list[str], jours: int = 500) -> pd.DataFrame:
    """Rendements quotidiens alignés sur les dates communes.

    L'alignement est indispensable : crypto cote 7j/7, les actions non — sans
    intersection des dates, les corrélations seraient fausses.
    """
    series = {}
    erreurs = {}
    for s in symboles:
        try:
            df = get_ohlcv(s, lookback_days=int(jours * 1.6) + 200)
            series[s] = df["close"].pct_change()
        except Exception as exc:
            erreurs[s] = str(exc)[:60]
    if len(series) < 2:
        raise RuntimeError(f"Au moins 2 titres exploitables requis. Échecs : {erreurs}")
    out = pd.DataFrame(series).dropna().tail(jours)
    if len(out) < 60:
        raise RuntimeError(f"Trop peu de dates communes ({len(out)}) — "
                           "mélanger crypto et actions réduit l'intersection")
    out.attrs["erreurs"] = erreurs
    return out


def matrice(symboles: list[str], jours: int = 500) -> pd.DataFrame:
    return rendements(symboles, jours).corr().round(3)


def paires_extremes(symboles: list[str], jours: int = 500,
                    n: int = 5) -> dict:
    """Paires les plus et les moins corrélées — les premières font doublon."""
    m = matrice(symboles, jours)
    paires = []
    cols = list(m.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            paires.append({"a": a, "b": b, "correlation": float(m.loc[a, b])})
    paires.sort(key=lambda p: p["correlation"], reverse=True)
    return {"plus_correlees": paires[:n], "moins_correlees": paires[-n:][::-1]}


def correlation_glissante(a: str, b: str, fenetre: int = 60,
                          jours: int = 750) -> pd.Series:
    """Corrélation glissante entre deux titres : montre son instabilité."""
    r = rendements([a, b], jours)
    return r[a].rolling(fenetre).corr(r[b]).dropna().round(3)


def correlation_par_regime(symboles: list[str], jours: int = 750) -> dict:
    """Corrélation moyenne en marché calme vs agité.

    Les jours sont classés par la volatilité du panier (tiers extrêmes).
    """
    r = rendements(symboles, jours)
    vol_panier = r.mean(axis=1).rolling(20).std()
    valides = vol_panier.dropna()
    seuil_bas, seuil_haut = valides.quantile(0.33), valides.quantile(0.67)

    def _moyenne(sous_ensemble: pd.DataFrame) -> float | None:
        if len(sous_ensemble) < 30:
            return None
        c = sous_ensemble.corr().to_numpy()
        haut = c[np.triu_indices_from(c, k=1)]
        return round(float(np.nanmean(haut)), 3)

    calme = _moyenne(r.loc[valides[valides <= seuil_bas].index])
    agite = _moyenne(r.loc[valides[valides >= seuil_haut].index])
    ecart = (round(agite - calme, 3)
             if calme is not None and agite is not None else None)
    return {
        "correlation_moyenne_calme": calme,
        "correlation_moyenne_agite": agite,
        "hausse_en_stress": ecart,
        "lecture": ("La diversification se dégrade quand le marché se tend : "
                    "prévoir une marge de sécurité." if ecart and ecart > 0.05
                    else "Corrélations relativement stables entre régimes."),
    }


def beta(symbole: str, reference: str = "^GSPC", jours: int = 500) -> dict:
    """Sensibilité au marché de référence (bêta) et part expliquée (R²)."""
    r = rendements([symbole, reference], jours)
    cov = float(np.cov(r[symbole], r[reference])[0, 1])
    var_ref = float(np.var(r[reference]))
    b = cov / var_ref if var_ref > 0 else float("nan")
    corr = float(r[symbole].corr(r[reference]))
    return {
        "symbole": symbole, "reference": reference,
        "beta": round(b, 3), "correlation": round(corr, 3),
        "r2": round(corr ** 2, 3), "n_jours": len(r),
        "lecture": ("amplifie les mouvements du marché" if b > 1.2 else
                    "amortit les mouvements du marché" if b < 0.8 else
                    "suit le marché"),
    }


# --- Portefeuille -----------------------------------------------------------

def analyser_portefeuille(poids: dict[str, float], jours: int = 500) -> dict:
    """Risque, diversification et concentration d'un portefeuille pondéré.

    `poids` : {symbole: montant ou pourcentage} — normalisé automatiquement.
    """
    poids = {s: float(v) for s, v in poids.items() if v and float(v) > 0}
    if len(poids) < 2:
        raise RuntimeError("Au moins 2 positions requises pour l'analyse")

    r = rendements(list(poids), jours)
    presents = [s for s in poids if s in r.columns]
    w = np.array([poids[s] for s in presents], dtype=float)
    w /= w.sum()

    cov = r[presents].cov().to_numpy() * PERIODES_AN
    vol_pf = float(np.sqrt(w @ cov @ w))
    vols = np.sqrt(np.diag(cov))
    vol_somme_ponderee = float(w @ vols)
    benefice = 1 - vol_pf / vol_somme_ponderee if vol_somme_ponderee > 0 else 0.0

    # contribution de chaque ligne au risque total (décomposition d'Euler)
    contrib_marg = cov @ w
    contrib = w * contrib_marg / (vol_pf ** 2) if vol_pf > 0 else w * 0

    hhi = float((w ** 2).sum())  # 1 = tout sur une ligne, 1/n = équipondéré
    corr = r[presents].corr().to_numpy()
    corr_moy = float(np.nanmean(corr[np.triu_indices_from(corr, k=1)]))

    lignes = [{
        "symbole": s,
        "poids_%": round(w[i] * 100, 1),
        "vol_annuelle_%": round(vols[i] * 100, 1),
        "contribution_risque_%": round(contrib[i] * 100, 1),
        "sur_representation": round(contrib[i] / w[i], 2) if w[i] > 0 else None,
    } for i, s in enumerate(presents)]
    lignes.sort(key=lambda x: -x["contribution_risque_%"])

    return {
        "n_positions": len(presents),
        "vol_portefeuille_%": round(vol_pf * 100, 1),
        "vol_sans_diversification_%": round(vol_somme_ponderee * 100, 1),
        "benefice_diversification_%": round(benefice * 100, 1),
        "correlation_moyenne": round(corr_moy, 3),
        "concentration_hhi": round(hhi, 3),
        "equivalent_positions": round(1 / hhi, 1) if hhi > 0 else None,
        "lignes": lignes,
        "lecture": _lecture_portefeuille(corr_moy, benefice, hhi, len(presents)),
    }


def _lecture_portefeuille(corr_moy: float, benefice: float, hhi: float,
                          n: int) -> str:
    messages = []
    if corr_moy > 0.7:
        messages.append(f"Corrélation moyenne très élevée ({corr_moy:.2f}) : "
                        "les positions bougent presque ensemble, la "
                        "diversification est largement illusoire.")
    elif corr_moy > 0.5:
        messages.append(f"Corrélation moyenne élevée ({corr_moy:.2f}) : "
                        "diversification partielle seulement.")
    else:
        messages.append(f"Corrélation moyenne modérée ({corr_moy:.2f}) : "
                        "les positions se compensent correctement.")
    if benefice < 0.10:
        messages.append("Le portefeuille n'est guère moins risqué qu'une "
                        "position unique.")
    equivalent = 1 / hhi if hhi > 0 else n
    if equivalent < n * 0.6:
        messages.append(f"Concentration marquée : {n} lignes mais l'équivalent "
                        f"de {equivalent:.1f} positions réellement indépendantes "
                        "en capital.")
    return " ".join(messages)


def analyser_paper() -> dict:
    """Analyse le portefeuille papier en cours (valorisé au prix de revient)."""
    from marketlab import paper
    pf = paper.load()
    poids = {s: p["qty"] * p["prix_moyen"] for s, p in pf["positions"].items()}
    if len(poids) < 2:
        raise RuntimeError("Le portefeuille papier compte moins de 2 positions")
    resultat = analyser_portefeuille(poids)
    resultat["source"] = "portefeuille papier"
    return resultat


def suggerer_diversification(detenus: list[str], univers: list[str] | None = None,
                             n: int = 5, jours: int = 500) -> list[dict]:
    """Candidats les moins corrélés à ce qui est déjà détenu."""
    if not detenus:
        raise RuntimeError("Aucune position détenue")
    univers = univers or [s for u in ("Actions US", "Actions EU", "Indices", "Crypto")
                          for s in config.UNIVERS[u]]
    candidats = [s for s in univers if s not in detenus]
    r = rendements(detenus + candidats, jours)
    dispo_detenus = [s for s in detenus if s in r.columns]
    if not dispo_detenus:
        raise RuntimeError("Aucun titre détenu exploitable")

    resultats = []
    for c in candidats:
        if c not in r.columns:
            continue
        correlations = [float(r[c].corr(r[d])) for d in dispo_detenus]
        resultats.append({
            "symbole": c,
            "correlation_moyenne": round(float(np.mean(correlations)), 3),
            "correlation_max": round(float(np.max(correlations)), 3),
            "vol_annuelle_%": round(float(r[c].std() * np.sqrt(PERIODES_AN) * 100), 1),
        })
    resultats.sort(key=lambda x: x["correlation_moyenne"])
    return resultats[:n]
