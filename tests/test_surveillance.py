"""Surveillance des positions ouvertes : signaler une fois, se réarmer, jamais casser.

HERMÉTIQUES PAR CONSTRUCTION : la fixture autouse neutralise tout ce qui
touche au réseau ou aux relevés commités (régimes → VIX, sauts → CSV,
concentration → cours). Chaque test ne réactive QUE le garde qu'il éprouve —
même contrat que test_dimensionnement et test_risque_portefeuille, les deux
familles de fuite déjà payées sur ce projet.
"""

import numpy as np
import pandas as pd
import pytest

from marketlab import surveillance as sv


@pytest.fixture(autouse=True)
def _sans_monde_exterieur(monkeypatch):
    monkeypatch.setattr(sv.regimes, "avis_suspendu", lambda: None)
    monkeypatch.setattr(sv.microstructure, "part_sauts", lambda s: None)
    monkeypatch.setattr(sv.risque_portefeuille, "matrice_stress",
                        lambda symboles, jours=750: None)
    monkeypatch.setattr(sv.correlations, "rendements",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("réseau interdit dans les tests")))
    # le relevé d'options COMMITÉ changerait le comportement des tests : la
    # pression vendeuse est neutralisée par défaut, réactivée test par test
    monkeypatch.setattr(sv, "_implicite_du_titre", lambda s: None)


