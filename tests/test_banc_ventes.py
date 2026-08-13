"""Banc d'essai du côté vente : consigner, rejouer en SHORT, conclure prudemment.

Aucun réseau : le plan de vente et les cours sont injectés. Le chemin du
journal est détourné vers tmp_path — le CSV commité ne doit jamais influencer
une valeur attendue. Les sémantiques SHORT sont le cœur du fichier : stop
AU-DESSUS touché par le haut, objectif EN DESSOUS touché par le bas, gain
quand le cours BAISSE.
"""

import numpy as np
import pandas as pd
import pytest

from marketlab import banc_ventes as bv


@pytest.fixture(autouse=True)
def _journal_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "JOURNAL_PATH", tmp_path / "banc_ventes.csv")


def _dossier(symbole="AAPL", avis="Défavorable", note=-35.0, horizon=20):
    return {"symbole": symbole, "date": "2026-08-13", "horizon": horizon,
            "avis": avis, "note_globale": note}


def _plan_vente(entree=100.0, stop=106.0, objectif=91.0):
    return {"entree": entree, "stop": stop, "objectif": objectif,
            "esperance_nette_%": 0.8,
            "couts": {"seuil_actif_%": 0.5}}


# ---------------------------------------------------------------- candidats

def test_candidats_defavorable_ou_note_franchement_negative():
    dossiers = [_dossier("A", avis="Défavorable", note=-10),
                _dossier("B", avis="Neutre", note=-25),
                _dossier("C", avis="Neutre", note=-5),
                _dossier("D", avis="Favorable", note=40),
                {"symbole": "E", "erreur": "boom"}]
    assert [d["symbole"] for d in bv.candidats(dossiers)] == ["A", "B"]


def test_journalise_le_plan_de_vente_du_moteur(monkeypatch):
    from marketlab import levels
    appels = []
    monkeypatch.setattr(levels, "plan",
                        lambda symbole, sens, horizon: appels.append(
                            (symbole, sens, horizon)) or _plan_vente())
    n = bv.journaliser([_dossier("AAPL"), _dossier("KC=F", note=-40,
                                                   avis="Neutre")])
    assert n == 2
    assert all(sens == "vente" for _, sens, _ in appels)
    journal = pd.read_csv(bv.JOURNAL_PATH)
    ligne = journal.set_index("symbole").loc["AAPL"]
    assert ligne["stop"] > ligne["entree"] > ligne["objectif"]


def test_le_premier_ecrit_gagne(monkeypatch):
    from marketlab import levels
    monkeypatch.setattr(levels, "plan",
                        lambda *a, **k: _plan_vente(entree=100.0))
    bv.journaliser([_dossier("AAPL")])
    monkeypatch.setattr(levels, "plan",
                        lambda *a, **k: _plan_vente(entree=999.0))
    bv.journaliser([_dossier("AAPL")])
    journal = pd.read_csv(bv.JOURNAL_PATH)
    assert len(journal) == 1 and journal["entree"].iloc[0] == 100.0


def test_un_titre_sans_plan_n_entre_pas_au_banc(monkeypatch):
    from marketlab import levels

    def _casse(*a, **k):
        raise RuntimeError("pas de cours")
    monkeypatch.setattr(levels, "plan", _casse)
    assert bv.journaliser([_dossier("MORT")]) == 0
    assert not bv.JOURNAL_PATH.exists()


# ------------------------------------------------------------- rejeu en SHORT

def _cours(closes, bas=None, hauts=None, debut="2026-08-14"):
    idx = pd.bdate_range(debut, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "close": closes,
        "low": np.asarray(bas, dtype=float) if bas is not None else closes - 0.5,
        "high": np.asarray(hauts, dtype=float) if hauts is not None
        else closes + 0.5,
    }, index=idx)


def test_short_stoppe_par_le_HAUT():
    df = _cours([100, 103, 104], hauts=[101, 106.5, 105])
    r = bv._rejouer_vente(df, "2026-08-13", 100.0, 106.0, 91.0, horizon=3)
    assert r["issue"] == "stop"
    assert r["rendement_pari_%"] == pytest.approx(-6.0)   # le short PERD


def test_short_gagne_a_l_objectif_par_le_BAS():
    df = _cours([100, 95, 92], bas=[99, 94, 90.5])
    r = bv._rejouer_vente(df, "2026-08-13", 100.0, 106.0, 91.0, horizon=3)
    assert r["issue"] == "objectif"
    assert r["rendement_pari_%"] == pytest.approx(9.0)    # le short GAGNE


def test_stop_prudent_si_les_deux_le_meme_jour():
    df = _cours([100, 98, 100], bas=[99, 90, 99], hauts=[101, 107, 101])
    r = bv._rejouer_vente(df, "2026-08-13", 100.0, 106.0, 91.0, horizon=3)
    assert r["issue"] == "stop"


def test_echeance_au_dernier_cours():
    df = _cours([100, 99, 97])
    r = bv._rejouer_vente(df, "2026-08-13", 100.0, 106.0, 91.0, horizon=3)
    assert r["issue"] == "echeance"
    assert r["rendement_pari_%"] == pytest.approx(3.0)    # baisse = gain


def test_immature_renvoie_none():
    df = _cours([100, 99])
    assert bv._rejouer_vente(df, "2026-08-13", 100.0, 106.0, 91.0,
                             horizon=3) is None


# --------------------------------------------------------------------- bilan

def _remplir(monkeypatch, n, plan=None):
    from marketlab import levels
    monkeypatch.setattr(levels, "plan", lambda *a, **k: plan or _plan_vente())
    bv.journaliser([_dossier(f"S{i}", horizon=3) for i in range(n)])


def test_bilan_trop_tot_ne_conclut_pas(monkeypatch):
    _remplir(monkeypatch, 3)
    monkeypatch.setattr("marketlab.data.get_ohlcv",
                        lambda s, lookback_days=0: _cours([100, 99, 97]))
    b = bv.bilan()
    assert b["murs"] == 3 and "Trop tôt" in b["lecture"]


def test_bilan_signale_quand_les_ventes_paient(monkeypatch):
    _remplir(monkeypatch, 12)
    monkeypatch.setattr("marketlab.data.get_ohlcv",
                        lambda s, lookback_days=0: _cours(
                            [100, 95, 92], bas=[99, 94, 90.5]))
    b = bv.bilan()
    assert b["murs"] == 12
    # objectif à −9 % du cours : le pari rend +9 % brut, −0,5 % de coût
    assert b["rendement_net_moyen_%"] == pytest.approx(8.5, abs=0.01)
    assert b["lecture"].startswith("SIGNAL")


def test_bilan_confirme_le_refus_quand_les_ventes_perdent(monkeypatch):
    _remplir(monkeypatch, 12)
    monkeypatch.setattr("marketlab.data.get_ohlcv",
                        lambda s, lookback_days=0: _cours(
                            [100, 103, 104], hauts=[101, 106.5, 105]))
    b = bv.bilan()
    assert b["rendement_net_moyen_%"] < 0
    assert "refus de vendre reste justifié" in b["lecture"]
