"""Le conseil s'abstient ; la mesure continue.

POURQUOI CE FICHIER EXISTE. Le 2026-07-31, l'abstention prudentielle a été
mise en service et a fermé, dans l'heure, toutes les positions des trois
robots. Aucun n'aurait pu en rouvrir : ils n'ouvrent que sur avis
« Favorable », et le site s'abstient désormais dans la plupart des régimes.

C'était un défaut de conception, pas un réglage malheureux. Les robots sont
l'APPAREIL DE MESURE : c'est leur activité qui accumule les épisodes
indépendants dont on a besoin pour un jour prouver — ou réfuter — un
avantage. Les faire taire referme la boucle qui pourrait justifier de lever le
silence : l'outil s'interdit de trader jusqu'à avoir prouvé qu'il sait trader,
sans plus jamais produire la preuve.

Ce qui est verrouillé ici :
  1. le site continue de s'abstenir (le conseil reste prudent) ;
  2. les robots de mesure continuent d'agir sur le verdict brut ;
  3. « claudeprudent » obéit, lui, pour qu'on sache un jour ce que
     l'abstention aura coûté ou rapporté ;
  4. une prudence n'est jamais présentée comme une inversion démontrée.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import robot_trading as rt  # noqa: E402


def _dossier_suspendu(symbole="AAPL", note=45.0):
    """Un verdict tel que le produit `decision.dossier` sous suspension :
    l'avis publié est « S'abstenir », l'avis brut reste « Favorable »."""
    return {
        "symbole": symbole, "note_globale": note, "classe": "Actions",
        "avis": "S'abstenir", "taille_multiplicateur": 0.0,
        "avis_hors_suspension": "Favorable", "taille_hors_suspension": 1.0,
        "plan": {"entree": 100.0, "stop": 95.0, "objectif": 110.0,
                 "ratio_gain_risque": 2.0},
        "concordance_%": 100.0,
    }


def _compte(nom="claude"):
    return {"nom": nom, "capital_initial": 1000.0, "solde": 1000.0,
            "positions": [], "ordres": [], "historique": []}


def test_le_robot_de_mesure_agit_malgre_l_abstention_du_site(monkeypatch):
    """Sans cela, le concours s'arrête et l'apprentissage avec lui."""
    monkeypatch.setattr(rt, "_cours_publie", lambda s: 100.0)
    compte = _compte()
    rt.decisions_robot(compte, [_dossier_suspendu()],
                       respecte_suspension=False)
    assert len(compte["positions"]) == 1, (
        "le robot de mesure doit continuer a produire des episodes meme "
        "quand le site s'abstient")


def test_le_robot_temoin_obeit_a_l_abstention(monkeypatch):
    """C'est lui la référence : l'écart avec les autres mesurera ce que
    l'abstention aura coûté ou rapporté."""
    monkeypatch.setattr(rt, "_cours_publie", lambda s: 100.0)
    compte = _compte("claudeprudent")
    rt.decisions_robot(compte, [_dossier_suspendu()],
                       respecte_suspension=True)
    assert compte["positions"] == []


def test_le_temoin_ferme_ce_que_les_autres_gardent(monkeypatch):
    """Une position ouverte avant la suspension : le témoin la solde, les
    robots de mesure la conservent."""
    monkeypatch.setattr(rt, "_cours_publie", lambda s: 100.0)
    from marketlab.data import get_ohlcv  # noqa: F401
    monkeypatch.setattr(rt, "get_ohlcv",
                        lambda s, lookback_days=30: __import__("pandas")
                        .DataFrame({"close": [100.0]}))
    position = {"symbole": "AAPL", "sens": "long", "marge": 50.0, "levier": 2,
                "prix_entree": 100.0, "quantite": 1.0, "stop": 95.0,
                "objectif": 110.0, "ouvert_le": "2026-07-27 15:22"}

    temoin = _compte("claudeprudent"); temoin["positions"] = [dict(position)]
    rt.decisions_robot(temoin, [_dossier_suspendu()], respecte_suspension=True)
    assert temoin["positions"] == [], "le temoin doit solder"

    mesure = _compte(); mesure["positions"] = [dict(position)]
    rt.decisions_robot(mesure, [_dossier_suspendu()],
                       respecte_suspension=False)
    assert len(mesure["positions"]) == 1, "le robot de mesure doit conserver"


def test_sans_suspension_les_deux_robots_font_pareil(monkeypatch):
    """L'écart ne doit venir QUE de la suspension. Un verdict ordinaire — sans
    les champs `*_hors_suspension` — doit produire le même comportement des
    deux côtés, sinon l'expérience compare deux choses à la fois."""
    monkeypatch.setattr(rt, "_cours_publie", lambda s: 100.0)
    ordinaire = _dossier_suspendu()
    del ordinaire["avis_hors_suspension"], ordinaire["taille_hors_suspension"]
    ordinaire["avis"] = "Favorable"
    ordinaire["taille_multiplicateur"] = 1.0

    a, b = _compte(), _compte("claudeprudent")
    rt.decisions_robot(a, [ordinaire], respecte_suspension=False)
    rt.decisions_robot(b, [ordinaire], respecte_suspension=True)
    assert len(a["positions"]) == len(b["positions"]) == 1


def test_une_seule_variable_separe_le_temoin_de_la_reference():
    """« claudeprudent » ne doit differer de « claude » QUE par l'obeissance.
    Deux differences rendraient tout ecart ininterpretable."""
    ref, temoin = rt.ROBOTS["claude"], rt.ROBOTS["claudeprudent"]
    for champ in ("cle", "horizon", "classes"):
        assert ref[champ] == temoin[champ], champ
    assert temoin.get("respecte_suspension") is True
    assert ref.get("respecte_suspension", False) is False
