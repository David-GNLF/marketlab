"""Risque d'ensemble : quatre positions font-elles un seul pari ?

Aucun accès réseau : les matrices de corrélation et les volatilités sont
injectées, ce qui rend chaque cas exactement reproductible.
"""

import numpy as np
import pandas as pd
import pytest

from marketlab import risque_portefeuille as rp


def _corr(valeurs: dict) -> pd.DataFrame:
    """Matrice symétrique depuis {(a, b): rho}."""
    noms = sorted({s for paire in valeurs for s in paire})
    m = pd.DataFrame(np.eye(len(noms)), index=noms, columns=noms)
    for (a, b), rho in valeurs.items():
        m.loc[a, b] = m.loc[b, a] = rho
    return m


def pos(sym, marge=50.0, levier=5, sens="long"):
    return {"symbole": sym, "sens": sens, "marge": marge, "levier": levier}


@pytest.fixture
def sans_reseau(monkeypatch):
    """Corrélations et volatilités figées : trois devises très liées, un or à
    part. C'est la configuration réelle qui a motivé ce module."""
    corr = _corr({
        ("EURUSD=X", "GBPUSD=X"): 0.92,
        ("EURUSD=X", "AUDUSD=X"): 0.88,
        ("GBPUSD=X", "AUDUSD=X"): 0.90,
        ("EURUSD=X", "GC=F"): 0.15,
        ("GBPUSD=X", "GC=F"): 0.12,
        ("AUDUSD=X", "GC=F"): 0.18,
        ("EURUSD=X", "USDCHF=X"): -0.95,
    })
    vols = {s: 8.0 for s in corr.index}
    vols["GC=F"] = 16.0
    monkeypatch.setattr(rp, "matrice_stress", lambda *a, **k: corr)
    monkeypatch.setattr(rp, "volatilites", lambda *a, **k: vols)
    # la co-chute de queue lit correlations.rendements — RÉSEAU. Ces tests
    # vérifient l'arithmétique de concentration ; le détecteur de queue a les
    # siens, sur données injectées.
    monkeypatch.setattr(rp.correlations, "rendements",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("réseau neutralisé en test")))
    return corr, vols


# ---------------------------------------------------------------------------
# Le cœur : compter les paris, pas les lignes
# ---------------------------------------------------------------------------

def test_trois_positions_tres_correlees_valent_un_seul_pari(sans_reseau):
    corr, _ = sans_reseau
    expo = {"EURUSD=X": 0.25, "GBPUSD=X": 0.25, "AUDUSD=X": 0.25}
    assert rp.paris_independants(expo, corr) == pytest.approx(1.1, abs=0.15)


def test_trois_positions_independantes_valent_trois_paris():
    corr = _corr({("A", "B"): 0.0, ("A", "C"): 0.0, ("B", "C"): 0.0})
    expo = {"A": 0.2, "B": 0.2, "C": 0.2}
    assert rp.paris_independants(expo, corr) == pytest.approx(3.0, abs=0.05)


def test_le_meme_pari_est_detecte_et_la_taille_reduite(sans_reseau):
    b = rp.evaluer([pos("EURUSD=X"), pos("GBPUSD=X")], 1000.0, pos("AUDUSD=X"))
    assert b["meme_pari_que"] in {"EURUSD=X", "GBPUSD=X"}
    assert b["correlation_max"] >= rp.SEUIL_MEME_PARI
    assert b["facteur"] < 1.0


def test_un_candidat_sans_rapport_nest_pas_penalise(sans_reseau):
    b = rp.evaluer([pos("EURUSD=X"), pos("GBPUSD=X")], 1000.0, pos("GC=F"))
    assert b["meme_pari_que"] is None
    assert b["facteur"] == 1.0


# ---------------------------------------------------------------------------
# Le sens : c'est le PARI qui compte, pas le cours
# ---------------------------------------------------------------------------

def test_deux_achats_sur_actifs_anticorreles_sont_deux_paris(sans_reseau):
    """Acheter EUR/USD et acheter USD/CHF, ce sont deux paris OPPOSÉS sur le
    dollar. Les cours sont anti-corrélés : sans signature par le sens, on
    croirait à une concentration là où il y a une couverture."""
    b = rp.evaluer([pos("EURUSD=X")], 1000.0, pos("USDCHF=X"))
    assert b["correlation_max"] < 0
    assert b["meme_pari_que"] is None
    assert b["facteur"] == 1.0


def test_une_vente_sur_un_actif_anticorrele_est_le_meme_pari(sans_reseau):
    """Vendre USD/CHF revient à acheter EUR/USD : même pari, et il doit être
    vu comme tel."""
    b = rp.evaluer([pos("EURUSD=X")], 1000.0, pos("USDCHF=X", sens="short"))
    assert b["correlation_max"] >= rp.SEUIL_MEME_PARI
    assert b["meme_pari_que"] == "EURUSD=X"


# ---------------------------------------------------------------------------
# Budget de volatilité
# ---------------------------------------------------------------------------

def test_le_plafond_de_volatilite_reduit_la_taille(sans_reseau, monkeypatch):
    monkeypatch.setattr(rp, "PLAFOND_VOL_PORTEFEUILLE", 5.0)
    b = rp.evaluer([pos("EURUSD=X", marge=100.0)], 1000.0,
                   pos("GC=F", marge=100.0))
    assert b["facteur"] < 1.0
    assert any("plafond" in r for r in b["raisons"])


