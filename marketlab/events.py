"""Étude d'événements autour des publications de résultats.

Les résultats trimestriels concentrent l'essentiel des mouvements brutaux d'une
action : un écart de quelques pourcents en une séance y est banal. Ce module
mesure, sur l'historique propre au titre :

1. **La réaction immédiate** — l'écart du jour de publication, en valeur
   absolue (l'amplitude est bien plus stable que le sens).
2. **La dérive post-annonce** (*post-earnings announcement drift*) — l'une des
   anomalies les mieux documentées : le cours continue souvent de dériver
   dans le sens de la surprise pendant plusieurs semaines.
3. **Le lien surprise → réaction** — une bonne surprise ne garantit pas une
   hausse : le marché sanctionne ce qui était déjà anticipé.
4. **Le risque d'événement à venir** — y a-t-il une publication dans mon
   horizon de position ? C'est la question la plus utile avant d'entrer.

Méthode : rendement anormal par modèle de marché (AR = R − α − β·R_marché),
β estimé sur une fenêtre d'estimation **antérieure** à chaque événement pour
éviter toute contamination. Repli sur le rendement excédentaire simple si
l'estimation échoue.

Source des dates : yfinance (`Ticker.earnings_dates`). Les dates passées sont
fiables ; les dates futures sont des *estimations* de Yahoo tant que
l'entreprise n'a pas confirmé — à vérifier auprès de la société.
"""

import json
import time

import numpy as np
import pandas as pd
import yfinance as yf

from marketlab import config
from marketlab.data import get_ohlcv

CACHE_TTL_H = 12
REFERENCES = {".PA": "^FCHI", ".DE": "^GDAXI", ".AS": "^STOXX50E",
              ".T": "^N225", ".HK": "^HSI", ".KS": "^KS11", ".TW": "^TWII"}
REFERENCE_DEFAUT = "^GSPC"


def a_des_resultats(symbole: str) -> bool:
    """Seules les actions publient des résultats."""
    if symbole.startswith("^") or symbole in config.BRVM:
        return False
    return not symbole.endswith(("=X", "USDT", "=F"))


def _reference(symbole: str) -> str:
    for suffixe, indice in REFERENCES.items():
        if symbole.endswith(suffixe):
            return indice
    return REFERENCE_DEFAUT


# --- Dates de publication ---------------------------------------------------

def dates_resultats(symbole: str) -> pd.DataFrame:
    """Publications connues : date, BPA attendu/publié, surprise (%).

    Index = dates (naïves), tri chronologique.
    """
    if not a_des_resultats(symbole):
        raise RuntimeError(f"{symbole} ne publie pas de résultats")

    cache = config.CACHE_DIR / f"resultats_{symbole.replace('.', '_')}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_H * 3600:
        brut = json.loads(cache.read_text(encoding="utf-8"))
        df = pd.DataFrame(brut)
        if df.empty:
            raise RuntimeError(f"Aucune date de résultats connue pour {symbole}")
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date").sort_index()

    try:
        brut_df = yf.Ticker(symbole).earnings_dates
    except Exception as exc:
        raise RuntimeError(f"Dates de résultats indisponibles : {exc}")
    if brut_df is None or brut_df.empty:
        raise RuntimeError(f"Aucune date de résultats connue pour {symbole}")

    df = brut_df.copy()
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    renommage = {"EPS Estimate": "bpa_attendu", "Reported EPS": "bpa_publie",
                 "Surprise(%)": "surprise_%"}
    df = df.rename(columns=renommage)
    for col in ("bpa_attendu", "bpa_publie", "surprise_%"):
        if col not in df.columns:
            df[col] = np.nan
    df = df[["bpa_attendu", "bpa_publie", "surprise_%"]].sort_index()

    try:
        sortie = df.reset_index()
        sortie.columns = ["date"] + list(df.columns)
        sortie["date"] = sortie["date"].dt.strftime("%Y-%m-%d %H:%M:%S")
        cache.write_text(sortie.to_json(orient="records"), encoding="utf-8")
    except Exception:
        pass
    return df


def prochaine_publication(symbole: str) -> dict | None:
    """Prochaine publication attendue, avec le délai en jours calendaires."""
    try:
        df = dates_resultats(symbole)
    except RuntimeError:
        return None
    maintenant = pd.Timestamp.now().normalize()
    futures = df[df.index.normalize() >= maintenant]
    if futures.empty:
        return None
    date = futures.index[0]
    jours = int((date.normalize() - maintenant).days)
    return {
        "date": date.strftime("%Y-%m-%d"),
        "dans_jours": jours,
        "bpa_attendu": (float(futures.iloc[0]["bpa_attendu"])
                        if pd.notna(futures.iloc[0]["bpa_attendu"]) else None),
        "estimation_yahoo": True,
    }


