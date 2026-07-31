"""HAR-RV et recalage de volatilité — aucun accès réseau."""

import numpy as np
import pandas as pd
import pytest

from marketlab import forecast, har


def _serie_rv(n=120, base=1e-4, graine=7):
    """Variance réalisée synthétique, persistante comme la vraie."""
    rng = np.random.default_rng(graine)
    lg = np.log(base)
    valeurs = []
    for _ in range(n):
        lg = 0.95 * lg + 0.05 * np.log(base) + rng.normal(0, 0.15)
        valeurs.append(np.exp(lg))
    dates = pd.date_range("2026-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.Series(valeurs, index=dates)


# ---------------------------------------------------------------------------
# Composantes : aucune fuite d'information
# ---------------------------------------------------------------------------

def test_composantes_ne_voient_jamais_leur_futur():
    rv = _serie_rv(60)
    comp = har.composantes(rv, horizon=1)
    lg = np.log(rv)
    # la cible d'une ligne est bien le log RV de la séance SUIVANTE
    for date in comp.index[:5]:
        pos = list(rv.index).index(date)
        assert comp.loc[date, "cible"] == pytest.approx(lg.iloc[pos + 1])
        assert comp.loc[date, "rv_j"] == pytest.approx(lg.iloc[pos])


def test_les_moyennes_sarretent_a_t_inclus():
    rv = _serie_rv(60)
    comp = har.composantes(rv, horizon=1)
    lg = np.log(rv)
    date = comp.index[10]
    pos = list(rv.index).index(date)
    assert comp.loc[date, "rv_s"] == pytest.approx(
        lg.iloc[pos - har.FENETRE_SEMAINE + 1:pos + 1].mean())
    assert comp.loc[date, "rv_m"] == pytest.approx(
        lg.iloc[pos - har.FENETRE_MOIS + 1:pos + 1].mean())


def test_cible_multi_horizon_est_la_moyenne_du_futur():
    rv = _serie_rv(80)
    comp = har.composantes(rv, horizon=5)
    lg = np.log(rv)
    date = comp.index[3]
    pos = list(rv.index).index(date)
    assert comp.loc[date, "cible"] == pytest.approx(lg.iloc[pos + 1:pos + 6].mean())


def test_la_premiere_ligne_exploitable_arrive_apres_le_mois():
    rv = _serie_rv(60)
    comp = har.composantes(rv, horizon=1)
    assert len(comp) == 60 - har.FENETRE_MOIS  # 21 perdues au début, 1 à la fin


def test_composantes_sur_serie_degeneree():
    assert har.composantes(pd.Series(dtype=float)).empty
    assert har.composantes(pd.Series([0.0, -1.0, np.nan])).empty


# ---------------------------------------------------------------------------
# Ajustement
# ---------------------------------------------------------------------------

def test_ajustement_refuse_un_echantillon_mince():
    """Quatre coefficients sur 30 points décriraient le bruit."""
    petit = har.composantes(_serie_rv(60))
    assert len(petit) < har.OBS_MIN
    assert har.ajuster(petit) is None
    assert har.ajuster(pd.DataFrame()) is None


def test_ajustement_retrouve_une_relation_posee():
    """Cible construite EXACTEMENT comme le modèle : il doit la retrouver."""
    rng = np.random.default_rng(3)
    n = 600
    cadre = pd.DataFrame({
        "rv_j": rng.normal(-9, 0.5, n),
        "rv_s": rng.normal(-9, 0.4, n),
        "rv_m": rng.normal(-9, 0.3, n),
    })
    cadre["cible"] = (-0.5 + 0.2 * cadre["rv_j"] + 0.5 * cadre["rv_s"]
                      + 0.3 * cadre["rv_m"] + rng.normal(0, 0.01, n))
    m = har.ajuster(cadre)
    assert m["b_jour"] == pytest.approx(0.2, abs=0.02)
    assert m["b_semaine"] == pytest.approx(0.5, abs=0.02)
    assert m["b_mois"] == pytest.approx(0.3, abs=0.02)
    assert m["somme_pentes"] == pytest.approx(1.0, abs=0.03)
    assert m["r2"] > 0.99


# ---------------------------------------------------------------------------
# Critères de comparaison
# ---------------------------------------------------------------------------

def test_qlike_minimal_quand_la_prevision_est_juste():
    vrai = np.array([1e-4, 2e-4, 5e-4])
    assert har._qlike(vrai, vrai) == pytest.approx(0.0, abs=1e-12)
    assert har._qlike(vrai, vrai * 2) > 0
    assert har._qlike(vrai, vrai / 2) > 0


def test_qlike_penalise_plus_la_sous_estimation():
    """Asymétrie voulue : annoncer trop peu de risque est la faute grave."""
    vrai = np.array([1e-4, 1e-4])
    assert har._qlike(vrai, vrai / 2) > har._qlike(vrai, vrai * 2)


def test_ewma_est_une_recursion_causale():
    rv = np.array([1e-4, 1e-4, 9e-4, 1e-4])
    out = har._ewma_sur_rv(rv, lam=0.9)
    # la case t ne dépend que de rv[0..t] : le saut de t=2 ne remonte pas en t=1
    assert out[1] == pytest.approx(0.9 * (0.9 * 1e-4 + 0.1 * 1e-4) + 0.1 * 1e-4)
    assert out[2] > out[1]


# ---------------------------------------------------------------------------
# Arbitrage : le garde-fou doit rester fermé sans preuve
# ---------------------------------------------------------------------------

def test_arbitrage_sans_releve_ne_retient_rien():
    vide = pd.DataFrame(columns=["date", "symbole", "interval", "rv",
                                 "n_barres", "vol_annualisee_%"])
    a = har.comparer(releve=vide)
    assert a["suffisant"] is False
    assert not a.get("har_retenu")


def test_la_regle_exige_les_deux_criteres():
    """Les quatre combinaisons, exercées une par une.

    L'ancien test recopiait la formule de har.py et la réappliquait à sa
    propre sortie : il était vrai quelle que soit l'implémentation. Et son jeu
    d'essai — trois colonnes identiques à la cible — faisait gagner HAR sur
    les DEUX critères, si bien que le désaccord annoncé dans son nom n'était
    jamais exercé.
    """
    assert har.retenir("har", "har") is True
    assert har.retenir("har", "ewma") is False, (
        "un avantage QLIKE non confirme par le RMSE ne doit rien retenir")
    assert har.retenir("hasard", "har") is False
    assert har.retenir("ewma", "ewma") is False


def test_la_regle_ne_se_contente_pas_dun_seul_critere():
    """Le defaut exact que l'ancien test laissait passer : si `and` devenait
    `or`, un modele gagnant un seul critere partirait en production."""
    desaccords = [("har", "ewma"), ("har", "hasard"),
                  ("ewma", "har"), ("hasard", "har")]
    assert not any(har.retenir(q, r) for q, r in desaccords)


def test_prevoir_sans_modele_retenu_renvoie_none(tmp_path, monkeypatch):
    """None doit rester None : jamais de repli sur une valeur inventée."""
    chemin = tmp_path / "har_modele.json"
    chemin.write_text('{"retenu": false, "horizons": {}}', encoding="utf-8")
    monkeypatch.setattr(har, "MODELE_PATH", chemin)
    assert har.prevoir("AAPL") is None


def test_prevoir_sans_fichier_renvoie_none(tmp_path, monkeypatch):
    monkeypatch.setattr(har, "MODELE_PATH", tmp_path / "absent.json")
    assert har.charger_modele() is None
    assert har.prevoir("AAPL") is None


# ---------------------------------------------------------------------------
# Recalage de volatilité dans la projection
# ---------------------------------------------------------------------------

def _prix(n=400, vol=0.01, graine=11):
    rng = np.random.default_rng(graine)
    r = rng.normal(0.0002, vol, n)
    return pd.DataFrame({"close": 100 * np.exp(np.cumsum(r))},
                        index=pd.date_range("2024-01-01", periods=n, freq="B"))


def test_sans_vol_cible_le_comportement_est_inchange():
    df = _prix()
    a = forecast.projeter(df, horizon=10, n_sim=3000)
    b = forecast.projeter(df, horizon=10, n_sim=3000, vol_cible=None)
    assert a["intervalle_80"] == b["intervalle_80"]
    assert a["facteur_volatilite"] is None


def test_une_vol_cible_plus_faible_resserre_le_cone():
    df = _prix(vol=0.01)
    large = forecast.projeter(df, horizon=10, n_sim=6000)
    serre = forecast.projeter(df, horizon=10, n_sim=6000, vol_cible=0.005)
    l_large = large["intervalle_80"][1] - large["intervalle_80"][0]
    l_serre = serre["intervalle_80"][1] - serre["intervalle_80"][0]
    assert l_serre < l_large
    assert serre["facteur_volatilite"] == pytest.approx(0.5, abs=0.06)


def test_une_vol_cible_plus_forte_elargit_le_cone():
    df = _prix(vol=0.01)
    base = forecast.projeter(df, horizon=10, n_sim=6000)
    large = forecast.projeter(df, horizon=10, n_sim=6000, vol_cible=0.02)
    assert (large["intervalle_80"][1] - large["intervalle_80"][0]) > \
           (base["intervalle_80"][1] - base["intervalle_80"][0])
    assert large["facteur_volatilite"] == pytest.approx(2.0, abs=0.2)


def test_le_facteur_est_borne():
    """Une prévision aberrante ne doit pas pouvoir rendre le cône absurde."""
    df = _prix(vol=0.01)
    p = forecast.projeter(df, horizon=5, n_sim=2000, vol_cible=5.0)
    assert p["facteur_volatilite"] == forecast.FACTEUR_VOL_MAX
    p = forecast.projeter(df, horizon=5, n_sim=2000, vol_cible=1e-9)
    assert p["facteur_volatilite"] == forecast.FACTEUR_VOL_MIN


def test_le_recalage_ne_deplace_pas_le_prix_median():
    """On redimensionne les ÉCARTS à la dérive, pas la dérive : changer la
    volatilité ne doit pas changer le scénario central."""
    df = _prix(vol=0.01)
    base = forecast.projeter(df, horizon=10, n_sim=20000)
    serre = forecast.projeter(df, horizon=10, n_sim=20000, vol_cible=0.005)
    assert serre["rendement_median_%"] == pytest.approx(
        base["rendement_median_%"], abs=0.35)