def test_une_taille_residuelle_derisoire_fait_ecarter(sans_reseau, monkeypatch):
    """Ouvrir une position à 5 % de sa taille prévue ne vaut pas ses frais :
    mieux vaut ne pas l'ouvrir que de faire semblant."""
    monkeypatch.setattr(rp, "PLAFOND_VOL_PORTEFEUILLE", 0.5)
    b = rp.evaluer([pos("EURUSD=X", marge=150.0)], 1000.0, pos("GC=F"))
    assert b["facteur"] == 0.0
    assert any("écartée" in r for r in b["raisons"])


def test_la_volatilite_densemble_tient_compte_des_correlations(sans_reseau):
    corr, vols = sans_reseau
    liees = rp.volatilite_ensemble(
        {"EURUSD=X": 0.25, "GBPUSD=X": 0.25}, corr, vols)
    opposees = rp.volatilite_ensemble(
        {"EURUSD=X": 0.25, "USDCHF=X": 0.25}, corr, vols)
    # deux paris opposés s'annulent presque ; deux paris identiques s'ajoutent
    assert liees > opposees * 3


# ---------------------------------------------------------------------------
# Le garde-fou ne doit jamais bloquer le robot
# ---------------------------------------------------------------------------

def test_sans_historique_le_dimensionnement_est_inchange(monkeypatch):
    """Un garde-fou en panne s'efface : il ne doit pas empêcher de travailler."""
    monkeypatch.setattr(rp, "matrice_stress", lambda *a, **k: None)
    monkeypatch.setattr(rp, "volatilites", lambda *a, **k: {})
    b = rp.evaluer([pos("EURUSD=X")], 1000.0, pos("GC=F"))
    assert b["facteur"] == 1.0
    assert b["mesurable"] is False
    assert any("insuffisant" in r for r in b["raisons"])


def test_portefeuille_vide(sans_reseau):
    b = rp.evaluer([], 1000.0, pos("GC=F"))
    assert b["facteur"] == 1.0
    assert b["meme_pari_que"] is None


def test_equite_nulle_ne_divise_pas_par_zero(sans_reseau):
    assert rp.evaluer([pos("EURUSD=X")], 0.0, pos("GC=F"))["facteur"] == 1.0


def test_bilan_sans_candidat(sans_reseau):
    b = rp.evaluer([pos("EURUSD=X"), pos("GBPUSD=X")], 1000.0)
    assert b["vol_avant_%"] is not None
    assert b["paris_independants"] is not None
    assert b["facteur"] == 1.0


# ---------------------------------------------------------------------------
# La co-chute de queue : ce que la corrélation ne voit pas
# ---------------------------------------------------------------------------

def _paire_crash_commun(n=800, graine=0):
    """Rendements quasi indépendants au quotidien, mais qui s'effondrent
    ENSEMBLE les jours de crash — la configuration exacte que la corrélation
    moyenne sous-estime et que la queue révèle."""
    rng = np.random.default_rng(graine)
    a = rng.normal(0, 0.01, n)
    b = rng.normal(0, 0.01, n)
    # crashs a -5 sigma sur 9 % des jours : la queue a 10 % est alors remplie
    # a ~90 % par les jours de crash communs (lambda attendu ~0,9) tandis que
    # la correlation reste ~0,63, sous le seuil « meme pari ». Deux essais plus
    # mous (-3,5 %/6 % puis -5 %/7 %) donnaient un lambda attendu trop proche
    # du seuil de 0,60 : le verdict basculait selon la GRAINE, et un test qui
    # depend du tirage ne teste que sa chance.
    crash = rng.random(n) < 0.09
    a[crash] -= 0.05
    b[crash] -= 0.05
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.Series(a, index=idx), pd.Series(b, index=idx)


def test_des_paris_independants_ont_une_co_chute_de_base():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2023-01-02", periods=800, freq="B")
    a = pd.Series(rng.normal(0, 0.01, 800), index=idx)
    b = pd.Series(rng.normal(0, 0.01, 800), index=idx)
    l = rp.co_chute(a, b)
    assert l == pytest.approx(0.10, abs=0.07)


def test_un_crash_commun_est_vu_par_la_queue_pas_par_la_correlation():
    """Le cas qui justifie le second détecteur : corrélation ordinaire sous le
    seuil « même pari », co-chute très au-dessus du sien."""
    a, b = _paire_crash_commun(graine=2)
    assert float(a.corr(b)) < rp.SEUIL_MEME_PARI
    assert rp.co_chute(a, b) > rp.SEUIL_CO_CHUTE


def test_trop_court_pour_juger_la_queue():
    idx = pd.date_range("2026-01-01", periods=100, freq="B")
    rng = np.random.default_rng(3)
    a = pd.Series(rng.normal(0, 0.01, 100), index=idx)
    assert rp.co_chute(a, a) is None


def test_evaluer_reduit_sur_co_chute_quand_la_correlation_se_tait(sans_reseau,
                                                                  monkeypatch):
    """Intégration : corrélation de stress posée MODÉRÉE (le premier détecteur
    se tait), crash commun dans les rendements — le second parle, une seule
    raison est donnée."""
    corr, _ = sans_reseau
    corr.loc["EURUSD=X", "GC=F"] = corr.loc["GC=F", "EURUSD=X"] = 0.30
    a, b = _paire_crash_commun(graine=4)
    monkeypatch.setattr(rp.correlations, "rendements",
                        lambda *a_, **k: pd.DataFrame({"GC=F": a,
                                                       "EURUSD=X": b}))
    bilan = rp.evaluer([pos("EURUSD=X")], 1000.0, pos("GC=F"))
    assert bilan["meme_pari_que"] == "EURUSD=X"
    assert bilan["co_chute_max"] >= rp.SEUIL_CO_CHUTE
    assert sum("co-chute" in r for r in bilan["raisons"]) == 1
    assert not any("corrélation +" in r for r in bilan["raisons"])
    assert bilan["facteur"] < 1.0
