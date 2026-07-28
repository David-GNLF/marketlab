"""Mesure en parallèle à plusieurs horizons.

Raison d'être : à 20 séances, deux ans de journal ne pèsent qu'une dizaine
d'épisodes de marché indépendants — trop peu pour conclure quoi que ce soit.
Les mêmes verdicts mesurés à 5 séances en donnent quatre fois plus, sans
attendre une seule journée de plus. Ces tests vérifient que la mesure
parallèle est correcte et qu'elle ne se ment pas sur sa propre puissance.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import decision


def _journal(n_dates=220, correlation=0.0, graine=0):
    rng = np.random.default_rng(graine)
    dates = pd.date_range("2024-06-25", periods=n_dates, freq="B")
    lignes = []
    for date in dates:
        for s in [f"S{i}" for i in range(12)]:
            note = rng.normal() * 20
            base = {"date": date, "symbole": s, "avis": "Neutre",
                    "note": note, "prix": 100.0, "horizon": 20,
                    "rendement_reel_%": correlation * note / 20 * 5
                                        + rng.normal() * 5}
            for h in decision.HORIZONS_MESURE:
                base[f"rendement_h{h}_%"] = (correlation * note / 20 * 5
                                             + rng.normal() * 5)
            lignes.append(base)
    return pd.DataFrame(lignes)


def test_un_horizon_court_donne_plus_depisodes_independants():
    df = _journal()
    court = decision._mesurer_competence(df, "rendement_h5_%", horizon_force=5)
    long_ = decision._mesurer_competence(df, "rendement_h20_%", horizon_force=20)
    assert court["episodes_independants"] > long_["episodes_independants"]
    assert court["episodes_independants"] == pytest.approx(
        long_["episodes_independants"] * 4, rel=0.35)


def test_un_horizon_court_detecte_des_ic_plus_faibles():
    """C'est tout l'intérêt de la manœuvre : abaisser le seuil de détection."""
    df = _journal()
    court = decision._mesurer_competence(df, "rendement_h5_%", horizon_force=5)
    long_ = decision._mesurer_competence(df, "rendement_h20_%", horizon_force=20)
    assert court["ic_detectable"] < long_["ic_detectable"]


def test_horizon_force_prime_sur_la_colonne_du_journal():
    df = _journal()
    c = decision._mesurer_competence(df, "rendement_h5_%", horizon_force=5)
    assert c["horizon_seances"] == 5      # et non 20, valeur du journal


def test_chaque_horizon_reste_honnete_sur_du_bruit():
    df = _journal(correlation=0.0, graine=7)
    for h in decision.HORIZONS_MESURE:
        c = decision._mesurer_competence(df, f"rendement_h{h}_%", horizon_force=h)
        assert c["sens"] == "indéterminé", f"horizon {h} conclut sur du bruit"


def test_une_vraie_competence_ressort_a_tous_les_horizons():
    df = _journal(correlation=1.2, graine=3)
    for h in decision.HORIZONS_MESURE:
        c = decision._mesurer_competence(df, f"rendement_h{h}_%", horizon_force=h)
        assert c["sens"] == "positif", f"horizon {h} rate un vrai signal"


def test_colonne_absente_ne_casse_pas_le_bilan():
    df = _journal().drop(columns=["rendement_h3_%"])
    c = decision._mesurer_competence(df, "rendement_h3_%", horizon_force=3)
    assert "statut" in c
