"""Magasin intrajournalier et volatilité réalisée — aucun accès réseau."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlab import intraday
from marketlab.data import base, binance, premium, yahoo


# ---------------------------------------------------------------------------
# Bornage des profondeurs Yahoo
# ---------------------------------------------------------------------------

def test_profondeur_bornee_en_intrajournalier():
    """Demander 730 jours de 5 min renvoyait un tableau VIDE chez Yahoo."""
    assert yahoo.profondeur_utile("5m", 730) == 59
    assert yahoo.profondeur_utile("1m", 730) == 6
    assert yahoo.profondeur_utile("1h", 3000) == 729


def test_la_borne_intraday_garde_un_jour_de_marge():
    """Pile 60 jours est REFUSÉ par Yahoo (limite vérifiée sur un horodatage,
    pas une date) : à l'amorçage, les 54 titres du lot groupé sont tombés
    ensemble. La borne doit rester strictement sous la limite annoncée."""
    assert yahoo.PROFONDEUR_MAX_JOURS["5m"] < 60
    assert yahoo.PROFONDEUR_MAX_JOURS["1m"] < 7
    assert yahoo.PROFONDEUR_MAX_JOURS["1h"] < 730


def test_profondeur_quotidienne_intacte():
    """Le chemin quotidien ne doit surtout pas être bridé."""
    assert yahoo.profondeur_utile("1d", 3000) == 3000
    assert yahoo.profondeur_utile("1wk", 5000) == 5000


def test_binance_aligne_la_fenetre_intraday_sur_minuit(monkeypatch):
    """Sans alignement, la 1re journée de la fenêtre commençait en milieu de
    séance : 230 barres au lieu de 287, volatilité sous-estimée de ~20 %."""
    vus = {}

    def faux_requete(params):
        vus.update(params)
        return []  # fenêtre vide : get_ohlcv lèvera, on ne teste que l'appel

    monkeypatch.setattr(binance, "_requete", faux_requete)
    monkeypatch.setattr(binance.base, "load_cached", lambda *a, **k: None)

    for interval, aligne in (("5m", True), ("1h", True), ("1d", False)):
        vus.clear()
        with pytest.raises(RuntimeError):
            binance.get_ohlcv("BTCUSDT", interval=interval, lookback_days=5)
        debut = dt.datetime.fromtimestamp(vus["startTime"] / 1000, dt.timezone.utc)
        minuit = debut.hour == 0 and debut.minute == 0 and debut.second == 0
        assert minuit is aligne, f"{interval} : alignement inattendu ({debut})"


def test_intervalle_inconnu_leve_au_lieu_de_retomber_en_quotidien():
    """Le repli silencieux sur 1d rendait la granularité invisible."""
    with pytest.raises(ValueError, match="Binance"):
        binance.get_ohlcv("BTCUSDT", interval="3m")
    assert "5m" in binance.INTERVAL_MAP and "15m" in binance.INTERVAL_MAP
    assert "5m" in premium.INTERVAL_MAP


# ---------------------------------------------------------------------------
# Fuseau : les barres de deux places doivent être alignables
# ---------------------------------------------------------------------------

def test_normalize_ramene_en_utc_quand_demande():
    idx = pd.date_range("2026-07-28 09:30", periods=3, freq="5min",
                        tz="America/New_York")
    df = pd.DataFrame({"Close": [1.0, 1.1, 1.2]}, index=idx)
    utc = base.normalize(df, tz="UTC")
    assert utc.index[0] == pd.Timestamp("2026-07-28 13:30")  # 9h30 NY = 13h30 UTC
    assert utc.index.tz is None


def test_normalize_sans_fuseau_garde_le_comportement_historique():
    idx = pd.date_range("2026-07-28", periods=2, freq="D", tz="America/New_York")
    df = pd.DataFrame({"Close": [1.0, 1.1]}, index=idx)
    naif = base.normalize(df)
    assert naif.index[0] == pd.Timestamp("2026-07-28")


def test_nom_fichier_partage_avec_le_cache():
    assert base.nom_fichier("GC=F") == "GC_F"
    assert base.nom_fichier("^VIX") == "IDX_VIX"
    assert base.nom_fichier("MC.PA") == "MC_PA"


# ---------------------------------------------------------------------------
# Volatilité réalisée
# ---------------------------------------------------------------------------

def _barres(jour: str, n: int, pas: float = 0.001, depart: float = 100.0):
    """n barres de 5 min avec un rendement log constant, pour un RV connu."""
    idx = pd.date_range(f"{jour} 13:30", periods=n, freq="5min")
    closes = depart * np.exp(np.arange(n) * pas)
    return pd.DataFrame({"close": closes}, index=idx)


def test_rv_vaut_la_somme_des_carres_des_rendements():
    df = _barres("2026-07-28", 13, pas=0.001)  # 12 rendements de 0,001
    rv = intraday.volatilite_realisee(df)
    assert len(rv) == 1
    assert rv.loc[0, "n_barres"] == 12
    assert rv.loc[0, "rv"] == pytest.approx(12 * 0.001 ** 2, rel=1e-9)
    attendu = np.sqrt(12 * 0.001 ** 2 * intraday.SEANCES_AN) * 100
    assert rv.loc[0, "vol_annualisee_%"] == pytest.approx(attendu, rel=1e-9)


def test_le_saut_de_nuit_est_exclu():
    """Deux séances distantes de 50 % : le trou ne doit PAS compter comme vol."""
    j1 = _barres("2026-07-28", 5, pas=0.001, depart=100.0)
    j2 = _barres("2026-07-29", 5, pas=0.001, depart=150.0)
    rv = intraday.volatilite_realisee(pd.concat([j1, j2]))
    assert len(rv) == 2
    # 4 rendements par séance, aucun rendement à cheval sur les deux jours
    assert list(rv["n_barres"]) == [4, 4]
    assert rv["rv"].max() == pytest.approx(4 * 0.001 ** 2, rel=1e-9)


def test_rv_vide_ne_casse_pas():
    assert intraday.volatilite_realisee(pd.DataFrame()).empty
    assert intraday.volatilite_realisee(None).empty
    assert intraday.volatilite_realisee(
        pd.DataFrame({"close": []}, index=pd.DatetimeIndex([]))).empty


# ---------------------------------------------------------------------------
# Le piège central : ne jamais enregistrer la séance en cours
# ---------------------------------------------------------------------------

def test_la_seance_du_jour_est_exclue_du_releve():
    """Une séance partielle a une variance faible : l'écrire créerait un faux
    souvenir de journée calme, réinjecté chaque jour dans l'historique."""
    rv = pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28", "2026-07-29"],
        "rv": [1e-4, 2e-4, 1e-9],
        "n_barres": [78, 78, 3],
        "vol_annualisee_%": [15.9, 22.4, 0.05],
    })
    gardees = intraday.journees_completes(rv, aujourdhui=dt.date(2026, 7, 29))
    assert list(gardees["date"]) == ["2026-07-27", "2026-07-28"]


