"""Calibration des probabilités : dire 60 % quand il arrive 60 % du temps.

Une probabilité n'a d'intérêt que si elle est TENUE. Mesurée sur le journal,
celle que l'outil affichait ne l'était pas du tout : quelle que soit la
confiance annoncée — 29 % comme 83 % —, le taux de hausses réellement
observé restait collé à 54 %, le taux de base. Annoncer 83 % n'engageait donc
rien, et c'était pire qu'un simple bruit : aux extrêmes, l'écart atteignait
28 points dans le mauvais sens.

Ce module apprend la relation RÉELLE entre la note du verdict et la
fréquence de hausse observée, par régression isotone — une courbe monotone
qui ne suppose aucune forme a priori et se contente de suivre les données.
Si la note ne porte aucune information, la courbe s'aplatit autour du taux
de base : l'outil annonce alors 54 % partout, ce qui est la vérité.

Le gain se mesure au score de Brier (erreur quadratique moyenne entre
probabilité annoncée et résultat advenu, plus bas = mieux). On le compare
toujours à celui de la référence naïve « annoncer le taux de base à tout le
monde » : une calibration qui ne bat pas cette référence n'apporte rien.
"""

import json

import numpy as np
import pandas as pd

from marketlab import config

MODELE = config.DATA_DIR / "calibration.json"
MIN_VERDICTS = 200          # en deçà, on n'ajuste rien
MIN_PAR_NOEUD = 60          # taille minimale d'un palier de la courbe


def score_brier(probas: np.ndarray, issues: np.ndarray) -> float:
    """Erreur quadratique moyenne. 0 = parfait, 0,25 = pile ou face."""
    return float(np.mean((np.asarray(probas) / 100 - np.asarray(issues)) ** 2))


