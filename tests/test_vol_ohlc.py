"""Estimateurs OHLC (GKYZ, Yang-Zhang) et ES orphelin — aucun réseau."""

import numpy as np
import pandas as pd
import pytest

from marketlab import implicite as imp, vol_ohlc

PAS = 50          # pas intrajournaliers simulés par séance


def _ohlc(n_jours, vol_intra=0.01, vol_nuit=0.005, graine=0, depart="2024-01-02"):
    """OHLC simulé pas à pas : le haut et le bas sont VRAIS, pas inventés.

    Variance quotidienne totale posée = vol_intra² + vol_nuit² — c'est la
    cible que les estimateurs doivent retrouver.
    """
    rng = np.random.default_rng(graine)
    p, lignes = 100.0, []
    for _ in range(n_jours):
        o = p * np.exp(rng.normal(0, vol_nuit))
        chemin = o * np.exp(np.cumsum(
            rng.normal(0, vol_intra / np.sqrt(PAS), PAS)))
        lignes.append({"open": o, "high": float(chemin.max()),
                       "low": float(chemin.min()), "close": float(chemin[-1])})
        p = float(chemin[-1])
    return pd.DataFrame(lignes,
                        index=pd.date_range(depart, periods=n_jours, freq="B"))


VAR_VRAIE = 0.01 ** 2 + 0.005 ** 2
VOL_VRAIE_ANN = float(np.sqrt(VAR_VRAIE * 252) * 100)      # ≈ 17,7 %


# ---------------------------------------------------------------------------
# La variance par séance (GKYZ)
# ---------------------------------------------------------------------------

def test_gkyz_retrouve_la_variance_posee():
    var = vol_ohlc.variance_gkyz(_ohlc(500, graine=1))
    assert var.mean() == pytest.approx(VAR_VRAIE, rel=0.25)


def test_gkyz_est_toujours_positive():
    """Le haut/bas majore le trajet ouverture-clôture : le terme soustrait ne
    peut pas l'emporter. Indispensable — un HAR travaille en log-variance."""
    var = vol_ohlc.variance_gkyz(_ohlc(500, graine=2))
    assert (var > 0).all()


def test_le_saut_de_nuit_est_compte():
    """C'est ce qui distingue ces estimateurs de la RV intrajournalière, qui
    exclut les nuits par construction."""
    avec = vol_ohlc.variance_gkyz(_ohlc(400, vol_nuit=0.01, graine=3)).mean()
    sans = vol_ohlc.variance_gkyz(_ohlc(400, vol_nuit=0.0, graine=3)).mean()
    assert avec > sans * 1.3


def test_les_seances_degenerees_sont_ecartees():
    df = _ohlc(60, graine=4)
    df.iloc[10, df.columns.get_loc("high")] = df.iloc[10]["low"] - 1  # high < low
    df.iloc[20] = 0.0                                                # zéros
    var = vol_ohlc.variance_gkyz(df)
    assert np.isfinite(var).all() and (var > 0).all()
    assert len(var) <= 58


# ---------------------------------------------------------------------------
# Yang-Zhang par fenêtre : le niveau, en mieux
# ---------------------------------------------------------------------------

def test_yang_zhang_retrouve_le_niveau_pose():
    yz = vol_ohlc.vol_yang_zhang(_ohlc(200, graine=5), fenetre=63)
    assert yz == pytest.approx(VOL_VRAIE_ANN, rel=0.2)


def test_yang_zhang_est_plus_precis_que_les_clotures():
    """LA raison d'être de tout le module : sur la même fenêtre de 21 séances,
    la dispersion des estimations Yang-Zhang doit être nettement plus serrée
    que celle des clôtures seules. Sinon les quatre prix n'apportent rien."""
    df = _ohlc(840, graine=6)
    closes = df["close"]
    yz_est, cc_est = [], []
    for debut in range(0, 800, 21):
        bloc = df.iloc[debut:debut + 22]
        yz = vol_ohlc.vol_yang_zhang(bloc, fenetre=21)
        r = np.log(closes.iloc[debut:debut + 22]).diff().dropna()
        if yz is not None and len(r) >= 21:
            yz_est.append(yz)
            cc_est.append(float(r.std() * np.sqrt(252) * 100))
    assert len(yz_est) > 30
    assert np.std(yz_est) < np.std(cc_est) * 0.8


