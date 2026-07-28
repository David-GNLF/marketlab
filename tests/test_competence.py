"""La mesure de compétence doit résister aux fenêtres qui se recouvrent.

Régression protégée : une première version corrélait note et rendement sur
toutes les lignes du journal en les traitant comme indépendantes. Avec un
horizon de 20 séances et des verdicts quasi quotidiens, 20 lignes voisines
décrivent presque le même bout de marché — la certitude était gonflée d'un
facteur √20, au point d'annoncer comme « démontré » un résultat qui ne
l'était pas. Le test ci-dessous vérifie qu'un signal PUREMENT ALÉATOIRE
n'est jamais déclaré démontré.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import decision

SYMBOLES = [f"S{i}" for i in range(12)]


def _journal(n_dates=220, horizon=20, correlation=0.0, graine=0):
    """Journal synthétique avec fenêtres recouvrantes, comme le vrai."""
    rng = np.random.default_rng(graine)
    dates = pd.date_range("2024-06-25", periods=n_dates, freq="B")
    lignes = []
    # facteur de marché commun, persistant : c'est lui qui crée le
    # recouvrement entre dates voisines
    marche = pd.Series(rng.normal(size=n_dates + horizon)).rolling(
        horizon, min_periods=1).mean().to_numpy()
    for k, date in enumerate(dates):
        for s in SYMBOLES:
            note = rng.normal() * 20
            rendement = (correlation * note / 20 * 5
                         + rng.normal() * 5 + marche[k] * 3)
            lignes.append({"date": date, "symbole": s, "avis": "Neutre",
                           "note": note, "prix": 100.0, "horizon": horizon,
                           "rendement_reel_%": rendement,
                           "rendement_relatif_%": rendement})
    return pd.DataFrame(lignes)


def test_taux_de_faux_positifs_conforme_au_seuil():
    """Le cas qui a piégé la première version.

    Un seuil à 95 % laisse passer 5 % de faux positifs PAR CONSTRUCTION :
    exiger zéro serait une erreur de raisonnement symétrique de celle qu'on
    corrige. Ce qu'on vérifie, c'est que le taux reste voisin de 5 % — et
    non de 40 %, ce que donnait la version qui ignorait le recouvrement.
    """
    faux = sum(decision._mesurer_competence(
        _journal(correlation=0.0, graine=g))["sens"] != "indéterminé"
        for g in range(40))
    assert faux / 40 <= 0.20, f"{faux}/40 conclusions tirées de bruit pur"


def test_vraie_competence_est_detectee():
    c = decision._mesurer_competence(_journal(correlation=0.9, graine=1))
    assert c["sens"] == "positif"
    assert c["t"] > 1.96


def test_vraie_inversion_est_detectee():
    c = decision._mesurer_competence(_journal(correlation=-0.9, graine=2))
    assert c["sens"] == "négatif"
    assert c["t"] < -1.96


def test_les_episodes_independants_sont_comptes():
    c = decision._mesurer_competence(_journal(n_dates=220, horizon=20))
    assert c["episodes_independants"] == pytest.approx(220 / 20, abs=2)
    assert c["n_dates"] > c["episodes_independants"]


def test_echantillon_trop_court_ne_conclut_pas():
    c = decision._mesurer_competence(_journal(n_dates=15))
    assert "statut" in c and "pas encore" in c["statut"]


def test_taille_effective_utilisee_pour_les_composantes():
    """Une composante ne doit pas être « prouvée » par du recouvrement."""
    rng = np.random.default_rng(3)
    n = 3200
    bruit = rng.normal(size=n)
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="h"),
        "symbole": "S", "avis": "Neutre", "note": bruit * 10, "prix": 100.0,
        "horizon": 20,
        "c_technique": bruit * 10,
        "c_prevision": rng.normal(size=n) * 10,
        "c_analogues": rng.normal(size=n) * 10,
        "rendement_reel_%": bruit * 0.35 + rng.normal(size=n),
    })
    df["rendement_relatif_%"] = df["rendement_reel_%"]
    rapport = decision._calculer_poids(df)
    info = rapport["ic_par_composante"]["technique"]
    assert info["n_effectif"] == pytest.approx(n / 20, abs=2)
    assert info["n_effectif"] < info["n"]