def _isotone(x: np.ndarray, y: np.ndarray, poids_min: int) -> list[dict]:
    """Régression isotone par paliers (pool adjacent violators, simplifié).

    Renvoie une liste de paliers {note_min, note_max, proba} croissants en
    note. Les paliers trop petits sont fusionnés avec leur voisin : mieux
    vaut peu de paliers solides que beaucoup de paliers anecdotiques.
    """
    ordre = np.argsort(x)
    x, y = x[ordre], y[ordre]
    n = len(x)
    if n < poids_min * 2:
        return [{"note_min": float(x[0]), "note_max": float(x[-1]),
                 "proba": float(y.mean() * 100), "n": n}]

    # découpage initial en tranches de taille égale, puis fusion des
    # tranches qui violent la monotonie (moyennes décroissantes)
    n_tranches = max(2, n // poids_min)
    bornes = np.array_split(np.arange(n), n_tranches)
    paliers = [{"i": list(b), "somme": float(y[b].sum()), "n": len(b)}
               for b in bornes if len(b)]
    fusion = True
    while fusion and len(paliers) > 1:
        fusion = False
        for k in range(len(paliers) - 1):
            g, d = paliers[k], paliers[k + 1]
            if g["somme"] / g["n"] > d["somme"] / d["n"]:
                paliers[k] = {"i": g["i"] + d["i"],
                              "somme": g["somme"] + d["somme"],
                              "n": g["n"] + d["n"]}
                del paliers[k + 1]
                fusion = True
                break
    return [{"note_min": float(x[p["i"][0]]), "note_max": float(x[p["i"][-1]]),
             "proba": round(p["somme"] / p["n"] * 100, 1), "n": p["n"]}
            for p in paliers]


def apprendre(df_evalue: pd.DataFrame) -> dict:
    """Apprend la courbe note → probabilité de hausse à partir du journal."""
    d = df_evalue.dropna(subset=["note", "rendement_reel_%"])
    n = len(d)
    if n < MIN_VERDICTS:
        return {"statut": f"échantillon insuffisant ({n} verdicts, "
                          f"minimum {MIN_VERDICTS})", "paliers": None}

    notes = d["note"].to_numpy(dtype=float)
    monte = (d["rendement_reel_%"].to_numpy(dtype=float) > 0).astype(float)
    base = float(monte.mean() * 100)
    paliers = _isotone(notes, monte, MIN_PAR_NOEUD)

    calibrees = np.array([_lire(paliers, note) for note in notes])
    brier_calibre = score_brier(calibrees, monte)
    brier_base = score_brier(np.full(n, base), monte)
    # la probabilité affichée jusqu'ici, reconstruite depuis la note
    brute = np.clip(50 + notes * 0.8, 5, 95)
    brier_brut = score_brier(brute, monte)

    # La courbe bat-elle VRAIMENT le simple taux de base, ou de façon
    # anecdotique ? Comparaison appariée des erreurs, observation par
    # observation, avec une taille d'échantillon EFFECTIVE : les verdicts se
    # recouvrent (horizon de 20 séances, verdicts quasi quotidiens), les
    # compter comme indépendants gonflerait la certitude d'un facteur √20.
    # Sans cette précaution, un gain de 0,0001 serait déclaré « démontré » —
    # exactement l'erreur qu'on reproche à l'outil.
    horizon = int(d["horizon"].median()) if "horizon" in d.columns else 20
    erreurs_base = (base / 100 - monte) ** 2
    erreurs_cal = (calibrees / 100 - monte) ** 2
    ecarts = erreurs_base - erreurs_cal          # positif = la courbe gagne
    n_eff = max(n / max(horizon, 1), 10)
    err_type = float(ecarts.std(ddof=1)) / np.sqrt(n_eff) if n > 1 else 0.0
    t = float(ecarts.mean() / err_type) if err_type > 0 else 0.0
    utile = t > 1.96

    return {
        "n": n, "n_effectif": int(n_eff), "taux_de_base_%": round(base, 1),
        "paliers": paliers,
        "brier_avant": round(brier_brut, 4),
        "brier_calibre": round(brier_calibre, 4),
        "brier_taux_de_base": round(brier_base, 4),
        "gain_sur_taux_de_base": round(float(ecarts.mean()), 6),
        "t": round(t, 2),
        "apporte_quelque_chose": bool(utile),
        "statut": ("la note apporte de l'information démontrée : la courbe "
                   "calibrée bat significativement la simple annonce du taux "
                   "de base"
                   if utile else
                   "la note n'apporte rien de démontrable : la courbe calibrée "
                   "ne fait pas mieux que d'annoncer le taux de base à tout le "
                   "monde. Les probabilités affichées sont donc ramenées près "
                   "de ce taux — c'est honnête, et déjà bien plus juste que "
                   "les 83 % annoncés auparavant, qui étaient pires qu'un "
                   "tirage à pile ou face."),
        "methode": ("régression isotone sur le journal : on n'invente aucune "
                    "forme, on suit la fréquence de hausse réellement observée "
                    "par tranche de note. Comparaison au score de Brier."),
    }


def _lire(paliers: list[dict], note: float) -> float:
    """Probabilité calibrée pour une note donnée."""
    for p in paliers:
        if note <= p["note_max"]:
            return p["proba"]
    return paliers[-1]["proba"]


def enregistrer(modele: dict) -> None:
    MODELE.parent.mkdir(parents=True, exist_ok=True)
    MODELE.write_text(json.dumps(modele, ensure_ascii=False, indent=1),
                      encoding="utf-8")


def charger() -> dict | None:
    if not MODELE.exists():
        return None
    try:
        return json.loads(MODELE.read_text(encoding="utf-8"))
    except Exception:
        return None


def proba_calibree(note: float) -> dict | None:
    """La probabilité de hausse que l'historique autorise pour cette note.

    Renvoie None tant qu'aucun modèle n'a été appris — l'outil affiche alors
    sa probabilité brute, faute de mieux, mais ne prétend pas qu'elle est
    calibrée.
    """
    modele = charger()
    if not modele or not modele.get("paliers"):
        return None
    return {"proba_%": _lire(modele["paliers"], float(note)),
            "taux_de_base_%": modele.get("taux_de_base_%"),
            "fondee_sur": modele.get("n"),
            "informative": modele.get("apporte_quelque_chose", False)}


def courbe_fiabilite(df_evalue: pd.DataFrame,
                     tranches=((0, 40), (40, 50), (50, 55), (55, 60),
                               (60, 65), (65, 70), (70, 101))) -> list[dict]:
    """Ce que l'outil annonçait contre ce qui est arrivé, par tranche.

    C'est le tableau qui rend la calibration vérifiable par n'importe qui :
    une colonne « annoncé », une colonne « advenu », et l'écart.
    """
    d = df_evalue.dropna(subset=["note", "rendement_reel_%"]).copy()
    if d.empty:
        return []
    d["annoncee"] = np.clip(50 + d["note"] * 0.8, 5, 95)
    d["montee"] = (d["rendement_reel_%"] > 0).astype(int)
    lignes = []
    for bas, haut in tranches:
        g = d[(d["annoncee"] >= bas) & (d["annoncee"] < haut)]
        if len(g) < 30:
            continue
        annonce = float(g["annoncee"].mean())
        advenu = float(g["montee"].mean() * 100)
        lignes.append({
            "annoncé_%": round(annonce, 1),
            "advenu_%": round(advenu, 1),
            "écart_pts": round(advenu - annonce, 1),
            "n": len(g),
        })
    return lignes
