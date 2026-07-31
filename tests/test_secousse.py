"""Règle 8 — secousse intraséance, sur barres de 5 minutes. Sans réseau."""

import numpy as np
import pandas as pd

from marketlab import alerts


def _barres(rendements, jour="2026-07-28", depart=100.0):
    """Barres consécutives d'UNE MÊME séance, à partir de rendements log.

    Pas de 1 minute et non 5 : il faut plus de 320 barres pour dépasser la
    fenêtre de référence, et 401 barres de 5 minutes déborderaient sur le
    lendemain — la règle les verrait alors comme deux séances. La règle ne
    dépend pas de la durée réelle d'une barre, seulement de leur succession.
    """
    idx = pd.date_range(f"{jour} 13:30", periods=len(rendements) + 1, freq="1min")
    closes = depart * np.exp(np.concatenate([[0.0], np.cumsum(rendements)]))
    return pd.DataFrame({"close": closes}, index=idx)


def _calme(n, ampleur=0.0005, graine=5):
    return list(np.random.default_rng(graine).normal(0, ampleur, n))


def _lancer(barres_par_titre, monkeypatch, symboles=("AAPL",)):
    """Exécute la seule règle 8 et renvoie les messages produits."""
    monkeypatch.setattr(alerts.intraday, "lire",
                        lambda sym, *a, **k: barres_par_titre.get(sym, pd.DataFrame()))
    etat = {"evenements": []}
    return alerts._regle_secousse(list(symboles), etat), etat


def test_seuil_mesure_et_non_suppose():
    """8σ vient d'une mesure sur 365 059 barres : à 3σ, la règle produirait
    95 alertes par jour. Le seuil ne doit pas dériver sans nouvelle mesure."""
    assert alerts.SEUIL_Z_SECOUSSE == 8.0
    assert alerts.SEUIL_Z_SECOUSSE > alerts.SEUIL_Z_FLASH


def test_une_secousse_declenche(monkeypatch):
    rendements = _calme(400) + [0.05]  # ~100σ sur une agitation de 0,05 %
    msgs, etat = _lancer({"AAPL": _barres(rendements)}, monkeypatch)
    assert len(msgs) == 1
    texte, urgent, meta = msgs[0]
    assert urgent is True                      # sonne même en silencieux
    assert "SECOUSSE" in texte
    assert meta["regle"] == "secousse_intraseance"
    assert meta["sens"] == "hausse"
    assert etat["evenements"] == ["secousse|AAPL|2026-07-28"]


def test_un_marche_calme_ne_declenche_pas(monkeypatch):
    msgs, _ = _lancer({"AAPL": _barres(_calme(400))}, monkeypatch)
    assert msgs == []


def test_un_decrochage_est_signale_comme_tel(monkeypatch):
    msgs, _ = _lancer({"AAPL": _barres(_calme(400) + [-0.05])}, monkeypatch)
    assert msgs[0][2]["sens"] == "baisse"
    assert "décrochage" in msgs[0][0]


def test_une_seule_alerte_par_titre_et_par_seance(monkeypatch):
    """Les barres qui suivent un choc sont elles aussi hors norme : les
    enchaîner transformerait un événement en avalanche de notifications."""
    barres = _barres(_calme(400) + [0.05, 0.05])
    monkeypatch.setattr(alerts.intraday, "lire", lambda *a, **k: barres)
    etat = {"evenements": ["secousse|AAPL|2026-07-28"]}
    assert alerts._regle_secousse(["AAPL"], etat) == []


def test_le_saut_de_nuit_nest_pas_une_secousse(monkeypatch):
    """Deux séances éloignées de 40 % : le trou entre elles n'est pas un
    mouvement intraséance et ne doit rien déclencher."""
    j1 = _barres(_calme(400), jour="2026-07-27", depart=100.0)
    j2 = _barres(_calme(30), jour="2026-07-28", depart=140.0)
    msgs, _ = _lancer({"AAPL": pd.concat([j1, j2])}, monkeypatch)
    assert msgs == []


def test_historique_trop_court(monkeypatch):
    """Sans fenêtre de référence, pas de mesure — et surtout pas de repli sur
    un sigma inventé."""
    msgs, _ = _lancer({"AAPL": _barres(_calme(50) + [0.05])}, monkeypatch)
    assert msgs == []


def test_titre_sans_barres(monkeypatch):
    msgs, _ = _lancer({}, monkeypatch)
    assert msgs == []


def test_agitation_nulle_ne_divise_pas_par_zero(monkeypatch):
    """Un titre figé (marché fermé, cotation suspendue) a un sigma nul."""
    msgs, _ = _lancer({"AAPL": _barres([0.0] * 400 + [0.01])}, monkeypatch)
    assert msgs == []


def test_plusieurs_titres_secoues_le_meme_jour(monkeypatch):
    """Cas RÉEL du 17/06 : EURUSD, AUDUSD, GBPUSD, USDCHF et l'or secoués
    ensemble par un choc dollar. Chacun doit produire son alerte."""
    secoue = _barres(_calme(400) + [0.05])
    msgs, etat = _lancer({"EURUSD=X": secoue, "AUDUSD=X": secoue,
                          "GC=F": secoue}, monkeypatch,
                         symboles=("EURUSD=X", "AUDUSD=X", "GC=F"))
    assert len(msgs) == 3
    assert len(etat["evenements"]) == 3
