"""Le plan doit être à la mesure de la durée de détention.

Régression protégée : le stop était calculé sans tenir compte de l'horizon.
Un plan à 5 séances héritait donc du stop d'un plan à 20 séances — une perte
laissée courir quatre fois plus longtemps que le pari lui-même, et un ratio
gain/risque ruiné (0,34 au lieu de 1,47 sur le cas réel qui l'a révélé).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import levels


@pytest.fixture
def marche_synthetique(monkeypatch):
    """Un actif à 100, ATR de 2, sans zone de support/résistance proche."""
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    prix = pd.Series(100.0, index=idx)
    df = pd.DataFrame({"open": prix, "high": prix + 1, "low": prix - 1,
                       "close": prix, "volume": 1_000.0}, index=idx)
    df["atr14"] = 2.0
    monkeypatch.setattr(levels.indicators, "enrich", lambda d: df)
    monkeypatch.setattr(levels, "get_ohlcv", lambda s, lookback_days=1825: df)
    monkeypatch.setattr(levels, "zones_proches",
                        lambda d: {"supports": [], "resistances": []})
    # **_ : le simulateur reçoit désormais `vol_cible` (volatilité prévue par
    # HAR). Un stub trop étroit fait tomber le test sur un TypeError qui
    # n'a rien à voir avec ce qu'il vérifie — la géométrie du stop.
    monkeypatch.setattr(levels.forecast, "projeter",
                        lambda d, horizon=20, **_: {
        "intervalle_80": [100 - horizon, 100 + horizon]})
    monkeypatch.setattr(levels.forecast, "proba_atteindre", lambda p, n: 30.0)
    monkeypatch.setattr(levels.forecast, "regime", lambda d: {"nom": "test"})
    monkeypatch.setattr(levels.events, "risque_evenement",
                        lambda s, h: {"concerne": False})
    # Depuis que har.vol_cible arbitre vers le modèle GKYZ (commité et
    # retenu), le laisser vivant ferait sortir CE test sur le réseau pour
    # chercher l'OHLC d'un symbole synthétique — la famille de fuite qui a
    # déjà coûté neuf minutes par déploiement.
    monkeypatch.setattr(levels.har, "vol_cible", lambda s: None)
    return df


def test_le_stop_se_resserre_quand_lhorizon_raccourcit(marche_synthetique):
    distances = {}
    for h in (5, 20, 60):
        p = levels.plan("TEST", sens="achat", horizon=h)
        distances[h] = p["entree"] - p["stop"]
    assert distances[5] < distances[20] < distances[60]


def test_le_stop_suit_la_racine_du_temps(marche_synthetique):
    """√(5/20) = 0,5 : le stop à 5 séances doit être moitié moins loin."""
    court = levels.plan("TEST", sens="achat", horizon=5)
    long_ = levels.plan("TEST", sens="achat", horizon=20)
    ratio = (court["entree"] - court["stop"]) / (long_["entree"] - long_["stop"])
    assert ratio == pytest.approx(np.sqrt(5 / 20), rel=0.02)


def test_lhorizon_de_reference_reste_a_deux_atr(marche_synthetique):
    """Garantit qu'on n'a pas déplacé le comportement historique à 20 séances."""
    p = levels.plan("TEST", sens="achat", horizon=20)
    assert p["entree"] - p["stop"] == pytest.approx(2 * 2.0, rel=1e-6)


def test_symetrie_a_la_vente(marche_synthetique):
    achat = levels.plan("TEST", sens="achat", horizon=5)
    vente = levels.plan("TEST", sens="vente", horizon=5)
    assert (vente["stop"] - vente["entree"]) == pytest.approx(
        achat["entree"] - achat["stop"], rel=1e-6)


def test_un_support_lointain_ne_fabrique_pas_un_stop_absurde(monkeypatch,
                                                             marche_synthetique):
    """Le garde-fou : un support à -30 % ne doit pas devenir le stop d'un
    trade de 5 séances."""
    monkeypatch.setattr(levels, "zones_proches",
                        lambda d: {"supports": [{"niveau": 70.0}],
                                   "resistances": []})
    p = levels.plan("TEST", sens="achat", horizon=5)
    distance = p["entree"] - p["stop"]
    plafond = 1.5 * 2.0 * np.sqrt(5 / 20) * 2.0   # 1,5 × mult × ATR
    assert distance <= plafond + 1e-6
    assert p["stop"] > 90, "stop encore trop éloigné pour 5 séances"
