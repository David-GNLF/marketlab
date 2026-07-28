"""L'invariant qui a coûté le plus cher : un seul montant, partout.

Le robot calcule l'équité en Python ; la page de trading et le panneau
d'administration la calculent en PHP. Deux langages, deux codes — donc rien
ne garantit qu'ils s'accordent, sauf à le vérifier. Ce test compare les deux
sur les mêmes comptes et les mêmes cours.

Il s'ignore si PHP n'est pas installé (poste de travail), mais il s'exécute
dans l'intégration continue, qui a PHP.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

import robot_trading as rt

PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP absent de ce poste")

PRIX = {"AAPL": 120.0, "BTCUSDT": 50000.0, "EURUSD=X": 1.10}

COMPTES = {
    "compte vierge": {"nom": "t", "solde": 1000.0, "positions": [],
                      "ordres": [], "historique": []},
    "position longue gagnante": {
        "nom": "t", "solde": 900.0, "ordres": [], "historique": [],
        "positions": [{"symbole": "AAPL", "sens": "long", "marge": 100.0,
                       "levier": 5, "notionnel": 500.0, "quantite": 5.0,
                       "prix_entree": 100.0}]},
    "position courte perdante": {
        "nom": "t", "solde": 800.0, "ordres": [], "historique": [],
        "positions": [{"symbole": "AAPL", "sens": "short", "marge": 200.0,
                       "levier": 2, "notionnel": 400.0, "quantite": 4.0,
                       "prix_entree": 110.0}]},
    "ordre en attente seul": {
        "nom": "t", "solde": 950.0, "positions": [], "historique": [],
        "ordres": [{"id": "o1", "symbole": "AAPL", "sens": "long",
                    "type": "limite", "prix": 90.0, "marge": 50.0,
                    "levier": 4}]},
    "mélange positions et ordres": {
        "nom": "t", "solde": 500.0, "historique": [],
        "positions": [
            {"symbole": "AAPL", "sens": "long", "marge": 100.0, "levier": 5,
             "notionnel": 500.0, "quantite": 5.0, "prix_entree": 100.0},
            {"symbole": "BTCUSDT", "sens": "short", "marge": 150.0,
             "levier": 3, "notionnel": 450.0, "quantite": 0.009,
             "prix_entree": 52000.0}],
        "ordres": [{"id": "o1", "symbole": "EURUSD=X", "sens": "long",
                    "type": "stop", "prix": 1.15, "marge": 80.0,
                    "levier": 5}]},
    "symbole sans cours connu": {
        "nom": "t", "solde": 700.0, "ordres": [], "historique": [],
        "positions": [{"symbole": "INCONNU", "sens": "long", "marge": 300.0,
                       "levier": 2, "notionnel": 600.0, "quantite": 6.0,
                       "prix_entree": 100.0}]},
}


def _equite_php(compte, prix, tmp_path):
    f_compte = tmp_path / "compte.json"
    f_prix = tmp_path / "prix.json"
    f_compte.write_text(json.dumps(compte), encoding="utf-8")
    f_prix.write_text(json.dumps(prix), encoding="utf-8")
    r = subprocess.run(
        [PHP, str(RACINE / "tests" / "equite_php.php"), str(f_compte),
         str(f_prix)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"PHP a échoué : {r.stderr}"
    return float(r.stdout.strip())


@pytest.mark.parametrize("libelle", list(COMPTES))
def test_php_et_python_donnent_la_meme_equite(libelle, tmp_path, monkeypatch):
    compte = COMPTES[libelle]
    monkeypatch.setattr(rt, "_cours_publie", lambda s: PRIX.get(s))
    python = rt._equite(compte)
    php = _equite_php(compte, PRIX, tmp_path)
    assert php == pytest.approx(python, abs=1e-6), (
        f"{libelle} : PHP {php} ≠ Python {python}")
