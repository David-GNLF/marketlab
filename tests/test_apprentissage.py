"""Tests de l'apprentissage des pondérations.

La règle qu'on protège ici : une composante ne gagne du poids que si sa
valeur est DÉMONTRÉE. Un IC de +0,02 sur 3 000 verdicts n'est pas une
compétence, c'est du bruit — et l'outil ne doit pas se réorganiser autour
du bruit.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import decision


def _journal(n=600, correlation=0.0, graine=0):
    """Journal synthétique : `correlation` fixe le lien réel entre la note de
    la composante « technique » et le rendement advenu."""
    rng = np.random.default_rng(graine)
    bruit = rng.normal(size=n)
    signal = rng.normal(size=n)
    rendement = correlation * signal + np.sqrt(max(1 - correlation**2, 0)) * bruit
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "symbole": ["TEST"] * n, "avis": ["Neutre"] * n,
        "note": signal * 10, "prix": 100.0, "horizon": 20,
        "c_technique": signal * 10,
        "c_prevision": rng.normal(size=n) * 10,
        "c_analogues": rng.normal(size=n) * 10,
        "rendement_reel_%": rendement,
        "rendement_relatif_%": rendement,
    })


def test_aucun_mouvement_quand_rien_nest_demontre():
    """Le cas réel de MarketLab : des IC minuscules, donc pas de conclusion."""
    rapport = decision._calculer_poids(_journal(n=3000, correlation=0.0))
    assert rapport["poids"] is None
    assert "aucune composante n'a démontré" in rapport["statut"]


def test_une_composante_reellement_predictive_gagne_du_poids():
    rapport = decision._calculer_poids(_journal(n=3000, correlation=0.30))
    assert rapport["poids"] is not None
    assert rapport["ic_par_composante"]["technique"]["prouve"] is True
    assert rapport["poids"]["technique"] > decision.POIDS["technique"]


def test_une_composante_anticorrelee_ne_recoit_pas_de_poids_negatif():
    rapport = decision._calculer_poids(_journal(n=3000, correlation=-0.30))
    assert rapport["ic_par_composante"]["technique"]["prouve"] is False
    if rapport["poids"]:
        assert all(v > 0 for v in rapport["poids"].values())
        assert rapport["poids"]["technique"] < decision.POIDS["technique"]


def test_echantillon_insuffisant_ne_bouge_rien():
    rapport = decision._calculer_poids(_journal(n=40, correlation=0.9))
    assert rapport["poids"] is None
    assert "insuffisant" in rapport["statut"]


def test_les_poids_somment_a_un_et_respectent_le_plancher():
    rapport = decision._calculer_poids(_journal(n=3000, correlation=0.30))
    poids = rapport["poids"]
    # chaque poids est arrondi à 4 décimales : la somme peut dévier d'autant
    assert sum(poids.values()) == pytest.approx(1.0, abs=1e-3)
    assert min(poids.values()) >= 0.019   # plancher 2 % (arrondi)


def test_rendement_relatif_retire_la_derive_de_lactif():
    """Deux actifs, l'un qui monte structurellement : la dérive ne doit pas
    être créditée au verdict."""
    df = pd.DataFrame({
        "symbole": ["A"] * 3 + ["B"] * 3,
        "rendement_reel_%": [10.0, 12.0, 14.0, 0.0, 1.0, 2.0],
    })
    derive = df.groupby("symbole")["rendement_reel_%"].transform("median")
    relatif = df["rendement_reel_%"] - derive
    assert list(relatif) == [-2.0, 0.0, 2.0, -1.0, 0.0, 1.0]


def test_candidat_est_mesure_sans_recevoir_de_poids():
    df = _journal(n=3000, correlation=0.30)
    df["c_brokers"] = df["c_technique"]        # candidat très prédictif
    rapport = decision._calculer_poids(df)
    assert "brokers" in rapport["candidats"]
    assert rapport["candidats"]["brokers"]["prouve"] is True
    # ... mais il ne pèse toujours rien dans le verdict
    assert "brokers" not in (rapport["poids"] or {})
