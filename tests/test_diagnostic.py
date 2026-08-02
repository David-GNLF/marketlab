"""Console de diagnostic — aucun accès réseau."""

import json

import pandas as pd
import pytest

from marketlab import diagnostic


@pytest.fixture(autouse=True)
def _sans_reseau(monkeypatch):
    """Rend la batterie HERMÉTIQUE.

    `diagnostic.etat()` interroge FRED à travers `realises.verifier()` et
    `surprise.surprises()`. En local le cache disque est chaud et personne ne
    le remarque ; en CI la batterie tourne AVANT l'étape qui restaure le
    cache, donc chaque appel télécharge pour de bon.

    Mesuré : un seul test à 29 s cache froid, et la batterie complète passée
    de 20 s à 565 s en CI — neuf minutes ajoutées à chaque déploiement, pour
    des tests qui ne vérifient rien du réseau. Ce que ces tests contrôlent,
    c'est la LOGIQUE du rapport : l'isolement des sondes, la hiérarchisation
    des alertes, l'absence de fuite de clé. Aucun n'a besoin d'une vraie série
    macroéconomique.
    """
    from marketlab import realises, surprise
    monkeypatch.setattr(realises, "verifier", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(realises, "correspondances_valides", lambda *a, **k: set())
    monkeypatch.setattr(surprise, "surprises",
                        lambda *a, **k: pd.DataFrame(columns=surprise.COLONNES))
    monkeypatch.setattr(surprise, "score_par_devise", lambda *a, **k: {})
    # le duel IV/EWMA lit le CSV commité puis SORTIRAIT sur le réseau : même
    # famille de fuite que celle qui avait coûté 9 minutes par déploiement
    from marketlab import implicite
    monkeypatch.setattr(implicite, "comparer_previsionnistes",
                        lambda *a, **k: {"mesurable": False,
                                         "raison": "neutralisé en test"})


# ---------------------------------------------------------------------------
# Robustesse : une console de diagnostic ne doit jamais tomber
# ---------------------------------------------------------------------------

def test_une_sonde_en_panne_nempeche_pas_les_autres(monkeypatch):
    """C'est PRÉCISÉMENT quand une brique casse qu'on consulte la console :
    elle doit rapporter la panne, pas la propager."""
    def tombe():
        raise RuntimeError("brique cassée")

    monkeypatch.setitem(diagnostic.SONDES, "volatilite_realisee", tombe)
    rapport = diagnostic.etat()
    assert "brique cassée" in rapport["volatilite_realisee"]["erreur"]
    assert "modele_volatilite" in rapport     # les autres ont rendu compte
    assert rapport["genere_le"]


def test_pas_de_doublon_avec_devapp():
    """Les clés d'API et le périmètre sont déjà rendus par `coulisses.sources()`
    et `coulisses.perimetre()`. Deux sondes répondant à la même question finissent
    par se contredire, et c'est alors la console qu'on cesse de croire."""
    assert "cles" not in diagnostic.SONDES
    assert "perimetre" not in diagnostic.SONDES


def test_le_diagnostic_est_greffe_dans_coulisses():
    """Une seule console, pas deux pages concurrentes.

    Le module `devapp` a ete renomme `coulisses` : la page publique s'appelle
    desormais « Coulisses », le nom « DevApp » restant a la console de
    l'espace d'administration. Deux pages homonymes qui ne montrent pas la
    meme chose sont une invitation a se tromper de porte.
    """
    from marketlab import coulisses
    assert "analyse" in coulisses.etat()


def test_une_panne_remonte_en_tete_de_page(monkeypatch):
    def tombe():
        raise ValueError("source injoignable")

    monkeypatch.setitem(diagnostic.SONDES, "surprises", tombe)
    alertes = diagnostic.etat()["alertes"]
    assert any(a["niveau"] == "erreur" and "surprises" in a["texte"]
               for a in alertes)


def test_le_rapport_est_serialisable():
    """Il part en JSON statique vers l'hébergement : rien ne doit résister."""
    json.dumps(diagnostic.etat(), default=str)


# ---------------------------------------------------------------------------
# Le point le plus sensible : aucun secret ne sort
# ---------------------------------------------------------------------------

def test_aucune_valeur_de_cle_dans_le_rapport(monkeypatch):
    """Le rapport est publié sur un site : une clé qui s'y glisserait serait
    exposée. On ne veut que des booléens."""
    faux = "ceciestunefausseclefredpourletest"
    from marketlab.data import fred, premium
    monkeypatch.setattr(fred, "api_key", lambda: faux)
    monkeypatch.setattr(premium, "api_key", lambda: faux)
    brut = json.dumps(diagnostic.etat(), default=str)
    assert faux not in brut
    assert "api_key" not in brut


# ---------------------------------------------------------------------------
# Hiérarchisation
# ---------------------------------------------------------------------------

def test_un_rapport_sain_dit_quil_est_sain():
    rapport = {
        "volatilite_realisee": {"manquants": []},
        "correspondances_fred": {"testees": 3, "valides": 3},
        "modele_volatilite": {"retenu": True},
        "surprises": {"surprises": 4},
    }
    alertes = diagnostic._resumer(rapport)
    assert len(alertes) == 1 and alertes[0]["niveau"] == "ok"


def test_les_correspondances_rejetees_sont_signalees():
    rapport = {"correspondances_fred": {"testees": 5, "valides": 3}}
    alertes = diagnostic._resumer(rapport)
    assert any("2 correspondance(s) FRED rejetée(s)" in a["texte"]
               for a in alertes)


def test_un_modele_ecarte_est_dit_explicitement():
    """Une brique qui s'est désactivée en silence est indiscernable d'une
    brique qui n'a jamais marché — c'est la raison d'être de cette console."""
    rapport = {"modele_volatilite": {"retenu": False}}
    alertes = diagnostic._resumer(rapport)
    assert any("HAR non retenu" in a["texte"] for a in alertes)


# ---------------------------------------------------------------------------
# Sondes individuelles
# ---------------------------------------------------------------------------

def test_volatilite_sur_releve_vide(monkeypatch):
    from marketlab import intraday
    monkeypatch.setattr(intraday, "charger_releve",
                        lambda: pd.DataFrame(columns=intraday.COLONNES_RV))
    etat = diagnostic._volatilite()
    assert etat["lignes"] == 0
    assert "avertissement" in etat


def test_volatilite_liste_les_titres_manquants(monkeypatch):
    from marketlab import intraday
    monkeypatch.setattr(diagnostic.config, "SUIVIS", ["AAPL", "MSFT", "ABSENT"])
    releve = pd.DataFrame({
        "date": ["2026-07-28", "2026-07-28"], "symbole": ["AAPL", "MSFT"],
        "interval": "5m", "rv": [1e-4, 1e-4], "n_barres": [77, 77],
        "vol_annualisee_%": [15.0, 16.0],
    })
    monkeypatch.setattr(intraday, "charger_releve", lambda: releve)
    assert diagnostic._volatilite()["manquants"] == ["ABSENT"]


def test_modele_sans_arbitrage(monkeypatch):
    from marketlab import har
    monkeypatch.setattr(har, "charger_modele", lambda: None)
    etat = diagnostic._modele_volatilite()
    assert etat["retenu"] is False
    assert "avertissement" in etat
