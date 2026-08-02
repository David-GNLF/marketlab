"""Volatilité implicite, prime de variance, banc d'essai — aucun réseau."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlab import implicite as imp


@pytest.fixture(autouse=True)
def _memo_propre():
    imp._MEMO.clear()
    yield
    imp._MEMO.clear()


# ---------------------------------------------------------------------------
# Choix d'échéance
# ---------------------------------------------------------------------------

def test_l_echeance_la_plus_proche_de_30_jours_est_choisie():
    ref = dt.date(2026, 8, 1)
    exps = ["2026-08-07", "2026-08-28", "2026-09-18", "2027-01-15"]
    assert imp.choisir_echeance(exps, ref) == "2026-08-28"   # 27 jours


def test_le_tres_court_terme_est_ecarte():
    """Sous 7 jours, une option porte un bruit de fin de vie, pas une
    prévision — même si c'est l'échéance la plus proche de la cible."""
    ref = dt.date(2026, 8, 1)
    assert imp.choisir_echeance(["2026-08-04", "2026-10-16"], ref) == "2026-10-16"


def test_sans_echeance_exploitable():
    assert imp.choisir_echeance([], dt.date(2026, 8, 1)) is None
    assert imp.choisir_echeance(["n'importe quoi"], dt.date(2026, 8, 1)) is None


# ---------------------------------------------------------------------------
# Extraction — le tri des cotations malades
# ---------------------------------------------------------------------------

def _chaine(strikes, ivs):
    return pd.DataFrame({"strike": strikes,
                         "impliedVolatility": [v / 100 for v in ivs]})


def test_l_iv_a_la_monnaie_est_la_mediane_des_greves_proches():
    calls = _chaine([90, 95, 100, 105, 110], [42, 41, 40, 39, 38])
    puts = _chaine([90, 95, 100, 105, 110], [44, 43, 40, 39, 38])
    e = imp.extraire_iv(calls, puts, spot=100.0)
    assert e["mesurable"] is True
    # médiane des 3 grèves les plus proches de 100, calls et puts confondus
    assert 38 <= e["iv_atm_pct"] <= 44


def test_une_cotation_figee_ne_deplace_pas_la_mesure():
    """Un contrat à IV 0,001 % (cotation morte) ou 500 % (division par un prix
    nul) est une donnée malade : filtrée AVANT la médiane."""
    calls = _chaine([95, 100, 105, 100], [40, 39, 41, 500])
    puts = _chaine([95, 100, 105, 100], [42, 40, 39, 0.001])
    e = imp.extraire_iv(calls, puts, spot=100.0)
    assert e["mesurable"] is True
    assert 38 <= e["iv_atm_pct"] <= 43       # ni 500 ni 0,001 n'ont pesé


def test_le_skew_mesure_l_asymetrie_de_la_peur():
    """Put à 95 % plus cher que le call à 105 % : skew positif — la protection
    à la baisse se paie plus cher que le pari à la hausse."""
    calls = _chaine([95, 100, 105], [40, 38, 36])
    puts = _chaine([95, 100, 105], [45, 40, 38])
    e = imp.extraire_iv(calls, puts, spot=100.0)
    assert e["skew_pts"] == pytest.approx(45 - 36, abs=0.01)
    assert e["skew_pts"] > 0


def test_trop_peu_de_contrats_sains():
    e = imp.extraire_iv(_chaine([100], [40]), _chaine([100], [40]), 100.0)
    assert e["mesurable"] is False


# ---------------------------------------------------------------------------
# Relevé accumulé
# ---------------------------------------------------------------------------

def test_fusion_immuable_le_premier_instantane_fait_foi():
    ligne = {"date": "2026-08-01", "symbole": "AAPL", "jours_echeance": 27,
             "iv_atm_pct": 38.6, "skew_pts": 4.0, "n_contrats": 50}
    ancien = pd.DataFrame([ligne])
    nouveau = pd.DataFrame([{**ligne, "iv_atm_pct": 99.0}])
    fusion = imp.fusionner(ancien, nouveau)
    assert len(fusion) == 1
    assert fusion.loc[0, "iv_atm_pct"] == pytest.approx(38.6)


# ---------------------------------------------------------------------------
# Le réalisé qui couvre le même temps que l'option
# ---------------------------------------------------------------------------

def _closes(n, vol_jour, graine=0, depart="2025-01-01"):
    rng = np.random.default_rng(graine)
    r = rng.normal(0, vol_jour, n)
    return pd.Series(100 * np.exp(np.cumsum(r)),
                     index=pd.date_range(depart, periods=n, freq="B"))


def test_vol_cloture_regarde_l_avenir_pas_le_passe():
    """La valeur en t doit décrire t+1..t+21 : au changement de régime, c'est
    AVANT la bascule que la mesure doit monter — elle anticipe par
    construction, puisqu'elle lit le futur réalisé."""
    calme = _closes(120, 0.005, graine=1)
    agite = _closes(120, 0.03, graine=2,
                    depart=str(calme.index[-1] + pd.Timedelta(days=1)))
    serie = pd.concat([calme, agite])
    realise = imp.vol_cloture(serie)
    avant_bascule = realise.iloc[95]      # fenêtre 96..116, encore calme
    juste_avant = realise.iloc[118]       # fenêtre 119..139, déjà agitée
    assert juste_avant > avant_bascule * 3


