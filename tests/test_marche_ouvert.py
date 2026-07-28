"""Le garde « marché fermé » : ordres refusés hors séance.

Régression protégée ici : une première version déduisait l'ouverture de
l'ÂGE de la cotation, et laissait donc passer des ordres au marché sur
Euronext fermé (la source continue de rafraîchir l'horodatage après la
cloche). Une seconde version, correcte, était contournée parce que la page
de trading reconstruisait une cotation partielle où les heures de séance
avaient disparu. Les deux cas sont testés.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP absent de ce poste")


def _ouvert(cote) -> bool:
    r = subprocess.run([PHP, str(RACINE / "tests" / "marche_php.php"),
                        json.dumps(cote)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip() == "ouvert"


MAINTENANT = int(time.time())
HEURE = 3600


def test_seance_en_cours():
    assert _ouvert({"source": "direct", "age_s": 10,
                    "seance_debut": MAINTENANT - HEURE,
                    "seance_fin": MAINTENANT + HEURE}) is True


def test_seance_terminee_meme_avec_une_cotation_recente():
    """Le cas Euronext : cloche passée depuis 5 min, cotation vieille de
    15 min seulement — l'âge dirait « ouvert », la séance dit « fermé »."""
    assert _ouvert({"source": "direct", "age_s": 900,
                    "seance_debut": MAINTENANT - 9 * HEURE,
                    "seance_fin": MAINTENANT - 300}) is False


def test_seance_pas_encore_ouverte():
    assert _ouvert({"source": "direct", "age_s": 60,
                    "seance_debut": MAINTENANT + HEURE,
                    "seance_fin": MAINTENANT + 8 * HEURE}) is False


def test_seance_a_cheval_sur_minuit_ouverte():
    """Forex et futures : début > fin, ouvert sauf pendant la coupure."""
    assert _ouvert({"source": "direct", "age_s": 60,
                    "seance_debut": MAINTENANT - HEURE,
                    "seance_fin": MAINTENANT - 2 * HEURE}) is True


def test_seance_a_cheval_pendant_la_coupure():
    assert _ouvert({"source": "direct", "age_s": 60,
                    "seance_debut": MAINTENANT + HEURE,
                    "seance_fin": MAINTENANT - HEURE}) is False


def test_crypto_toujours_ouverte():
    assert _ouvert({"source": "direct", "age_s": 5, "permanent": True}) is True


def test_repli_sur_le_cours_publie_est_ferme():
    """Sans cotation vivante, on n'exécute pas au marché."""
    assert _ouvert({"source": "publié", "age_s": None}) is False


def test_sans_heures_de_seance_on_retombe_sur_lage():
    assert _ouvert({"source": "direct", "age_s": 60}) is True
    assert _ouvert({"source": "direct", "age_s": 99999}) is False


def test_reponse_precalculee_fait_foi():
    """Une page qui transporte l'état ne doit pas pouvoir le contredire,
    même si les heures de séance manquent dans ce qu'elle transmet."""
    assert _ouvert({"source": "direct", "age_s": 60,
                    "marche_ouvert": False}) is False
    assert _ouvert({"source": "direct", "age_s": 99999,
                    "marche_ouvert": True}) is True
