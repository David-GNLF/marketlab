"""Spread mesuré (Roll) et son branchement dans les coûts — aucun réseau."""

import numpy as np
import pandas as pd
import pytest

from marketlab import couts, microstructure as ms


@pytest.fixture(autouse=True)
def _memo_propre():
    """Le module mémorise le relevé chargé : chaque test repart à neuf,
    sinon l'ordre d'exécution déciderait du résultat."""
    ms._MEMO.clear()
    yield
    ms._MEMO.clear()


def _seance(spread=0.10, n=300, mid=100.0, graine=0, jour="2026-07-28"):
    """Une séance simulée : prix milieu constant, transactions au prix
    acheteur ou vendeur au hasard — le modèle exact de Roll."""
    rng = np.random.default_rng(graine)
    q = rng.choice([-1.0, 1.0], size=n)
    prix = mid + spread / 2 * q
    idx = pd.date_range(f"{jour} 07:00", periods=n, freq="5min")
    return pd.Series(prix, index=idx)


# ---------------------------------------------------------------------------
# L'estimateur
# ---------------------------------------------------------------------------

def test_roll_retrouve_un_spread_pose():
    """Rebond aléatoire entre prix acheteur et vendeur séparés de S :
    l'estimateur doit retrouver S. C'est le modèle qui l'a défini."""
    e = ms.estimer_roll(_seance(spread=0.10, n=2000))
    assert e["mesurable"] is True
    # 0,10 sur un cours de 100 = 0,10 %
    assert e["spread_pct"] == pytest.approx(0.10, rel=0.15)


def test_un_spread_plus_large_donne_une_estimation_plus_large():
    petit = ms.estimer_roll(_seance(spread=0.05, n=2000, graine=1))
    grand = ms.estimer_roll(_seance(spread=0.20, n=2000, graine=1))
    assert grand["spread_pct"] > petit["spread_pct"] * 2


def test_sans_rebond_l_estimateur_avoue_ne_rien_voir():
    """Une dérive régulière n'a pas d'anti-corrélation : covariance ≥ 0, et le
    bon comportement est de le DIRE, pas d'extorquer un chiffre. C'est le cas
    réel MC.PA de la sonde."""
    # variations POSITIVEMENT autocorrélées (momentum intrajournalier) : le
    # premier essai utilisait une dérive linéaire, dont les variations
    # constantes donnent une covariance nulle... aux erreurs d'arrondi près,
    # qui peuvent la rendre infinitésimalement négative et faire « mesurer »
    # un spread de 1e-16. Un cas dégénéré ne teste rien.
    rng = np.random.default_rng(5)
    dp = np.empty(300)
    dp[0] = 0.0
    for i in range(1, 300):
        dp[i] = 0.9 * dp[i - 1] + rng.normal(0, 0.05)
    prix = pd.Series(100 + np.cumsum(dp),
                     index=pd.date_range("2026-07-28 07:00", periods=300,
                                         freq="5min"))
    e = ms.estimer_roll(prix)
    assert e["mesurable"] is False
    assert "positive" in e["raison"]


def test_trop_peu_d_ecarts():
    e = ms.estimer_roll(_seance(n=20))
    assert e["mesurable"] is False and "requis" in e["raison"]


# ---------------------------------------------------------------------------
# Relevé par séance terminée
# ---------------------------------------------------------------------------

def test_la_seance_du_jour_est_exclue(monkeypatch):
    """Même discipline que la volatilité réalisée : une séance partielle
    estimée sur trois écarts resterait à vie dans un relevé immuable."""
    import datetime as dt
    barres = pd.concat([_seance(jour="2026-07-28"), _seance(jour="2026-07-29")])
    monkeypatch.setattr(ms.intraday, "lire",
                        lambda *a, **k: pd.DataFrame({"close": barres}))
    r = ms.releve_du_magasin("AAPL", aujourdhui=dt.date(2026, 7, 29))
    assert list(r["date"]) == ["2026-07-28"]