def test_sans_ohlc_yang_zhang_avoue():
    assert vol_ohlc.vol_yang_zhang(pd.DataFrame({"close": [1, 2, 3]})) is None


# ---------------------------------------------------------------------------
# Le réalisé futur, OHLC quand il existe, clôtures sinon
# ---------------------------------------------------------------------------

def test_vol_future_sur_ohlc_retrouve_la_cible():
    realise = vol_ohlc.vol_future(_ohlc(300, graine=7)).dropna()
    assert realise.median() == pytest.approx(VOL_VRAIE_ANN, rel=0.25)


def test_vol_future_sans_fenetre_finie_reste_nan():
    realise = vol_ohlc.vol_future(_ohlc(100, graine=8))
    assert realise.iloc[-21:].isna().all()


def test_le_repli_clotures_reproduit_l_ancien_comportement():
    """Les tests d'implicite servent des DataFrames sans OHLC : le repli doit
    donner EXACTEMENT ce que donnait vol_cloture, sinon la bascule change des
    résultats qu'elle prétendait seulement affiner."""
    closes = _ohlc(200, graine=9)["close"]
    df_sans = closes.to_frame("close")
    nouveau = vol_ohlc.vol_future(df_sans)
    ancien = imp.vol_cloture(closes)
    pd.testing.assert_series_equal(nouveau.dropna(), ancien.dropna(),
                                   check_names=False)


# ---------------------------------------------------------------------------
# Le rejeu de l'arbitrage HAR sur cinq ans de GKYZ
# ---------------------------------------------------------------------------

def test_le_rejeu_produit_un_verdict_complet(monkeypatch):
    """Structure seulement : le verdict réel se mesure sur les vraies données.
    Volatilité PERSISTANTE posée (deux régimes), sinon ni HAR ni EWMA n'ont
    rien à apprendre et le duel ne veut rien dire."""
    calme = _ohlc(220, vol_intra=0.006, graine=10)
    agite = _ohlc(220, vol_intra=0.02, graine=11,
                  depart=str(calme.index[-1] + pd.Timedelta(days=1)))
    serie = pd.concat([calme, agite])
    from marketlab import data
    monkeypatch.setattr(data, "get_ohlcv", lambda *a, **k: serie)
    v = vol_ohlc.rejouer_arbitrage_har(symboles=["A", "B"], horizon=1)
    assert v["suffisant"] is True
    assert set(v["scores"]) == {"har", "hasard", "ewma"}
    assert "GKYZ" in v["matiere"]
    assert isinstance(v["har_retenu"], bool)


def test_sans_ohlc_le_rejeu_le_dit(monkeypatch):
    from marketlab import data

    def tombe(*a, **k):
        raise RuntimeError("source coupée")

    monkeypatch.setattr(data, "get_ohlcv", tombe)
    v = vol_ohlc.rejouer_arbitrage_har(symboles=["A"])
    assert v["suffisant"] is False


# ---------------------------------------------------------------------------
# L'ES orphelin : cohérence, maintenant qu'il est exposé
# ---------------------------------------------------------------------------

def test_l_es_est_toujours_au_moins_aussi_severe_que_la_var():
    """L'Expected Shortfall est la perte MOYENNE au-delà de la VaR : il ne peut
    par construction pas être plus clément qu'elle. Calculé depuis le premier
    jour du cône et affiché nulle part — on le vérifie maintenant qu'il sort."""
    from marketlab import forecast
    rng = np.random.default_rng(12)
    df = pd.DataFrame({"close": 100 * np.exp(np.cumsum(
        rng.normal(0.0002, 0.012, 400)))},
        index=pd.date_range("2024-01-01", periods=400, freq="B"))
    p = forecast.projeter(df, horizon=10, n_sim=4000)
    assert p["perte_moyenne_pire_5_%"] <= p["var_95_%"]
    assert p["perte_moyenne_pire_5_%"] < 0
