"""La page Concours doit dire POURQUOI le témoin ressemble à la référence.

POURQUOI CE FICHIER EXISTE. David a regardé le classement et constaté que
« claudeprudent » affichait exactement les mêmes positions que « claude », aux
mêmes prix d'entrée. Sa conclusion — « ça a juste repris les données de
claude » — était fausse, mais parfaitement raisonnable au vu de ce que la page
montrait.

Les deux comptes décident séparément ; ils aboutissent au même résultat parce
que le témoin ne diffère que par l'obéissance au veto de régime, et qu'il n'y
a rien à quoi obéir tant qu'aucun régime n'est suspendu.

Une page qui laisse tirer une conclusion fausse d'une observation juste doit
fournir le chaînon manquant. C'est ce que `_etat_temoin()` publie, et ce que
ce fichier verrouille : la mention doit être présente, et surtout DIRE LA
VÉRITÉ DU JOUR — annoncer un écart mesurable quand il n'y en a pas serait
exactement la même faute, dans l'autre sens.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import robot_trading as rt  # noqa: E402
from marketlab import regimes  # noqa: E402


def _etat(monkeypatch, courant, suspendus):
    monkeypatch.setattr(regimes, "regime_courant", lambda: courant)
    monkeypatch.setattr(regimes, "charger_verdict",
                        lambda: {"suspendus": suspendus})
    return rt._etat_temoin()


def test_regime_suspendu_lecart_se_mesure(monkeypatch):
    e = _etat(monkeypatch, "normal", ["normal", "tendu"])
    assert e["actif"] is True
    assert "s'abstient" in e["lecture"]
    assert "coûte ou rapporte" in e["lecture"]


def test_regime_non_suspendu_la_page_previent_du_doublon(monkeypatch):
    e = _etat(monkeypatch, "calme", ["normal", "tendu"])
    assert e["actif"] is False
    assert "PAR CONSTRUCTION" in e["lecture"], (
        "le lecteur doit comprendre que l'identite est voulue")
    assert "duplication" in e["lecture"]
    # et il doit savoir QUAND l'ecart apparaitra
    assert "marché ordinaire" in e["lecture"] and "marché tendu" in e["lecture"]


def test_aucun_regime_suspendu(monkeypatch):
    """Cas du veto entierement leve : pas de « leur ecart apparaitra en … »
    puisqu'il n'y a plus aucun regime ou il pourrait apparaitre."""
    e = _etat(monkeypatch, "calme", [])
    assert e["actif"] is False
    assert "aucun régime n'est actuellement" in e["lecture"]
    assert " ou en ." not in e["lecture"], "phrase tronquee"


def test_le_regime_illisible_ne_ment_pas(monkeypatch):
    """Ne pas savoir doit se dire. Afficher « identique par construction »
    alors qu'on n'a pas pu lire le regime serait une affirmation gratuite."""
    def _tombe():
        raise RuntimeError("source injoignable")
    monkeypatch.setattr(regimes, "regime_courant", _tombe)
    e = rt._etat_temoin()
    assert e["actif"] is None
    assert "indisponible" in e["lecture"]


def test_la_mention_ne_promet_pas_un_ecart_inexistant(monkeypatch):
    """Le defaut symetrique : annoncer que l'ecart se mesure alors que le
    temoin fait la meme chose que la reference."""
    e = _etat(monkeypatch, "calme", ["tendu"])
    assert "à partir de maintenant" not in e["lecture"]
