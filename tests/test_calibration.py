"""Calibration des probabilités : annoncer 60 % quand il arrive 60 % du temps.

Contexte. Mesurée sur le journal, la probabilité affichée par l'outil ne
tenait pas du tout : quelle que soit la confiance annoncée — 29 % comme
83 % —, le taux de hausses observé restait à 54 %. Score de Brier 0,297,
soit PIRE qu'un tirage à pile ou face (0,25). Ces tests protègent la
correction, et surtout le critère qui décide si la note apporte vraiment
quelque chose.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import calibration


def _journal(n=3000, correlation=0.0, graine=0, horizon=20):
    """Journal synthétique : `correlation` fixe le lien réel note → hausse."""
    rng = np.random.default_rng(graine)
    note = rng.normal(size=n) * 25
    seuil = correlation * note / 25
    monte = (rng.normal(size=n) < seuil).astype(int)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="h"),
        "symbole": "TEST", "note": note, "horizon": horizon,
        "rendement_reel_%": np.where(monte == 1, 2.0, -2.0),
    })


def test_score_brier():
    assert calibration.score_brier(np.array([100.0]), np.array([1])) == 0.0
    assert calibration.score_brier(np.array([0.0]), np.array([1])) == 1.0
    assert calibration.score_brier(np.array([50.0]), np.array([1])) == 0.25


def test_une_note_sans_information_ne_doit_rien_prouver():
    """Le cas réel de MarketLab. Le critère doit résister au bruit."""
    for graine in range(4):
        m = calibration.apprendre(_journal(correlation=0.0, graine=graine))
        assert m["apporte_quelque_chose"] is False, (
            f"graine {graine} : du bruit déclaré informatif (t={m['t']})")


def test_une_note_reellement_predictive_est_reconnue():
    m = calibration.apprendre(_journal(correlation=1.2, graine=1))
    assert m["apporte_quelque_chose"] is True
    assert m["t"] > 1.96


def test_la_courbe_est_monotone():
    """Une note plus haute ne doit jamais donner une probabilité plus basse."""
    m = calibration.apprendre(_journal(correlation=0.8, graine=2))
    probas = [p["proba"] for p in m["paliers"]]
    assert probas == sorted(probas)


def test_la_calibration_ameliore_toujours_le_brier():
    """Même sans information, calibrer vaut mieux que d'annoncer n'importe
    quoi : c'est le gain principal, indépendant de la question du signal."""
    m = calibration.apprendre(_journal(correlation=0.0, graine=3))
    assert m["brier_calibre"] < m["brier_avant"]
    assert m["brier_calibre"] <= 0.26      # jamais pire qu'un pile ou face


def test_taille_deffet_effective_prise_en_compte():
    m = calibration.apprendre(_journal(n=3000, horizon=20))
    assert m["n_effectif"] == pytest.approx(150, abs=5)
    assert m["n_effectif"] < m["n"]


def test_echantillon_insuffisant_nappprend_rien():
    m = calibration.apprendre(_journal(n=50))
    assert m["paliers"] is None
    assert "insuffisant" in m["statut"]


def test_lecture_dune_note_hors_bornes():
    m = calibration.apprendre(_journal(correlation=0.5, graine=4))
    basse = calibration._lire(m["paliers"], -500)
    haute = calibration._lire(m["paliers"], +500)
    assert 0 <= basse <= 100 and 0 <= haute <= 100
    assert basse <= haute


def test_courbe_de_fiabilite(tmp_path):
    lignes = calibration.courbe_fiabilite(_journal(correlation=0.0, graine=5))
    assert lignes, "la courbe doit produire des tranches"
    for l in lignes:
        assert set(l) == {"annoncé_%", "advenu_%", "écart_pts", "n"}
        assert l["n"] >= 30


def test_proba_calibree_sans_modele(monkeypatch, tmp_path):
    """Sans modèle appris, on ne prétend pas être calibré."""
    monkeypatch.setattr(calibration, "MODELE", tmp_path / "absent.json")
    assert calibration.proba_calibree(50) is None
