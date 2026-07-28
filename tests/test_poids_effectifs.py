"""Les pondérations réellement appliquées au verdict.

Régression protégée : quand la calibration conclut « rien n'est démontré »,
elle ne produit pas de pondérations. Si l'ancien fichier de poids appris
survivait, il continuerait de piloter le verdict APRÈS que la mesure l'a
invalidé — exactement l'inverse de ce qu'on veut.
"""

import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import decision


@pytest.fixture
def fichier_poids(tmp_path, monkeypatch):
    chemin = tmp_path / "poids_appris.json"
    monkeypatch.setattr(decision, "POIDS_APPRIS", chemin)
    return chemin


def test_sans_fichier_on_utilise_les_poids_de_base(fichier_poids):
    poids, meta = decision.poids_effectifs()
    assert poids == decision.POIDS
    assert meta["source"] == "base"


def test_poids_appris_valides_sont_appliques(fichier_poids):
    appris = {nom: 1 / len(decision.POIDS) for nom in decision.POIDS}
    fichier_poids.write_text(json.dumps(
        {"poids": appris, "n_evalues": 500, "lambda": 0.5, "date": "2026-07-27"}),
        encoding="utf-8")
    poids, meta = decision.poids_effectifs()
    assert poids == appris
    assert meta["source"] == "apprise"


def test_conclusion_negative_ramene_aux_poids_de_base(fichier_poids):
    """« poids: null » = aucune composante n'a fait ses preuves."""
    fichier_poids.write_text(json.dumps(
        {"poids": None, "n_evalues": 3232,
         "statut": "aucune composante n'a démontré de pouvoir prédictif"}),
        encoding="utf-8")
    poids, meta = decision.poids_effectifs()
    assert poids == decision.POIDS
    assert meta["source"] == "base"


def test_fichier_incoherent_ne_pilote_pas_le_verdict(fichier_poids):
    fichier_poids.write_text(json.dumps({"poids": {"technique": 0.9}}),
                             encoding="utf-8")
    poids, _ = decision.poids_effectifs()
    assert poids == decision.POIDS


def test_calibrer_ecrit_meme_quand_rien_nest_prouve(fichier_poids, monkeypatch):
    """Le rapport du jour doit toujours remplacer celui de la veille."""
    fichier_poids.write_text(json.dumps(
        {"poids": {nom: 1 / len(decision.POIDS) for nom in decision.POIDS}}),
        encoding="utf-8")
    import pandas as pd
    monkeypatch.setattr(decision, "_evaluer_journal", lambda: pd.DataFrame())
    rapport = decision.calibrer()
    assert rapport["poids"] is None
    ecrit = json.loads(fichier_poids.read_text(encoding="utf-8"))
    assert ecrit["poids"] is None          # l'ancien a bien été remplacé
    poids, meta = decision.poids_effectifs()
    assert poids == decision.POIDS and meta["source"] == "base"