def test_fusion_immuable_le_premier_releve_fait_foi():
    ancien = pd.DataFrame([{"date": "2026-07-28", "symbole": "AAPL",
                            "interval": "5m", "spread_pct": 0.04,
                            "n_ecarts": 200}])
    nouveau = pd.DataFrame([{"date": "2026-07-28", "symbole": "AAPL",
                             "interval": "5m", "spread_pct": 9.0,
                             "n_ecarts": 200}])
    fusion = ms.fusionner(ancien, nouveau)
    assert len(fusion) == 1
    assert fusion.loc[0, "spread_pct"] == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# La valeur de production : médiane, jamais une estimation isolée
# ---------------------------------------------------------------------------

def _releve(valeurs, symbole="EURUSD=X"):
    return pd.DataFrame([{"date": f"2026-07-{10 + i:02d}", "symbole": symbole,
                          "interval": "5m", "spread_pct": v, "n_ecarts": 200}
                         for i, v in enumerate(valeurs)])


def test_sous_le_seuil_de_seances_pas_de_valeur(monkeypatch):
    monkeypatch.setattr(ms, "charger_releve", lambda: _releve([0.01] * 4))
    assert ms.spread_median("EURUSD=X") is None


def test_la_mediane_resiste_a_une_seance_aberrante(monkeypatch):
    """Une séance à 0,50 % (coquille, trou de cotation) ne déplace pas une
    médiane — elle aurait doublé une moyenne."""
    monkeypatch.setattr(ms, "charger_releve",
                        lambda: _releve([0.007, 0.008, 0.007, 0.006, 0.007, 0.50]))
    m = ms.spread_median("EURUSD=X")
    assert m["spread_pct"] == pytest.approx(0.007, abs=0.001)


# ---------------------------------------------------------------------------
# Le branchement dans les coûts : mesure d'abord, table en repli, source dite
# ---------------------------------------------------------------------------

def test_sans_mesure_la_table_sert_de_repli(monkeypatch):
    monkeypatch.setattr(ms, "spread_median", lambda *a, **k: None)
    r = couts.spread_effectif("EURUSD=X")
    assert r["spread_pct"] == pytest.approx(couts.SPREAD_PCT["Forex"])
    assert r["source"] == "table"


def test_la_mesure_prime_quand_elle_existe(monkeypatch):
    """Le cas qui motive tout : 0,007 % mesuré contre 0,015 % supposé sur le
    forex — la table surestimait d'un facteur 2 et écartait des idées à tort."""
    monkeypatch.setattr(ms, "spread_median",
                        lambda *a, **k: {"spread_pct": 0.007, "n_seances": 30,
                                         "derniere": "2026-07-31"})
    r = couts.spread_effectif("EURUSD=X")
    assert r["spread_pct"] == pytest.approx(0.007)
    assert "mesuré (30 séances)" in r["source"]


def test_une_mesure_absurde_est_bornee_et_le_dit(monkeypatch):
    """L'estimateur n'a pas le droit de renverser l'ordre de grandeur en
    silence : un spread mesuré 30 fois la table est une donnée malade, pas une
    découverte."""
    monkeypatch.setattr(ms, "spread_median",
                        lambda *a, **k: {"spread_pct": 0.5, "n_seances": 10,
                                         "derniere": "2026-07-31"})
    r = couts.spread_effectif("EURUSD=X")
    assert r["spread_pct"] == pytest.approx(couts.SPREAD_PCT["Forex"] * 4)
    assert "borné" in r["source"]


def test_un_spread_mesure_a_zero_ne_rend_pas_le_trading_gratuit(monkeypatch):
    """Des cotations lissées (milieux de fourchette) donnent un Roll quasi nul.
    Le plancher à ¼ de table empêche le filtre de coût de disparaître."""
    monkeypatch.setattr(ms, "spread_median",
                        lambda *a, **k: {"spread_pct": 0.0001, "n_seances": 20,
                                         "derniere": "2026-07-31"})
    r = couts.spread_effectif("EURUSD=X")
    assert r["spread_pct"] == pytest.approx(couts.SPREAD_PCT["Forex"] / 4)
    assert "borné" in r["source"]


def test_couts_annonce_toujours_sa_source(monkeypatch):
    monkeypatch.setattr(ms, "spread_median", lambda *a, **k: None)
    c = couts.couts("AAPL", horizon=20)
    assert c["spread_source"] == "table"
    c2 = couts.couts("AAPL", horizon=20, spread_pct=0.03)
    assert c2["spread_source"] == "fourni"
