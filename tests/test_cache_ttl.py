"""Le délai de validité du cache doit être réglable par la veille.

Régression protégée : les barres quotidiennes sont mises en cache 12 heures.
C'est le bon réglage pour la publication du soir, mais il rendait la veille
des alertes INUTILE — rebalayer toutes les 15 minutes en relisant un cache
de 12 heures ne peut rien détecter de neuf. La veille abaisse donc ce délai
par variable d'environnement.
"""

import importlib
import sys
import time
from pathlib import Path

import pandas as pd
import re

import pytest

from marketlab import config as config_module

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))


def _recharger_config(monkeypatch, **variables):
    for cle, valeur in variables.items():
        monkeypatch.setenv(cle, valeur)
    from marketlab import config
    return importlib.reload(config)


def test_valeurs_par_defaut(monkeypatch):
    monkeypatch.delenv("MARKETLAB_TTL_1D_H", raising=False)
    monkeypatch.delenv("MARKETLAB_TTL_1H_H", raising=False)
    config = _recharger_config(monkeypatch)
    assert config.CACHE_TTL_HOURS["1d"] == 12
    assert config.CACHE_TTL_HOURS["1h"] == 2
    assert config.CACHE_TTL_HOURS["1wk"] == 48


def test_la_veille_peut_abaisser_le_delai(monkeypatch):
    config = _recharger_config(monkeypatch, MARKETLAB_TTL_1D_H="0.23")
    assert config.CACHE_TTL_HOURS["1d"] == pytest.approx(0.23)


def test_le_delai_reel_de_la_veille_reste_sous_son_intervalle():
    """Les DEUX valeurs sont lues dans le workflow, aucune n'est fournie ici.

    La version precedente posait elle-meme le `0.23` qu'elle verifiait ensuite,
    et le comparait a un intervalle de 15 min qui n'existe plus (il est de 10).
    Elle etait donc vraie par construction : remonter le delai a 30 min dans le
    workflow — ce qui ferait relire le meme instantane pendant trois heures de
    veille — l'aurait laissee verte.
    """
    texte = (config_module.ROOT / ".github" / "workflows" / "alertes.yml")         .read_text(encoding="utf-8")
    ttl = re.search(r'MARKETLAB_TTL_1D_H:\s*"([0-9.]+)"', texte)
    intervalle = re.search(r"--intervalle-minutes\s+(\d+)", texte)
    assert ttl and intervalle, "workflow illisible : le garde-fou ne garde rien"
    ttl_min = float(ttl.group(1)) * 60
    intervalle_min = int(intervalle.group(1))
    assert ttl_min < intervalle_min, (
        f"delai de cache {ttl_min:.0f} min >= intervalle de balayage "
        f"{intervalle_min} min : la veille relirait le meme instantane")


def test_un_cache_perime_est_ignore(monkeypatch, tmp_path):
    """Vérifie la conséquence réelle : passé le délai, on refetch."""
    config = _recharger_config(monkeypatch, MARKETLAB_TTL_1D_H="0.23")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    from marketlab.data import base
    importlib.reload(base)
    monkeypatch.setattr(base.config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(base.config, "CACHE_TTL_HOURS", {"1d": 0.23})

    df = pd.DataFrame({"close": [1.0, 2.0]},
                      index=pd.date_range("2026-01-01", periods=2))
    base.save_cache(df, "test", "AAA", "1d", 400)
    assert base.load_cached("test", "AAA", "1d", 400) is not None

    # on vieillit artificiellement le fichier de 20 minutes
    chemin = base._cache_path("test", "AAA", "1d", 400)
    vieux = time.time() - 20 * 60
    import os
    os.utime(chemin, (vieux, vieux))
    assert base.load_cached("test", "AAA", "1d", 400) is None, (
        "un cache plus vieux que le délai doit être ignoré")


def test_le_delai_long_reste_valable_pour_la_publication(monkeypatch, tmp_path):
    monkeypatch.delenv("MARKETLAB_TTL_1D_H", raising=False)
    config = _recharger_config(monkeypatch)
    from marketlab.data import base
    importlib.reload(base)
    monkeypatch.setattr(base.config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(base.config, "CACHE_TTL_HOURS", {"1d": 12})

    df = pd.DataFrame({"close": [1.0]}, index=pd.date_range("2026-01-01", periods=1))
    base.save_cache(df, "test", "BBB", "1d", 400)
    chemin = base._cache_path("test", "BBB", "1d", 400)
    import os
    vieux = time.time() - 3 * 3600          # 3 heures : encore valable
    os.utime(chemin, (vieux, vieux))
    assert base.load_cached("test", "BBB", "1d", 400) is not None
