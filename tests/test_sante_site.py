"""Sonde de santé du site : détecter la panne d'aujourd'hui, sans crier deux fois.

Aucun réseau : HTTP, FTPS et canaux sont stubbés. La sonde est jugée sur les
DEUX moitiés du contrat — voir ce qui casse une page (dont le token NaN nu,
la panne réelle du 2026-08-02), et se taire tant qu'une panne déjà signalée
persiste.
"""

import json

import pandas as pd
import pytest

from marketlab import sante_site as ss

QUAND = pd.Timestamp("2026-08-02 12:00")


def _fichiers_sains(quand=QUAND):
    meta = {"genere_le": (quand - pd.Timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")}
    return {
        "donnees/meta.json": json.dumps(meta).encode(),
        "donnees/verdicts.json": b'{"dossiers": []}',
        "donnees/concours.json": b'{"comptes": []}',
        "donnees/coulisses.json": b'{"depot": {}}',
    }


@pytest.fixture(autouse=True)
def _sans_reseau(monkeypatch):
    monkeypatch.setattr(ss, "_http_etat", lambda: 401)   # mur d'auth debout
    monkeypatch.setattr(ss, "_lire_distants",
                        lambda chemins: {c: _fichiers_sains().get(c)
                                         for c in chemins})
    monkeypatch.setattr(ss, "_ecrire_distant", lambda chemin, contenu: None)


# ------------------------------------------------------------------- la sonde

def test_tout_sain_401_compris():
    s = ss.sonder(QUAND)
    assert s["sain"] and s["http"] == 401


def test_injoignable_et_5xx_sont_des_pannes(monkeypatch):
    monkeypatch.setattr(ss, "_http_etat", lambda: None)
    assert any("injoignable" in p for p in ss.sonder(QUAND)["problemes"])
    monkeypatch.setattr(ss, "_http_etat", lambda: 503)
    assert any("503" in p for p in ss.sonder(QUAND)["problemes"])


def test_le_nan_nu_est_vu_comme_page_morte(monkeypatch):
    # LE cas réel : coulisses.json avec un token NaN nu — JSON.parse meurt
    fichiers = _fichiers_sains()
    fichiers["donnees/coulisses.json"] = b'{"recalcule": NaN}'
    monkeypatch.setattr(ss, "_lire_distants",
                        lambda chemins: {c: fichiers.get(c) for c in chemins})
    problemes = ss.sonder(QUAND)["problemes"]
    assert any("coulisses.json" in p and "JSON" in p for p in problemes)


def test_un_nan_entre_guillemets_ne_declenche_rien(monkeypatch):
    # la leçon du test de CI : « NaN » DANS une chaîne est du JSON valide
    fichiers = _fichiers_sains()
    fichiers["donnees/coulisses.json"] = \
        b'{"sujet": "un NaN tuait la page Coulisses"}'
    monkeypatch.setattr(ss, "_lire_distants",
                        lambda chemins: {c: fichiers.get(c) for c in chemins})
    assert ss.sonder(QUAND)["sain"]


def test_fichier_absent_et_instantane_perime(monkeypatch):
    fichiers = _fichiers_sains()
    fichiers["donnees/verdicts.json"] = None
    fichiers["donnees/meta.json"] = json.dumps(
        {"genere_le": "2026-07-28 20:29"}).encode()      # ~5 jours
    monkeypatch.setattr(ss, "_lire_distants",
                        lambda chemins: {c: fichiers.get(c) for c in chemins})
    problemes = ss.sonder(QUAND)["problemes"]
    assert any("verdicts.json absent" in p for p in problemes)
    assert any("périmé" in p for p in problemes)


# ------------------------------------------------- transitions, pas répétitions

def _canaux(monkeypatch):
    envois = []
    from marketlab import fil_alertes, notify
    monkeypatch.setattr(notify, "envoyer",
                        lambda texte, urgent=False:
                        envois.append((texte, urgent)) or True)
    monkeypatch.setattr(fil_alertes, "publier", lambda n: {"publiees": len(n)})
    return envois


def _etat_precedent(monkeypatch, problemes):
    """L'état mémorisé sur l'hébergement, injecté dans la lecture stubbée."""
    memorise = json.dumps({"problemes": problemes}).encode()

    def _lire(chemins):
        base = {c: _fichiers_sains().get(c) for c in chemins}
        if ss.ETAT_DISTANT in chemins:
            base[ss.ETAT_DISTANT] = memorise
        return base
    monkeypatch.setattr(ss, "_lire_distants", _lire)


def test_panne_nouvelle_alerte_urgente_une_fois(monkeypatch):
    envois = _canaux(monkeypatch)
    _etat_precedent(monkeypatch, [])
    monkeypatch.setattr(ss, "_http_etat", lambda: None)
    r = ss.verifier_et_alerter(QUAND)
    assert r["transitions"] == 1
    assert envois and envois[0][1] is True               # urgent


# AUCUN de ces tests ne doit toucher le réseau. Ils vérifient une LOGIQUE DE
# TRANSITION — « une panne inchangée reste muette », « un rétablissement se
# signale une fois » — et cette logique ne dépend pas de l'état du serveur au
# moment où la suite tourne. Deux d'entre eux appelaient pourtant le vrai site
# et ont bloqué la publication du 2026-08-04 : `verifier_et_alerter` faisait
# une requête HTTP réelle, et `sonder` une lecture FTPS réelle, si bien que le
# problème mémorisé n'était pas celui recalculé juste après.
#
# Un test qui interroge le réseau depuis une barrière bloquante ne mesure plus
# le code : il mesure la météo.


def test_panne_inchangee_reste_muette(monkeypatch):
    envois = _canaux(monkeypatch)
    monkeypatch.setattr(ss, "_http_etat", lambda: None)
    # Les fichiers distants sont stubbés AVANT le premier sondage : sans cela,
    # `probleme` venait d'une lecture réelle et différait de ce que
    # `verifier_et_alerter` recalculait ensuite — d'où une transition fantôme.
    monkeypatch.setattr(ss, "_lire_distants",
                        lambda chemins: {c: _fichiers_sains().get(c)
                                         for c in chemins})
    probleme = ss.sonder(QUAND)["problemes"][0]
    _etat_precedent(monkeypatch, [probleme])
    r = ss.verifier_et_alerter(QUAND)
    assert r["transitions"] == 0 and envois == []


def test_retablissement_signale_sans_urgence(monkeypatch):
    envois = _canaux(monkeypatch)
    # 401 : le serveur RÉPOND, le site est simplement protégé par mot de passe.
    # C'est l'état sain nominal de ce site, et il doit être simulé — sinon le
    # test échoue dès que la connexion du poste de travail hoquette.
    monkeypatch.setattr(ss, "_http_etat", lambda: 401)
    _etat_precedent(monkeypatch, ["site injoignable en HTTP (délai ou "
                                  "connexion refusée)"])
    r = ss.verifier_et_alerter(QUAND)
    assert r["sain"] and r["transitions"] == 1
    assert envois[0][1] is False and "rétabli" in envois[0][0]


def test_aucun_test_de_ce_fichier_ne_touche_au_reseau(monkeypatch):
    """Garde-fou du garde-fou.

    Si `_http_etat` ou `_lire_distants` reprenaient le chemin réel, l'échec
    serait intermittent et on le mettrait sur le compte du réseau pendant des
    semaines. Ici on les rend explosifs et l'on vérifie que la sonde stubbée
    fonctionne quand même de bout en bout.
    """
    def _interdit(*a, **k):
        raise AssertionError("ce test a tenté un accès réseau")

    monkeypatch.setattr(ss.urllib.request, "urlopen", _interdit)
    monkeypatch.setattr(ss, "_http_etat", lambda: 401)
    monkeypatch.setattr(ss, "_lire_distants",
                        lambda chemins: {c: _fichiers_sains().get(c)
                                         for c in chemins})
    assert ss.sonder(QUAND)["sain"]


def test_la_sonde_en_panne_ne_casse_jamais_la_veille(monkeypatch):
    _canaux(monkeypatch)

    def _casse(*a, **k):
        raise RuntimeError("FTPS en panne")
    monkeypatch.setattr(ss, "_lire_distants", _casse)
    monkeypatch.setattr(ss, "_ecrire_distant", _casse)
    r = ss.verifier_et_alerter(QUAND)                          # ne lève pas
    assert not r["sain"]