def _position(symbole="AAPL", **extra):
    base = {"symbole": symbole, "sens": "long", "marge": 100.0, "levier": 2,
            "notionnel": 200.0, "prix_entree": 100.0,
            "ouvert_le": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")}
    base.update(extra)
    return base


def _compte(*positions, horizon=20):
    return {"nom": "test", "horizon": horizon, "positions": list(positions)}


# ------------------------------------------------------------------ régime

def test_regime_suspendu_signale_une_fois_puis_se_tait(monkeypatch):
    monkeypatch.setattr(sv.regimes, "avis_suspendu",
                        lambda: {"regime": "tendu"})
    compte = _compte(_position())
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "tendu" in premieres[0]
    assert sv.examiner(compte) == []


def test_regime_se_rearme_quand_le_marche_se_calme(monkeypatch):
    etats = iter([{"regime": "tendu"}, None, {"regime": "tendu"}])
    reponses = {"v": None}

    def _suivant():
        return reponses["v"]

    monkeypatch.setattr(sv.regimes, "avis_suspendu", _suivant)
    compte = _compte(_position())
    reponses["v"] = next(etats)
    assert len(sv.examiner(compte)) == 1      # épisode 1 : signalé
    reponses["v"] = next(etats)
    assert sv.examiner(compte) == []          # accalmie : rien, état effacé
    reponses["v"] = next(etats)
    assert len(sv.examiner(compte)) == 1      # épisode 2 : re-signalé


# ------------------------------------------------------------------ sauts

def test_sauts_au_dessus_du_seuil_signales_une_fois(monkeypatch):
    monkeypatch.setattr(sv.microstructure, "part_sauts",
                        lambda s: {"part_saut": 0.35, "n_seances": 40})
    compte = _compte(_position("KC=F"))
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "35 %" in premieres[0]
    assert sv.examiner(compte) == []


def test_sauts_sous_le_seuil_inertes():
    compte = _compte(_position())
    # part_sauts renvoie None (fixture) : aucun bruit
    assert sv.examiner(compte) == []


# ---------------------------------------------------------------- portage

def test_portage_signale_chaque_palier_une_seule_fois():
    p = _position(frais_portage_cumules=1.2)     # 1,2 % de la mise
    compte = _compte(p)
    assert len(sv.examiner(compte)) == 1
    assert sv.examiner(compte) == []             # même palier : silence
    p["frais_portage_cumules"] = 2.4             # palier 2 % franchi
    assert len(sv.examiner(compte)) == 1


def test_portage_sous_le_premier_palier_muet():
    compte = _compte(_position(frais_portage_cumules=0.5))
    assert sv.examiner(compte) == []


# ---------------------------------------------------------------- horizon

def test_horizon_ecoule_signale_une_fois():
    vieux = (pd.Timestamp.now() - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    compte = _compte(_position(ouvert_le=vieux), horizon=20)
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "20" in premieres[0]
    assert sv.examiner(compte) == []


def test_horizon_non_ecoule_muet():
    compte = _compte(_position(), horizon=20)
    assert sv.examiner(compte) == []


# ---------------------------------------- concentration entre positions détenues

def _matrice(symboles, rho):
    m = pd.DataFrame(np.eye(len(symboles)), index=symboles, columns=symboles)
    for i, a in enumerate(symboles):
        for b in symboles[i + 1:]:
            m.loc[a, b] = m.loc[b, a] = rho
    return m


def test_deux_detenues_correlees_meme_sens_signalees_une_fois(monkeypatch):
    monkeypatch.setattr(sv.risque_portefeuille, "matrice_stress",
                        lambda s, jours=750: _matrice(s, 0.9))
    compte = _compte(_position("EURUSD=X"), _position("GBPUSD=X"))
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "MÊME pari" in premieres[0]
    assert sv.examiner(compte) == []             # paire déjà signalée


def test_sens_opposes_sur_actifs_correles_ne_sont_pas_le_meme_pari(monkeypatch):
    # corrélation +0,9 mais un achat et une vente : paris OPPOSÉS (−0,9),
    # aucune alerte — le sens compte, comme partout dans le module de risque
    monkeypatch.setattr(sv.risque_portefeuille, "matrice_stress",
                        lambda s, jours=750: _matrice(s, 0.9))
    compte = _compte(_position("EURUSD=X"),
                     _position("GBPUSD=X", sens="short"))
    assert sv.examiner(compte) == []


def test_paire_assainie_se_rearme(monkeypatch):
    monkeypatch.setattr(sv.risque_portefeuille, "matrice_stress",
                        lambda s, jours=750: _matrice(s, 0.9))
    compte = _compte(_position("EURUSD=X"), _position("GBPUSD=X"))
    assert len(sv.examiner(compte)) == 1
    compte["positions"] = compte["positions"][:1]    # une des deux fermée
    assert sv.examiner(compte) == []
    assert "surveillance_paires" not in compte       # état nettoyé
    compte["positions"].append(_position("GBPUSD=X"))
    assert len(sv.examiner(compte)) == 1             # re-signalé


def test_co_chute_prend_le_relais_quand_la_correlation_se_tait(monkeypatch):
    # corrélation discrète (0,3) mais queues communes : le second détecteur
    # doit parler — avec SA raison, pas celle de la corrélation
    monkeypatch.setattr(sv.risque_portefeuille, "matrice_stress",
                        lambda s, jours=750: _matrice(s, 0.3))
    rendements = pd.DataFrame(
        np.random.default_rng(7).normal(0, 0.01, (500, 2)),
        columns=["AAPL", "NVDA"])
    monkeypatch.setattr(sv.correlations, "rendements",
                        lambda *a, **k: rendements)
    monkeypatch.setattr(sv.risque_portefeuille, "co_chute",
                        lambda a, b: 0.72)
    compte = _compte(_position("AAPL"), _position("NVDA"))
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "co-chute" in premieres[0]


# -------------------------------------------------------- pression vendeuse

def test_pression_vendeuse_prend_sa_reference_puis_alerte_sur_creusement(
        monkeypatch):
    mesures = {"v": {"iv": 30.0, "skew": 1.0}}
    monkeypatch.setattr(sv, "_implicite_du_titre", lambda s: mesures["v"])
    compte = _compte(_position("NVDA"))
    assert sv.examiner(compte) == []          # 1er passage : référence prise
    mesures["v"] = {"iv": 32.0, "skew": 7.0}  # skew +6 pts depuis l'entrée
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "skew" in premieres[0]
    assert sv.examiner(compte) == []          # déjà signalé : silence
    mesures["v"] = {"iv": 32.0, "skew": 2.0}  # la pression retombe
    assert sv.examiner(compte) == []          # ...et l'état se réarme
    mesures["v"] = {"iv": 32.0, "skew": 8.0}
    assert len(sv.examiner(compte)) == 1      # nouveau creusement : re-signalé


def test_pression_vendeuse_iv_qui_bondit(monkeypatch):
    mesures = {"v": {"iv": 20.0, "skew": 0.0}}
    monkeypatch.setattr(sv, "_implicite_du_titre", lambda s: mesures["v"])
    compte = _compte(_position("NVDA"))
    sv.examiner(compte)                       # référence : IV 20 %
    mesures["v"] = {"iv": 32.0, "skew": 0.0}  # ×1,6 : le marché price violent
    premieres = sv.examiner(compte)
    assert len(premieres) == 1 and "volatilité implicite" in premieres[0]


def test_titre_sans_options_reste_muet():
    # la fixture renvoie None (pas de chaînes d'options relevées) : silence
    compte = _compte(_position("EURUSD=X"))
    assert sv.examiner(compte) == []


# ------------------------------------------------------------------ pannes

def test_les_gardes_en_panne_s_effacent(monkeypatch):
    def _casse(*a, **k):
        raise RuntimeError("panne simulée")
    monkeypatch.setattr(sv.regimes, "avis_suspendu", _casse)
    monkeypatch.setattr(sv.microstructure, "part_sauts", _casse)
    monkeypatch.setattr(sv.risque_portefeuille, "matrice_stress", _casse)
    compte = _compte(_position("EURUSD=X"), _position("GBPUSD=X"))
    assert sv.examiner(compte) == []             # jamais d'exception qui remonte
