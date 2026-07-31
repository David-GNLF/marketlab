"""La veille en boucle : tirer parti de chaque déclenchement obtenu.

Contexte. Les exécutions planifiées gratuites de GitHub sont au mieux-effort :
un cron horaire n'a donné que 10 passages en 24 h, et trois crons par heure
seulement 7 en 15 h (~15 % d'honorés). Plutôt que d'espérer plus de
déclenchements, chaque déclenchement obtenu balaie maintenant pendant une
heure. Ces tests vérifient la mécanique de cette veille — sans jamais
dormir pour de vrai.
"""

import sys
from pathlib import Path


RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

import alertes as cli


class Horloge:
    """Horloge factice : n'avance que lorsqu'on « dort »."""

    def __init__(self):
        self.t = 0.0
        self.sommeils = []

    def maintenant(self):
        return self.t

    def dormir(self, secondes):
        self.sommeils.append(secondes)
        self.t += secondes


def _veille(duree, intervalle, passage=None, horloge=None):
    h = horloge or Horloge()
    appels = []

    def defaut(universes=None, dry_run=False):
        appels.append(h.maintenant())
        return {"alertes": 1, "envoyees": 1}

    cumul = cli.boucler(duree, intervalle, horloge=h.maintenant,
                        dormir=h.dormir, passage=passage or defaut)
    return cumul, appels, h


def test_un_passage_immediat_puis_a_intervalle_regulier():
    cumul, appels, h = _veille(duree=55, intervalle=8)
    assert appels[0] == 0                      # le premier balayage est immédiat
    assert cumul["passages"] == 7              # 0, 8, 16, 24, 32, 40, 48 min
    assert [round(a / 60) for a in appels] == [0, 8, 16, 24, 32, 40, 48]


def test_la_veille_ne_deborde_pas_de_sa_duree():
    cumul, appels, h = _veille(duree=55, intervalle=8)
    assert max(appels) / 60 < 55
    assert h.maintenant() / 60 < 55


def test_un_intervalle_plus_court_donne_plus_de_balayages():
    court, _, _ = _veille(duree=55, intervalle=5)
    long_, _, _ = _veille(duree=55, intervalle=15)
    assert court["passages"] > long_["passages"]


def test_un_passage_en_echec_narrete_pas_la_veille():
    """Sur une heure, mieux vaut cinq balayages réussis et un raté qu'un
    arrêt complet au premier incident réseau."""
    etat = {"n": 0}

    def capricieux(universes=None, dry_run=False):
        etat["n"] += 1
        if etat["n"] == 2:
            raise RuntimeError("timed out")
        return {"alertes": 0, "envoyees": 0}

    cumul, _, _ = _veille(duree=55, intervalle=8, passage=capricieux)
    assert cumul["passages"] == 7
    assert cumul["echecs"] == 1


def test_lintervalle_se_compte_depuis_le_debut_du_passage():
    """Un balayage réel dure plusieurs minutes. Compter l'intervalle depuis
    sa FIN allongeait le cycle d'autant et faisait tomber la veille à un seul
    passage — c'est ce qu'a montré le premier essai en conditions réelles."""
    h = Horloge()
    debuts = []

    def lent(universes=None, dry_run=False):
        debuts.append(h.maintenant())
        h.t += 3 * 60                     # le balayage prend 3 minutes
        return {"alertes": 0, "envoyees": 0}

    cli.boucler(55, 8, horloge=h.maintenant, dormir=h.dormir, passage=lent)
    assert [round(d / 60) for d in debuts] == [0, 8, 16, 24, 32, 40, 48]


def test_un_balayage_plus_long_que_lintervalle_enchaine_sans_attendre():
    h = Horloge()
    debuts = []

    def tres_lent(universes=None, dry_run=False):
        debuts.append(h.maintenant())
        h.t += 12 * 60                    # plus long que l'intervalle
        return {"alertes": 0, "envoyees": 0}

    cli.boucler(50, 8, horloge=h.maintenant, dormir=h.dormir, passage=tres_lent)
    assert [round(d / 60) for d in debuts] == [0, 12, 24, 36, 48]
    assert all(s == 0 for s in h.sommeils)   # jamais d'attente inutile


def test_le_cumul_agrege_les_alertes():
    def deux_alertes(universes=None, dry_run=False):
        return {"alertes": 2, "envoyees": 1}

    cumul, _, _ = _veille(duree=30, intervalle=10, passage=deux_alertes)
    assert cumul["passages"] == 3
    assert cumul["alertes"] == 6
    assert cumul["envoyees"] == 3


def test_une_duree_courte_ne_fait_quun_passage():
    cumul, appels, h = _veille(duree=5, intervalle=8)
    assert cumul["passages"] == 1
    assert h.sommeils == []          # on ne dort jamais pour rien


def test_la_veille_couvre_bien_mieux_quun_passage_unique():
    """Le gain attendu du changement de mécanisme, chiffré."""
    cumul, appels, _ = _veille(duree=55, intervalle=8)
    couverture_min = (max(appels) - min(appels)) / 60
    assert cumul["passages"] >= 6
    assert couverture_min >= 45      # une heure couverte, pas un instantané
