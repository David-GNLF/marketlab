"""Journal des verdicts de la chaîne : consigner, mûrir, juger — sans réseau.

Le rejeu et le bilan travaillent sur des cours SYNTHÉTIQUES injectés par
monkeypatch : un test dont le verdict dépend des cours du jour ne teste rien.
Le chemin du journal est détourné vers tmp_path — le CSV commité ne doit
jamais influencer une valeur attendue (leçon des tests de coûts et de sauts).
"""

import numpy as np
import pandas as pd
import pytest

from marketlab import journal_chaine as jc


@pytest.fixture(autouse=True)
def _journal_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(jc, "JOURNAL_PATH", tmp_path / "journal_chaine.csv")


def _dossier(symbole="AAPL", date="2026-08-01", horizon=20, retenue=True,
             etapes=(), entree=100.0, stop=94.0, objectif=112.0, cout=0.4):
    return {
        "symbole": symbole, "date": date, "horizon": horizon,
        "dimensionnement": {"retenue": retenue, "mise": 77.0 if retenue else 0.0,
                            "etapes": list(etapes)},
        "plan": {"entree": entree, "stop": stop, "objectif": objectif,
                 "esperance_nette_%": 1.2,
                 "couts": {"seuil_actif_%": cout}},
    }


# ------------------------------------------------------------- journalisation

def test_journalise_retenues_et_ecartees():
    n = jc.journaliser([
        _dossier("AAPL", retenue=True),
        _dossier("EURUSD=X", retenue=False,
                 etapes=["ÉCARTÉE — l'espérance ne survit pas aux frais : ..."]),
    ])
    assert n == 2
    journal = pd.read_csv(jc.JOURNAL_PATH)
    par_symbole = journal.set_index("symbole")
    assert par_symbole.loc["AAPL", "retenue"] == 1
    assert par_symbole.loc["AAPL", "etape_fatale"] != par_symbole.loc[
        "EURUSD=X", "etape_fatale"]
    assert par_symbole.loc["EURUSD=X", "etape_fatale"] == "frais"


@pytest.mark.parametrize("texte, attendu", [
    ("ÉCARTÉE — l'espérance ne survit pas aux frais : 0.35 % attendus", "frais"),
    ("ÉCARTÉE — avis directionnel suspendu en marché tendu", "regime"),
    ("distance au stop inconnue : dimensionnement impossible", "stop_inconnu"),
    ("mise résiduelle sous 10 $ : position écartée", "mise_min"),
    ("aucune équité ou aucun plan : rien à dimensionner", "sans_plan"),
    ("un motif inédit que personne n'a prévu", "autre"),
])
def test_etape_fatale_suit_le_texte_de_la_chaine(texte, attendu):
    assert jc._etape_fatale({"retenue": False, "etapes": [texte]}) == attendu


def test_le_premier_ecrit_gagne():
    jc.journaliser([_dossier("AAPL", retenue=True)])
    jc.journaliser([_dossier("AAPL", retenue=False,
                             etapes=["ÉCARTÉE — l'espérance ne survit pas "
                                     "aux frais"])])
    journal = pd.read_csv(jc.JOURNAL_PATH)
    assert len(journal) == 1 and journal["retenue"].iloc[0] == 1


def test_dossier_sans_plan_ou_en_erreur_ignore():
    assert jc.journaliser([{"symbole": "X", "erreur": "boom"},
                           {"symbole": "Y", "date": "2026-08-01",
                            "horizon": 20, "plan": None,
                            "dimensionnement": {"retenue": True}},
                           _dossier("Z", retenue=True) | {
                               "dimensionnement": {"erreur": "cassé"}}]) == 0
    assert not jc.JOURNAL_PATH.exists()


def test_dossier_sans_chaine_ignore():
    # un dossier produit AVANT le branchement de la chaîne n'a pas de clé
    # « dimensionnement » : le consigner « écartée » fausserait la comparaison
    # (constaté sur le verdicts.json local du 2026-07-28)
    assert jc.journaliser([_dossier("AAPL") | {"dimensionnement": {}}]) == 0
    assert not jc.JOURNAL_PATH.exists()


# --------------------------------------------------------------------- rejeu

def _cours(closes, bas=None, hauts=None, debut="2026-08-03"):
    idx = pd.bdate_range(debut, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "close": closes,
        "low": np.asarray(bas, dtype=float) if bas is not None else closes - 0.5,
        "high": np.asarray(hauts, dtype=float) if hauts is not None
        else closes + 0.5,
    }, index=idx)


def test_rejeu_stop_touche_en_premier():
    df = _cours([100, 99, 96], bas=[99, 98, 93.5])       # séance 3 : bas 93,5
    r = jc._rejouer(df, "2026-08-01", 100.0, 94.0, 112.0, horizon=3)
    assert r["issue"] == "stop"
    assert r["rendement_brut_%"] == pytest.approx(-6.0)


