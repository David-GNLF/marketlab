"""Tests du moteur de trading virtuel.

Ces règles décident d'argent (virtuel aujourd'hui, mais c'est le même moteur
qui informe des décisions réelles) : elles doivent être vérifiées à chaque
modification, pas seulement le jour où on les écrit.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

import robot_trading as rt


@pytest.fixture(autouse=True)
def cours_figes(monkeypatch):
    """Séance figée : plus haut 105, plus bas 95, clôture 100."""
    monkeypatch.setattr(rt, "get_ohlcv", lambda s, lookback_days=30:
                        pd.DataFrame([{"high": 105.0, "low": 95.0,
                                       "close": 100.0}]))
    monkeypatch.setattr(rt, "_cours_publie", lambda s: 100.0)


def _ordre(sens, type_, prix, **extra):
    return {"id": "o1", "symbole": "TEST", "sens": sens, "type": type_,
            "prix": prix, "marge": 50.0, "levier": 4, "stop": None,
            "objectif": None, "cree_le": "2026-07-01 10:00", **extra}


def _compte(**extra):
    base = {"nom": "t", "solde": 950.0, "positions": [], "ordres": [],
            "historique": [], "capital_initial": 1000.0}
    base.update(extra)
    return base


# --- déclenchement des ordres en attente -------------------------------------

@pytest.mark.parametrize("sens,type_,prix,attendu", [
    ("long", "limite", 96.0, True),    # le marché descend jusqu'au prix
    ("long", "limite", 90.0, False),
    ("long", "stop", 104.0, True),     # le marché monte jusqu'au prix
    ("long", "stop", 110.0, False),
    ("short", "limite", 104.0, True),
    ("short", "limite", 110.0, False),
    ("short", "stop", 96.0, True),
    ("short", "stop", 90.0, False),
])
def test_declenchement_ordres(sens, type_, prix, attendu):
    c = _compte(ordres=[_ordre(sens, type_, prix)])
    rt.executer_ordres(c)
    assert (len(c["positions"]) == 1) is attendu
    # un ordre non déclenché doit rester en attente, jamais disparaître
    assert (len(c["ordres"]) == 0) is attendu


def test_prix_execution_inclut_le_spread_defavorable():
    c = _compte(ordres=[_ordre("long", "limite", 96.0)])
    rt.executer_ordres(c)
    assert c["positions"][0]["prix_entree"] == pytest.approx(
        96.0 * (1 + rt.SPREAD_PCT / 100))


def test_ordre_expire_rend_la_mise_et_ne_sexecute_pas():
    hier = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    # prix atteignable : sans l'échéance, cet ordre s'exécuterait
    c = _compte(ordres=[_ordre("long", "limite", 96.0, expire_le=hier)])
    rt.executer_ordres(c)
    assert c["positions"] == []
    assert c["ordres"] == []
    assert c["solde"] == pytest.approx(1000.0)


def test_ordre_encore_valide_survit():
    demain = (pd.Timestamp.now() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    c = _compte(ordres=[_ordre("long", "limite", 90.0, expire_le=demain)])
    rt.executer_ordres(c)
    assert len(c["ordres"]) == 1
    assert c["solde"] == pytest.approx(950.0)


# --- tenue des positions ------------------------------------------------------

def _position(**extra):
    p = {"symbole": "TEST", "sens": "long", "marge": 100.0, "levier": 5,
         "notionnel": 500.0, "quantite": 5.0, "prix_entree": 100.0,
         "stop": None, "objectif": None, "ouvert_le": "2026-07-01 10:00"}
    p.update(extra)
    return p


def test_stop_prime_sur_objectif_le_meme_jour():
    """Hypothèse prudente : si la séance touche les deux, le stop d'abord."""
    c = _compte(positions=[_position(stop=96.0, objectif=104.0)])
    evenements = rt.tenir_compte(c)
    assert "stop touché" in evenements[0]
    assert c["historique"][0]["sortie"] == 96.0


def test_liquidation_prime_sur_tout():
    # levier 5 -> liquidation vers 80 ; la séance descend à 95 seulement
    c = _compte(positions=[_position(levier=20, stop=90.0)])
    evenements = rt.tenir_compte(c)
    assert "LIQUIDATION" in evenements[0]
    assert c["solde"] >= 950.0          # jamais de solde négatif


def test_objectif_atteint_si_le_stop_ne_lest_pas():
    c = _compte(positions=[_position(stop=90.0, objectif=104.0)])
    evenements = rt.tenir_compte(c)
    assert "objectif atteint" in evenements[0]


# --- frais de portage ---------------------------------------------------------

def test_portage_facture_seulement_la_part_empruntee():
    c = _compte(solde=900.0, positions=[_position()])
    rt.facturer_portage(c)
    attendu = (500.0 - 100.0) * rt.TAUX_PORTAGE_ANNUEL / 365
    assert 900.0 - c["solde"] == pytest.approx(attendu)


def test_portage_non_facture_deux_fois_le_meme_jour():
    c = _compte(solde=900.0, positions=[_position()])
    rt.facturer_portage(c)
    solde = c["solde"]
    assert rt.facturer_portage(c) == []
    assert c["solde"] == solde


def test_pas_de_portage_sans_levier():
    c = _compte(solde=900.0, positions=[_position(levier=1, notionnel=100.0)])
    assert rt.facturer_portage(c) == []
    assert c["solde"] == 900.0


# --- équité : LA définition qui doit être identique partout -------------------

def test_equite_compte_la_marge_reservee_des_ordres():
    """Régression : un ordre en attente avait « disparu » de l'équité, ce qui
    faisait diverger la page trading et le panneau admin."""
    c = _compte(solde=950.0, ordres=[_ordre("long", "limite", 90.0)])
    assert rt._equite(c) == pytest.approx(1000.0)


def test_equite_compte_marge_et_pnl_des_positions():
    c = _compte(solde=900.0, positions=[_position(prix_entree=90.0)])
    # 900 cash + 100 marge + (100-90)*5 de plus-value = 1050
    assert rt._equite(c) == pytest.approx(1050.0)


def test_equite_dun_compte_vierge_vaut_le_capital():
    assert rt._equite(_compte(solde=1000.0)) == pytest.approx(1000.0)
