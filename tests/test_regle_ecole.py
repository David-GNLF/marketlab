"""La « règle d'école » : une CANDIDATE se teste comme une grande.

Le DataFrame est construit de toutes pièces (le helper ne lit que close,
rsi14, ema20/50/200) : aucun cours réel, aucun réseau, et les fondamentaux
sont injectés — un signal d'école jugé sur des données qui bougent ne
testerait que la météo.
"""

import numpy as np
import pandas as pd
import pytest

from marketlab import decision, fundamentals


def _df(n=220, e20=106.0, e50=105.0, e200=100.0,
        closes_fin=(104.0, 104.0, 104.0, 106.0),
        rsi_fin=(35.0, 36.0, 37.0, 38.0, 39.0, 42.0)):
    close = np.full(n, 100.0)
    close[-len(closes_fin):] = closes_fin
    rsi = np.full(n, 55.0)
    rsi[-len(rsi_fin):] = rsi_fin
    return pd.DataFrame({"close": close, "rsi14": rsi,
                         "ema20": np.full(n, e20),
                         "ema50": np.full(n, e50),
                         "ema200": np.full(n, e200)})


@pytest.fixture(autouse=True)
def _fondamentaux_sains(monkeypatch):
    monkeypatch.setattr(fundamentals, "profil",
                        lambda s: {"croissance_ca": 0.12,
                                   "dette_sur_capitaux": 80.0})


def test_setup_achat_complet_note_maximale():
    # tendance (+20) + confirmation (+10) + croisement récent de la MM50
    # (+25) + RSI qui sort de la zone basse (+35) = +90
    assert decision._regle_ecole(_df(), "AAPL") == 90.0


def test_le_filtre_qualite_divise_une_note_acheteuse(monkeypatch):
    monkeypatch.setattr(fundamentals, "profil",
                        lambda s: {"croissance_ca": 0.01,
                                   "dette_sur_capitaux": 80.0})
    assert decision._regle_ecole(_df(), "AAPL") == 45.0


def test_setup_vente_symetrique_sans_filtre_qualite(monkeypatch):
    # tendance baissière, perte de la MM50, RSI qui retombe du surachat :
    # −90 — et les fondamentaux n'y changent RIEN (le filtre ne s'applique
    # qu'aux achats : on n'achète pas une entreprise fragile, mais sa
    # fragilité ne rend pas un signal vendeur plus vrai)
    monkeypatch.setattr(fundamentals, "profil",
                        lambda s: {"croissance_ca": 0.01,
                                   "dette_sur_capitaux": 500.0})
    df = _df(e20=94.0, e50=95.0, e200=100.0,
             closes_fin=(96.0, 96.0, 96.0, 94.0),
             rsi_fin=(75.0, 74.0, 73.0, 72.0, 71.0, 65.0))
    assert decision._regle_ecole(df, "AAPL") == -90.0


def test_etat_stable_ne_score_que_la_tendance():
    # au-dessus de la MM50 depuis toujours, RSI à 55 : pas d'ÉVÉNEMENT,
    # seulement l'état de tendance (+20 +10)
    df = _df(closes_fin=(106.0, 106.0, 106.0, 106.0),
             rsi_fin=(55.0,) * 6)
    assert decision._regle_ecole(df, "AAPL") == 30.0


def test_historique_court_rend_none():
    assert decision._regle_ecole(_df(n=150), "AAPL") is None


def test_un_titre_sans_fondamentaux_garde_sa_note(monkeypatch):
    def _pas_une_action(s):
        raise RuntimeError("pas une action")
    monkeypatch.setattr(fundamentals, "profil", _pas_une_action)
    assert decision._regle_ecole(_df(), "GC=F") == 90.0


# ---------------------------------------------------------------------------
# Le filtre qualité PERMUTE selon la classe : macro pour le forex,
# saisonnalité pour les matières — on ne calcule pas le PER du pétrole
# ---------------------------------------------------------------------------

def test_une_devise_desavouee_par_la_macro_est_divisee(monkeypatch):
    def _pas_une_action(s):
        raise RuntimeError("pas une action")
    monkeypatch.setattr(fundamentals, "profil", _pas_une_action)
    # surprise macro franchement négative (< −10) : la note acheteuse ÷ 2
    assert decision._regle_ecole(_df(), "EURUSD=X",
                                 qualite_classe=-16.3) == 45.0


def test_une_qualite_de_classe_neutre_ne_change_rien(monkeypatch):
    def _pas_une_action(s):
        raise RuntimeError("pas une action")
    monkeypatch.setattr(fundamentals, "profil", _pas_une_action)
    assert decision._regle_ecole(_df(), "GC=F", qualite_classe=-4.0) == 90.0
    assert decision._regle_ecole(_df(), "GC=F", qualite_classe=None) == 90.0


def test_le_filtre_de_classe_ignore_les_notes_vendeuses(monkeypatch):
    def _pas_une_action(s):
        raise RuntimeError("pas une action")
    monkeypatch.setattr(fundamentals, "profil", _pas_une_action)
    df = _df(e20=94.0, e50=95.0, e200=100.0,
             closes_fin=(96.0, 96.0, 96.0, 94.0),
             rsi_fin=(75.0, 74.0, 73.0, 72.0, 71.0, 65.0))
    assert decision._regle_ecole(df, "EURUSD=X",
                                 qualite_classe=-50.0) == -90.0