def test_rejeu_stop_prioritaire_si_les_deux_touches_le_meme_jour():
    # même hypothèse prudente que la tenue des comptes du robot
    df = _cours([100, 105, 100], bas=[99, 93, 99], hauts=[101, 113, 101])
    r = jc._rejouer(df, "2026-08-01", 100.0, 94.0, 112.0, horizon=3)
    assert r["issue"] == "stop"


def test_rejeu_objectif_touche():
    df = _cours([100, 108, 110], hauts=[101, 112.5, 111])
    r = jc._rejouer(df, "2026-08-01", 100.0, 94.0, 112.0, horizon=3)
    assert r["issue"] == "objectif"
    assert r["rendement_brut_%"] == pytest.approx(12.0)


def test_rejeu_echeance_au_dernier_cours():
    df = _cours([100, 101, 103])
    r = jc._rejouer(df, "2026-08-01", 100.0, 94.0, 112.0, horizon=3)
    assert r["issue"] == "echeance"
    assert r["rendement_brut_%"] == pytest.approx(3.0)


def test_rejeu_immature_renvoie_none():
    df = _cours([100, 101])                              # 2 séances pour 3
    assert jc._rejouer(df, "2026-08-01", 100.0, 94.0, 112.0, horizon=3) is None


# --------------------------------------------------------------------- bilan

def test_bilan_sans_journal_le_dit():
    b = jc.bilan()
    assert b["murs"] == 0 and "se remplit" in b["lecture"]


def test_bilan_trop_tot_ne_conclut_pas(monkeypatch):
    jc.journaliser([_dossier("AAPL", retenue=True, horizon=3)])
    monkeypatch.setattr("marketlab.data.get_ohlcv",
                        lambda s, lookback_days=0: _cours([100, 101, 103]))
    b = jc.bilan()
    assert b["murs"] == 1
    assert "Trop tôt" in b["lecture"]
    assert b["par_groupe"]["retenues"]["n"] == 1


def test_bilan_compare_net_des_couts_connus_a_la_decision(monkeypatch):
    # 12 verdicts mûrs : 6 retenues qui finissent à +3 % brut, 6 écartées aux
    # frais qui finissent à −6 % (stop) — le filtre doit être déclaré payant,
    # et les rendements NET doivent porter le coût enregistré (0,4 %)
    dossiers = []
    for i in range(6):
        dossiers.append(_dossier(f"R{i}", retenue=True, horizon=3))
        dossiers.append(_dossier(
            f"E{i}", retenue=False, horizon=3,
            etapes=["ÉCARTÉE — l'espérance ne survit pas aux frais"]))
    jc.journaliser(dossiers)

    def _ohlcv(symbole, lookback_days=0):
        if symbole.startswith("R"):
            return _cours([100, 101, 103])               # échéance +3 %
        return _cours([100, 99, 96], bas=[99, 98, 93.5])  # stop −6 %
    monkeypatch.setattr("marketlab.data.get_ohlcv", _ohlcv)

    b = jc.bilan()
    assert b["murs"] == 12
    assert b["par_groupe"]["retenues"]["rendement_net_moyen_%"] == \
        pytest.approx(3.0 - 0.4, abs=0.01)
    assert b["par_groupe"]["ecartees_frais"]["rendement_net_moyen_%"] == \
        pytest.approx(-6.0 - 0.4, abs=0.01)
    assert "gagne sa vie" in b["lecture"]


def test_bilan_previent_quand_les_ecartees_font_mieux(monkeypatch):
    dossiers = []
    for i in range(6):
        dossiers.append(_dossier(f"R{i}", retenue=True, horizon=3))
        dossiers.append(_dossier(
            f"E{i}", retenue=False, horizon=3,
            etapes=["ÉCARTÉE — avis directionnel suspendu en marché tendu"]))
    jc.journaliser(dossiers)

    def _ohlcv(symbole, lookback_days=0):
        if symbole.startswith("R"):
            return _cours([100, 100, 100.5])             # retenues : +0,5 %
        return _cours([100, 104, 108], hauts=[101, 105, 109])
    monkeypatch.setattr("marketlab.data.get_ohlcv", _ohlcv)

    b = jc.bilan()
    assert b["lecture"].startswith("ATTENTION")


def test_bilan_titre_sans_cours_compte_inverifiable(monkeypatch):
    jc.journaliser([_dossier("MORT", retenue=True, horizon=3)])

    def _casse(symbole, lookback_days=0):
        raise RuntimeError("titre retiré de la cote")
    monkeypatch.setattr("marketlab.data.get_ohlcv", _casse)
    b = jc.bilan()
    assert b["inverifiables"] == 1 and b["murs"] == 0