# --- Étude d'événements -----------------------------------------------------

def _rendements_anormaux(r_titre: pd.Series, r_marche: pd.Series,
                         positions: np.ndarray, avant: int, apres: int,
                         estimation: int = 100) -> np.ndarray | None:
    """Matrice (événements × jours relatifs) des rendements anormaux."""
    lignes = []
    for pos in positions:
        debut_est = pos - avant - estimation
        fin_est = pos - avant
        if debut_est < 0 or pos + apres >= len(r_titre):
            continue
        y = r_titre.iloc[debut_est:fin_est].to_numpy()
        x = r_marche.iloc[debut_est:fin_est].to_numpy()
        if len(y) < 40 or np.std(x) == 0:
            continue
        try:  # modèle de marché estimé HORS fenêtre d'événement
            beta, alpha = np.polyfit(x, y, 1)
        except Exception:
            beta, alpha = 1.0, 0.0
        fenetre = slice(pos - avant, pos + apres + 1)
        attendu = alpha + beta * r_marche.iloc[fenetre].to_numpy()
        lignes.append(r_titre.iloc[fenetre].to_numpy() - attendu)
    return np.array(lignes) if lignes else None


def etude(symbole: str, avant: int = 10, apres: int = 20,
          lookback_days: int = 1825) -> dict:
    """Comportement moyen du cours autour des publications passées."""
    dates = dates_resultats(symbole)
    passees = dates[dates.index.normalize() < pd.Timestamp.now().normalize()]
    if passees.empty:
        raise RuntimeError(f"Aucune publication passée exploitable pour {symbole}")

    df = get_ohlcv(symbole, lookback_days=lookback_days)
    ref = _reference(symbole)
    df_ref = get_ohlcv(ref, lookback_days=lookback_days)
    prix = pd.DataFrame({"titre": df["close"], "marche": df_ref["close"]}).dropna()
    rends = prix.pct_change().dropna()
    if len(rends) < 200:
        raise RuntimeError("Historique commun insuffisant avec l'indice de référence")

    # position de chaque annonce dans l'index des séances (la séance suivante
    # si l'annonce tombe hors séance ou après clôture)
    positions, surprises, ecarts_j0 = [], [], []
    for date, ligne in passees.iterrows():
        suivantes = rends.index[rends.index >= date.normalize()]
        if len(suivantes) == 0:
            continue
        pos = rends.index.get_loc(suivantes[0])
        if pos < 1 or pos >= len(rends) - 1:
            continue
        positions.append(pos)
        surprises.append(ligne.get("surprise_%", np.nan))
        ecarts_j0.append(float(rends["titre"].iloc[pos]) * 100)

    if not positions:
        raise RuntimeError("Aucune publication alignable sur l'historique de cours")

    ecarts = np.array(ecarts_j0)
    ar = _rendements_anormaux(rends["titre"], rends["marche"],
                              np.array(positions), avant, apres)

    resultat = {
        "symbole": symbole,
        "reference": ref,
        "n_publications": len(positions),
        "periode": [rends.index[min(positions)].strftime("%Y-%m-%d"),
                    rends.index[max(positions)].strftime("%Y-%m-%d")],
        "reaction_jour_j": {
            "amplitude_moyenne_%": round(float(np.mean(np.abs(ecarts))), 2),
            "amplitude_mediane_%": round(float(np.median(np.abs(ecarts))), 2),
            "ecart_moyen_%": round(float(np.mean(ecarts)), 2),
            "part_hausses_%": round(float((ecarts > 0).mean()) * 100, 1),
            "meilleur_%": round(float(ecarts.max()), 2),
            "pire_%": round(float(ecarts.min()), 2),
            "ecart_type_%": round(float(np.std(ecarts)), 2),
        },
    }

    if ar is not None and len(ar) >= 3:
        car = np.cumsum(ar, axis=1) * 100          # (événements × jours)
        jours_relatifs = list(range(-avant, apres + 1))
        moyenne_car = car.mean(axis=0)
        idx_j0 = avant
        resultat["rendement_anormal_cumule"] = {
            "jours": jours_relatifs,
            "moyenne_%": np.round(moyenne_car, 3).tolist(),
        }
        resultat["derive"] = {
            "avant_j0_%": round(float(moyenne_car[idx_j0 - 1]), 2),
            "apres_j0_%": round(float(moyenne_car[-1] - moyenne_car[idx_j0]), 2),
            "lecture": _lecture_derive(float(moyenne_car[-1] - moyenne_car[idx_j0])),
        }

    # lien surprise -> réaction
    s = np.array(surprises, dtype=float)
    valides = ~np.isnan(s)
    if valides.sum() >= 5:
        correlation = float(np.corrcoef(s[valides], ecarts[valides])[0, 1])
        resultat["surprise_vs_reaction"] = {
            "n": int(valides.sum()),
            "correlation": round(correlation, 3),
            "lecture": ("La réaction suit la surprise." if correlation > 0.4 else
                        "Lien faible : le marché réagit aux perspectives plus "
                        "qu'au chiffre publié." if correlation > 0 else
                        "Lien inverse ou nul : le chiffre publié n'explique pas "
                        "la réaction."),
        }
    return resultat