def test_vol_cloture_retrouve_une_volatilite_posee():
    serie = _closes(300, 0.01, graine=3)
    realise = imp.vol_cloture(serie).dropna()
    attendu = 0.01 * np.sqrt(252) * 100
    assert realise.median() == pytest.approx(attendu, rel=0.25)


def test_les_dernieres_seances_nont_pas_de_realise():
    """Leur fenêtre n'est pas finie : NaN, jamais une valeur partielle."""
    realise = imp.vol_cloture(_closes(100, 0.01))
    assert realise.iloc[-imp.SEANCES_REALISE:].isna().all()


# ---------------------------------------------------------------------------
# La prime de variance du marché
# ---------------------------------------------------------------------------

def test_la_prime_posee_est_retrouvee(monkeypatch):
    """S&P simulé à 16 % de volatilité annualisée, VIX servi 5 points
    au-dessus : la prime médiane doit ressortir à ~+5, y compris sur les
    fenêtres indépendantes."""
    spx = _closes(700, 0.01, graine=4)                    # ~15,9 % annualisés
    vix = pd.Series(0.01 * np.sqrt(252) * 100 + 5.0, index=spx.index)

    def faux_ohlcv(symbole, **k):
        return pd.DataFrame({"close": vix if symbole == "^VIX" else spx})

    from marketlab import data
    monkeypatch.setattr(data, "get_ohlcv", faux_ohlcv)
    p = imp.prime_variance_vix()
    assert p["mesurable"] is True
    assert p["prime_mediane_pts"] == pytest.approx(5.0, abs=1.5)
    assert p["prime_mediane_independante_pts"] == pytest.approx(5.0, abs=2.5)
    assert p["part_jours_positive_%"] > 85
    assert p["n_independants"] >= 15
    assert "assurance" in p["lecture"]


def test_sans_donnees_la_prime_le_dit(monkeypatch):
    from marketlab import data

    def tombe(*a, **k):
        raise RuntimeError("source coupée")

    monkeypatch.setattr(data, "get_ohlcv", tombe)
    assert imp.prime_variance_vix()["mesurable"] is False


# ---------------------------------------------------------------------------
# Portrait par titre
# ---------------------------------------------------------------------------

def test_synthese_depuis_le_releve_sans_reseau(monkeypatch):
    monkeypatch.setattr(imp, "charger_releve", lambda: pd.DataFrame([
        {"date": "2026-08-01", "symbole": "AAPL", "jours_echeance": 27,
         "iv_atm_pct": 38.6, "skew_pts": 4.2, "n_contrats": 50}]))
    s = imp.synthese_titre("AAPL", _closes(300, 0.012, graine=6).to_frame("close"))
    assert s["iv_atm_pct"] == pytest.approx(38.6)
    assert s["notre_prevision_pct"] > 0
    assert s["realise_21s_pct"] > 0
    assert "assurance" in s["lecture"]
    assert "plus cher" in s["lecture"]        # skew positif


def test_hors_releve_pas_de_portrait(monkeypatch):
    monkeypatch.setattr(imp, "charger_releve",
                        lambda: pd.DataFrame(columns=imp.COLONNES))
    assert imp.synthese_titre("EURUSD=X") is None


# ---------------------------------------------------------------------------
# Le banc d'essai différé
# ---------------------------------------------------------------------------

def test_sans_duels_murs_le_verdict_attend(monkeypatch):
    monkeypatch.setattr(imp, "charger_releve",
                        lambda: pd.DataFrame(columns=imp.COLONNES))
    v = imp.comparer_previsionnistes()
    assert v["mesurable"] is False


def test_le_marche_gagne_quand_il_a_vu_juste(monkeypatch):
    """Changement de régime : le marché price la tempête à venir, notre EWMA
    ne connaît que le calme passé. Le QLIKE doit donner le duel au marché —
    c'est le scénario exact pour lequel ce banc d'essai existe."""
    calme = _closes(150, 0.005, graine=7)
    agite = _closes(150, 0.03, graine=8,
                    depart=str(calme.index[-1] + pd.Timedelta(days=1)))
    serie = pd.concat([calme, agite])
    dates_photo = serie.index[128:144]        # fenêtres majoritairement agitées
    iv_juste = 0.03 * np.sqrt(252) * 100
    monkeypatch.setattr(imp, "charger_releve", lambda: pd.DataFrame([
        {"date": d.date().isoformat(), "symbole": "AAPL", "jours_echeance": 27,
         "iv_atm_pct": iv_juste, "skew_pts": 3.0, "n_contrats": 50}
        for d in dates_photo]))
    from marketlab import data
    monkeypatch.setattr(data, "get_ohlcv",
                        lambda *a, **k: serie.to_frame("close"))
    v = imp.comparer_previsionnistes(seances_min=10)
    assert v["mesurable"] is True
    assert v["qlike_iv"] < v["qlike_ewma"]
    assert v["gagnant"] == "marché (IV)"
    assert "SA prévision" in v["lecture"]