def test_les_seances_trop_courtes_sont_ecartees():
    rv = pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28"],
        "rv": [1e-4, 1e-6],
        "n_barres": [78, 4],  # 4 barres = demi-séance ou titre peu traité
        "vol_annualisee_%": [15.9, 1.6],
    })
    gardees = intraday.journees_completes(rv, aujourdhui=dt.date(2026, 7, 29))
    assert list(gardees["date"]) == ["2026-07-27"]


def test_seuil_relatif_a_linstrument():
    """Cas RÉEL mesuré : EURUSD=X un dimanche soir, 11 barres au lieu de 287.
    Le plancher absolu de 10 le laissait passer ; c'est la médiane de
    l'instrument qui l'écarte, sans réglage par actif."""
    rv = pd.DataFrame({
        "date": ["2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29"],
        "rv": [1.8e-07, 9.1e-06, 6.1e-06, 2.2e-05],
        "n_barres": [11, 287, 287, 287],
        "vol_annualisee_%": [0.68, 4.80, 3.92, 7.44],
    })
    gardees = intraday.journees_completes(rv, aujourdhui=dt.date(2026, 7, 30))
    assert list(gardees["date"]) == ["2026-07-27", "2026-07-28", "2026-07-29"]


def test_seuil_relatif_epargne_une_seance_actions_normale():
    """78 barres est une séance PLEINE en actions : le seuil relatif ne doit
    pas confondre « peu de barres » et « barres manquantes »."""
    rv = pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28", "2026-07-29"],
        "rv": [1.7e-4, 1.2e-4, 1.7e-4],
        "n_barres": [77, 77, 77],
        "vol_annualisee_%": [20.8, 17.1, 20.5],
    })
    gardees = intraday.journees_completes(rv, aujourdhui=dt.date(2026, 7, 30))
    assert len(gardees) == 3


def test_seuil_relatif_inactif_sous_trois_seances():
    """Une médiane sur deux points ne veut rien dire : plancher absolu seul."""
    rv = pd.DataFrame({
        "date": ["2026-07-27", "2026-07-28"],
        "rv": [1e-4, 5e-5],
        "n_barres": [287, 40],
        "vol_annualisee_%": [15.9, 11.2],
    })
    gardees = intraday.journees_completes(rv, aujourdhui=dt.date(2026, 7, 30))
    assert len(gardees) == 2


# ---------------------------------------------------------------------------
# Relevé versionné : stable d'un passage à l'autre
# ---------------------------------------------------------------------------

def _ligne(date, symbole, rv):
    return pd.DataFrame([{
        "date": date, "symbole": symbole, "interval": "5m",
        "rv": rv, "n_barres": 78, "vol_annualisee_%": np.sqrt(rv * 252) * 100,
    }])


def test_une_journee_deja_relevee_nest_pas_reecrite():
    """Sinon le fichier bougerait à chaque passage et le workflow produirait
    un commit par exécution."""
    ancien = _ligne("2026-07-28", "AAPL", 1e-4)
    nouveau = _ligne("2026-07-28", "AAPL", 9e-9)  # valeur dégradée
    fusion = intraday.fusionner_releve(ancien, nouveau)
    assert len(fusion) == 1
    assert fusion.loc[0, "rv"] == pytest.approx(1e-4)


