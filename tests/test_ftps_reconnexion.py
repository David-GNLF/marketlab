"""La connexion FTPS doit survivre à un incident réseau passager.

Régression protégée : la nuit du 2026-07-28, un `TimeoutError` sur la
connexion FTPS a fait échouer l'étape du robot, et avec elle TOUTE la
publication — le site est resté figé sur les données de la veille alors que
les verdicts étaient déjà calculés. Un hébergement mutualisé refuse ou
retarde une connexion de temps à autre ; il faut le supporter.
"""

import ftplib
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import ftps

CFG = {"hote": "exemple.test", "port": 21, "utilisateur": "u",
       "mot_de_passe": "p", "dossier_distant": "/public_html/x"}


class FausseSession:
    """Session FTPS factice : échoue les `echecs` premières tentatives."""
    tentatives = 0

    def __init__(self, echecs):
        self._echecs = echecs

    def connect(self, hote, port):
        FausseSession.tentatives += 1
        if FausseSession.tentatives <= self._echecs:
            raise TimeoutError("timed out")

    def login(self, u, m): pass
    def prot_p(self): pass
    def set_pasv(self, v): pass


@pytest.fixture(autouse=True)
def sans_attente(monkeypatch):
    """Les tests ne doivent pas dormir pour de vrai."""
    monkeypatch.setattr(ftps.time, "sleep", lambda s: None)
    FausseSession.tentatives = 0


def _preparer(monkeypatch, echecs):
    monkeypatch.setattr(ftps.ftplib, "FTP_TLS",
                        lambda timeout=60: FausseSession(echecs))


def test_reussite_immediate(monkeypatch):
    _preparer(monkeypatch, echecs=0)
    assert ftps._connecter(CFG) is not None
    assert FausseSession.tentatives == 1


def test_un_incident_passager_est_rattrape(monkeypatch):
    """Le cas réel : la première connexion expire, la seconde passe."""
    _preparer(monkeypatch, echecs=1)
    assert ftps._connecter(CFG) is not None
    assert FausseSession.tentatives == 2


def test_deux_incidents_sont_rattrapes(monkeypatch):
    _preparer(monkeypatch, echecs=2)
    assert ftps._connecter(CFG) is not None
    assert FausseSession.tentatives == 3


def test_panne_durable_leve_une_erreur_explicite(monkeypatch):
    _preparer(monkeypatch, echecs=99)
    with pytest.raises(RuntimeError, match="après 3 essais"):
        ftps._connecter(CFG)
    assert FausseSession.tentatives == 3


def test_une_erreur_de_protocole_est_aussi_reessayee(monkeypatch):
    """Un refus temporaire du serveur (421 too many connections) doit être
    traité comme un incident réseau, pas comme une fatalité."""
    class RefusTemporaire(FausseSession):
        def connect(self, hote, port):
            FausseSession.tentatives += 1
            if FausseSession.tentatives == 1:
                raise ftplib.error_temp("421 Too many connections")
    monkeypatch.setattr(ftps.ftplib, "FTP_TLS",
                        lambda timeout=60: RefusTemporaire(0))
    assert ftps._connecter(CFG) is not None
    assert FausseSession.tentatives == 2


def test_le_nombre_dessais_est_reglable(monkeypatch):
    _preparer(monkeypatch, echecs=99)
    with pytest.raises(RuntimeError, match="après 5 essais"):
        ftps._connecter(CFG, tentatives=5)
    assert FausseSession.tentatives == 5