def _lecture_derive(derive: float) -> str:
    if derive > 1.5:
        return ("Dérive post-annonce positive : le cours a tendance à prolonger "
                "son mouvement après publication.")
    if derive < -1.5:
        return ("Dérive post-annonce négative : les gains du jour J ont tendance "
                "à s'effacer ensuite.")
    return "Pas de dérive marquée après publication."


# --- Risque d'événement à venir ---------------------------------------------

def risque_evenement(symbole: str, horizon: int = 20) -> dict:
    """Une publication tombe-t-elle dans l'horizon de la position ?

    Renvoie l'amplitude historique observée pour dimensionner le risque.
    """
    if not a_des_resultats(symbole):
        return {"symbole": symbole, "concerne": False,
                "message": "Pas de publication de résultats pour cet instrument."}

    prochaine = prochaine_publication(symbole)
    base = {"symbole": symbole, "concerne": True, "prochaine": prochaine}
    try:
        e = etude(symbole)
        amplitude = e["reaction_jour_j"]["amplitude_moyenne_%"]
        pire = e["reaction_jour_j"]["pire_%"]
        base["amplitude_historique_%"] = amplitude
        base["pire_reaction_%"] = pire
        base["n_publications"] = e["n_publications"]
    except RuntimeError:
        amplitude = pire = None

    if prochaine is None:
        base["dans_horizon"] = False
        base["message"] = "Aucune date de publication connue à venir."
        return base

    # horizon en séances -> ~1,4 jour calendaire par séance
    jours_calendaires = int(horizon * 1.4)
    dans = prochaine["dans_jours"] <= jours_calendaires
    base["dans_horizon"] = dans
    if dans:
        detail = (f" Amplitude moyenne observée : ±{amplitude} % en une séance "
                  f"(pire cas {pire} %)." if amplitude else "")
        base["message"] = (
            f"⚠️ Publication de résultats le {prochaine['date']}, soit dans "
            f"{prochaine['dans_jours']} jours — donc DANS l'horizon de la "
            f"position.{detail} Envisager de réduire la taille, d'élargir le "
            "stop, ou d'attendre la publication.")
        base["niveau"] = "élevé" if (amplitude or 0) >= 5 else "modéré"
    else:
        base["message"] = (f"Prochaine publication le {prochaine['date']} "
                           f"(dans {prochaine['dans_jours']} jours) : hors de "
                           "l'horizon de la position.")
        base["niveau"] = "faible"
    return base


def prochaines_publications(symboles: list[str], jours: int = 30) -> pd.DataFrame:
    """Calendrier des publications à venir pour une liste de titres."""
    lignes = []
    for s in symboles:
        if not a_des_resultats(s):
            continue
        p = prochaine_publication(s)
        if p and p["dans_jours"] <= jours:
            ligne = {"symbole": s, "date": p["date"], "dans_jours": p["dans_jours"],
                     "bpa_attendu": p["bpa_attendu"], "amplitude_historique_%": None}
            try:
                ligne["amplitude_historique_%"] = \
                    etude(s)["reaction_jour_j"]["amplitude_moyenne_%"]
            except RuntimeError:
                pass
            lignes.append(ligne)
    if not lignes:
        return pd.DataFrame(columns=["symbole", "date", "dans_jours",
                                     "bpa_attendu", "amplitude_historique_%"])
    return pd.DataFrame(lignes).sort_values("dans_jours").reset_index(drop=True)