def test_recalculer_force_la_reecriture():
    ancien = _ligne("2026-07-28", "AAPL", 1e-4)
    nouveau = _ligne("2026-07-28", "AAPL", 5e-4)
    fusion = intraday.fusionner_releve(ancien, nouveau, recalculer=True)
    assert len(fusion) == 1
    assert fusion.loc[0, "rv"] == pytest.approx(5e-4)


def test_une_journee_nouvelle_sajoute_et_le_releve_reste_trie():
    ancien = pd.concat([_ligne("2026-07-28", "MSFT", 1e-4),
                        _ligne("2026-07-28", "AAPL", 1e-4)], ignore_index=True)
    fusion = intraday.fusionner_releve(ancien, _ligne("2026-07-29", "AAPL", 2e-4))
    assert len(fusion) == 3
    assert list(fusion["date"]) == ["2026-07-28", "2026-07-28", "2026-07-29"]
    assert list(fusion["symbole"][:2]) == ["AAPL", "MSFT"]


def test_fusion_de_deux_releves_vides():
    vide = pd.DataFrame(columns=intraday.COLONNES_RV)
    assert intraday.fusionner_releve(vide, vide).empty


# ---------------------------------------------------------------------------
# Magasin : écriture partitionnée puis relecture
# ---------------------------------------------------------------------------

def test_ecriture_partitionnee_puis_relecture(tmp_path, monkeypatch):
    monkeypatch.setattr(intraday.config, "INTRADAY_DIR", tmp_path / "intraday")
    df = pd.concat([_barres("2026-07-28", 6), _barres("2026-07-29", 6)])
    n = intraday.ecrire_partition("GC=F", "5m", df)
    assert n == 12
    assert intraday.journees_archivees("GC=F", "5m") == ["2026-07-28", "2026-07-29"]
    relu = intraday.lire("GC=F", "5m")
    assert len(relu) == 12
    assert relu.index.is_monotonic_increasing


def test_reecrire_une_journee_ne_duplique_pas(tmp_path, monkeypatch):
    """Chaque balayage réécrit la séance en cours : idempotence obligatoire."""
    monkeypatch.setattr(intraday.config, "INTRADAY_DIR", tmp_path / "intraday")
    intraday.ecrire_partition("AAPL", "5m", _barres("2026-07-28", 4))
    intraday.ecrire_partition("AAPL", "5m", _barres("2026-07-28", 7))
    relu = intraday.lire("AAPL", "5m")
    assert len(relu) == 7


def test_lire_depuis_une_borne(tmp_path, monkeypatch):
    monkeypatch.setattr(intraday.config, "INTRADAY_DIR", tmp_path / "intraday")
    intraday.ecrire_partition("AAPL", "5m", pd.concat([
        _barres("2026-07-27", 3), _barres("2026-07-28", 3), _barres("2026-07-29", 3)]))
    relu = intraday.lire("AAPL", "5m", depuis="2026-07-28")
    assert len(relu) == 6


def test_lire_un_titre_jamais_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(intraday.config, "INTRADAY_DIR", tmp_path / "intraday")
    assert intraday.lire("INEXISTANT", "5m").empty
    assert intraday.journees_archivees("INEXISTANT", "5m") == []


# ---------------------------------------------------------------------------
# Routage des fournisseurs
# ---------------------------------------------------------------------------

def test_routage_par_fournisseur():
    yahoos, binances, ecartes = intraday._router(
        ["AAPL", "BTCUSDT", "SNTS", "EURUSD=X", "GC=F"])
    assert binances == ["BTCUSDT"]
    assert ecartes == ["SNTS"]           # BRVM : quotidien par CSV, pas d'intraday
    assert yahoos == ["AAPL", "EURUSD=X", "GC=F"]


def test_capture_ne_leve_jamais(monkeypatch, tmp_path):
    """Un fournisseur en panne doit donner un bilan, pas une exception."""
    monkeypatch.setattr(intraday.config, "INTRADAY_DIR", tmp_path / "intraday")

    def tombe(*a, **k):
        raise RuntimeError("réseau coupé")

    monkeypatch.setattr(intraday.yahoo, "get_ohlcv_multi", tombe)
    monkeypatch.setattr(intraday.binance, "get_ohlcv", tombe)
    bilan = intraday.capturer(["AAPL", "BTCUSDT"], jours=2)
    assert bilan["titres"] == 0
    assert bilan["echecs"]


def test_capture_archive_ce_qui_repond(monkeypatch, tmp_path):
    monkeypatch.setattr(intraday.config, "INTRADAY_DIR", tmp_path / "intraday")
    monkeypatch.setattr(intraday.yahoo, "get_ohlcv_multi",
                        lambda syms, **k: {"AAPL": _barres("2026-07-28", 5)})
    bilan = intraday.capturer(["AAPL", "MSFT"], jours=2)
    assert bilan["titres"] == 1
    assert bilan["barres"] == 5
    assert bilan["echecs"] == ["MSFT"]   # absent du lot renvoyé, signalé
