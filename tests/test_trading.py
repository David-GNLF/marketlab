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


# --- rattrapage des séances manquées -----------------------------------------
#
# INCIDENT DES 07-09/08/2026 : trois nuits sans tenue (publication en panne),
# et l'ancienne tenue n'aurait confronté les stops qu'à la DERNIÈRE séance au
# retour — un stop franchi jeudi serait resté ouvert comme si de rien n'était.
# La tenue rejoue désormais chaque séance manquée, dans l'ordre chronologique.

def _calendrier(monkeypatch, seances):
    """Un vrai calendrier de séances : {date: (haut, bas, clôture)}."""
    df = pd.DataFrame(
        [{"high": h, "low": b, "close": c} for (h, b, c) in seances.values()],
        index=pd.to_datetime(list(seances)))
    monkeypatch.setattr(rt, "get_ohlcv", lambda s, lookback_days=30: df)


def test_un_stop_franchi_pendant_la_panne_est_rattrape(monkeypatch):
    _calendrier(monkeypatch, {
        "2026-08-06": (105.0, 99.0, 100.0),
        "2026-08-07": (105.0, 93.0, 100.0),   # le stop passe PENDANT la panne
        "2026-08-08": (105.0, 99.0, 100.0),
    })
    c = _compte(positions=[_position(stop=96.0)],
                equity=[["2026-08-06 22:30", 1000.0]])   # dernière tenue : le 6
    evenements = rt.tenir_compte(c)
    assert "stop touché" in evenements[0]
    assert c["historique"][0]["sortie"] == 96.0


def test_la_premiere_seance_touchee_gagne_chronologiquement(monkeypatch):
    # objectif atteint le 7, stop franchi le 8 : la tenue rejouée sort à
    # l'OBJECTIF — comme si elle avait eu lieu le soir du 7
    _calendrier(monkeypatch, {
        "2026-08-07": (104.5, 99.0, 104.0),
        "2026-08-08": (100.0, 93.0, 95.0),
    })
    c = _compte(positions=[_position(stop=96.0, objectif=104.0)],
                equity=[["2026-08-06 22:30", 1000.0]])
    assert "objectif atteint" in rt.tenir_compte(c)[0]


def test_une_position_nee_apres_la_panne_ignore_l_avant(monkeypatch):
    _calendrier(monkeypatch, {
        "2026-08-07": (105.0, 93.0, 100.0),   # aurait franchi le stop…
        "2026-08-09": (105.0, 99.0, 100.0),
    })
    c = _compte(positions=[_position(stop=96.0, ouvert_le="2026-08-09 10:00")],
                equity=[["2026-08-06 22:30", 1000.0]])
    assert rt.tenir_compte(c) == []           # …mais avant sa naissance


def test_un_ordre_touche_pendant_la_panne_sexecute(monkeypatch):
    _calendrier(monkeypatch, {
        "2026-08-07": (105.0, 89.5, 100.0),   # l'achat limite 90 est touché
        "2026-08-08": (105.0, 99.0, 100.0),
    })
    c = _compte(ordres=[_ordre("long", "limite", 90.0,
                               expire_le="2027-01-01")],
                equity=[["2026-08-06 22:30", 1000.0]])
    assert "exécuté" in rt.executer_ordres(c)[0]


# --- la tenue ne dépend pas de la génération ---------------------------------

def test_verdicts_absents_donnent_tenue_seule(tmp_path, monkeypatch):
    """Avant, verdicts.json absent arrêtait TOUT le script : trois nuits de
    publication en panne ont donc aussi gelé les stops de tous les comptes."""
    monkeypatch.setattr(rt, "VERDICTS_LOCAL", tmp_path / "absent.json")
    assert rt.charger_verdicts_publies() == {}


def test_verdicts_illisibles_donnent_tenue_seule(tmp_path, monkeypatch):
    f = tmp_path / "verdicts.json"
    f.write_text("{cassé", encoding="utf-8")
    monkeypatch.setattr(rt, "VERDICTS_LOCAL", f)
    assert rt.charger_verdicts_publies() == {}


# ---------------------------------------------------------------------------
# Stop suiveur : opt-in, resserre seulement, jamais l'inverse
# ---------------------------------------------------------------------------

def _pos_suiveur(**extra):
    base = {"symbole": "TEST", "sens": "long", "marge": 50.0, "levier": 2,
            "quantite": 1.0, "prix_entree": 100.0, "stop": 90.0,
            "objectif": None, "ouvert_le": "2026-08-01 10:00",
            "suiveur": True, "suiveur_distance_pct": 6.0}
    base.update(extra)
    return base


def test_stop_suiveur_resserre_apres_une_seance_survivante():
    # clôture 100, écart gardé 6 % : le stop monte de 90 à 94
    c = _compte(positions=[_pos_suiveur()])
    evenements = rt.tenir_compte(c)
    assert c["positions"][0]["stop"] == pytest.approx(94.0)
    assert any("suiveur" in e for e in evenements)


def test_stop_suiveur_ne_recule_jamais():
    # stop déjà à 95 : le candidat (94) est PLUS BAS — rien ne bouge
    c = _compte(positions=[_pos_suiveur(stop=94.5)])
    evenements = rt.tenir_compte(c)
    assert c["positions"][0]["stop"] == pytest.approx(94.5)
    assert not any("suiveur" in e for e in evenements)


def test_stop_suiveur_short_descend():
    # pour un vendeur, « resserrer » = descendre : 106 → 104 (clôture 100 + 4 %)
    c = _compte(positions=[_pos_suiveur(sens="short", stop=106.0,
                                        suiveur_distance_pct=4.0)])
    rt.tenir_compte(c)
    assert c["positions"][0]["stop"] == pytest.approx(104.0)


def test_sans_drapeau_suiveur_le_stop_ne_bouge_pas():
    # les robots ne posent jamais ce drapeau : leurs règles d'expérience
    # restent intactes
    c = _compte(positions=[_pos_suiveur(suiveur=False)])
    rt.tenir_compte(c)
    assert c["positions"][0]["stop"] == pytest.approx(90.0)


def test_un_stop_touche_prime_sur_le_suiveur():
    # bas de séance 95 : le stop à 96 est touché, la position se ferme —
    # le suiveur n'a rien à resserrer sur une position morte
    c = _compte(positions=[_pos_suiveur(stop=96.0)])
    evenements = rt.tenir_compte(c)
    assert c["positions"] == []
    assert any("stop touché" in e for e in evenements)
