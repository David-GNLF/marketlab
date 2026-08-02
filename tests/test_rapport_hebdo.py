"""Rapport hebdomadaire : partir le vendredi, une fois, et composer juste.

Aucun réseau : le fil, la notification et le journal sont stubbés ; les
chemins (marqueur, concours) sont détournés vers tmp_path. La date est
toujours PASSÉE en paramètre — un test qui dépend du jour où il tourne
serait vert quatre jours sur cinq.
"""

import json

import pandas as pd
import pytest

from marketlab import rapport_hebdo as rh

VENDREDI = pd.Timestamp("2026-08-07 22:30")     # un vendredi
MERCREDI = pd.Timestamp("2026-08-05 22:30")


@pytest.fixture(autouse=True)
def _isole(tmp_path, monkeypatch):
    monkeypatch.setattr(rh, "MARQUEUR_PATH", tmp_path / "marqueur.json")
    monkeypatch.setattr(rh, "CONCOURS_PATH", tmp_path / "concours.json")
    # le rapport composé est stubbé par défaut : les tests d'envoi jugent la
    # PORTE (jour, marqueur, canaux), pas la composition
    monkeypatch.setattr(rh, "composer",
                        lambda quand=None: {"semaine": rh._semaine_iso(
                            quand or VENDREDI), "comptes": {}, "chaine": {},
                            "surveillance": {}, "jalons": {}})


@pytest.fixture
def canaux(monkeypatch):
    """Notification et fil stubbés, appels enregistrés."""
    appels = {"notify": [], "fil": []}
    from marketlab import fil_alertes, notify
    monkeypatch.setattr(notify, "envoyer",
                        lambda corps, urgent=False:
                        appels["notify"].append(corps) or True)
    monkeypatch.setattr(fil_alertes, "publier",
                        lambda nouvelles: appels["fil"].extend(nouvelles)
                        or {"publiees": len(nouvelles)})
    return appels


# ------------------------------------------------------------------- la porte

def test_ne_part_pas_hors_vendredi(canaux):
    r = rh.envoyer(quand=MERCREDI)
    assert not r["envoye"] and "vendredi" in r["raison"]
    assert not canaux["notify"]


def test_part_le_vendredi_et_marque_la_semaine(canaux):
    r = rh.envoyer(quand=VENDREDI)
    assert r["envoye"] and r["notification"] and r["fil"]
    assert len(canaux["notify"]) == 1
    marque = json.loads(rh.MARQUEUR_PATH.read_text(encoding="utf-8"))
    assert marque["semaine"] == rh._semaine_iso(VENDREDI)


def test_ne_part_qu_une_fois_par_semaine(canaux):
    assert rh.envoyer(quand=VENDREDI)["envoye"]
    r = rh.envoyer(quand=VENDREDI)
    assert not r["envoye"] and "déjà servi" in r["raison"]
    assert len(canaux["notify"]) == 1


def test_force_part_n_importe_quel_jour_et_marque(canaux):
    r = rh.envoyer(force=True, quand=MERCREDI)
    assert r["envoye"]
    # l'essai forcé compte : sans marqueur, le vendredi suivant doublerait
    assert rh.MARQUEUR_PATH.exists()


def test_echec_des_deux_canaux_ne_marque_pas(monkeypatch):
    # le prochain passage doit RETENTER : un rapport perdu ne doit pas être
    # enregistré comme servi
    from marketlab import fil_alertes, notify
    monkeypatch.setattr(notify, "envoyer",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(fil_alertes, "publier",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    r = rh.envoyer(quand=VENDREDI)
    assert not r["envoye"]
    assert not rh.MARQUEUR_PATH.exists()


# -------------------------------------------------------------- la composition

def test_delta_semaine_sur_serie_equite():
    base = pd.Timestamp("2026-08-07")
    equity = [[(base - pd.Timedelta(days=j)).strftime("%Y-%m-%d %H:%M"), v]
              for j, v in [(10, 900.0), (6, 1000.0), (3, 1020.0), (0, 1050.0)]]
    equity.sort()
    # la fenêtre de 7 jours part de 1 000 (le point à J−6), pas de 900
    assert rh._delta_semaine_pct(equity) == pytest.approx(5.0)


def test_delta_semaine_serie_trop_courte():
    assert rh._delta_semaine_pct([["2026-08-07 22:00", 1000.0]]) is None
    assert rh._delta_semaine_pct([]) is None


def test_bloc_comptes_lit_l_experience():
    concours = {"comptes": [
        {"nom": "claude", "est_robot": True, "equite": 1000.0, "perf_%": 2.0,
         "equity": [], "n_positions": 1, "n_trades": 4},
        {"nom": "claude5", "est_robot": True, "equite": 985.0, "perf_%": -1.5,
         "equity": [], "n_positions": 0, "n_trades": 9},
        {"nom": "claudefx", "est_robot": True, "equite": 1002.0, "perf_%": 0.2,
         "equity": [], "n_positions": 0, "n_trades": 1},
        {"nom": "david", "est_robot": False, "equite": 1100.0, "perf_%": 10.0,
         "equity": [], "n_positions": 2, "n_trades": 3},
    ]}
    b = rh.bloc_comptes(concours)
    assert b["experience"]["horizon_court_pts"] == pytest.approx(-3.5)
    assert b["experience"]["specialisation_forex_pts"] == pytest.approx(-1.8)
    assert len(b["comptes"]) == 4


def test_bloc_chaine_ne_compte_que_la_semaine(tmp_path, monkeypatch):
    from marketlab import journal_chaine as jc
    monkeypatch.setattr(jc, "JOURNAL_PATH", tmp_path / "journal.csv")
    pd.DataFrame([
        {"date": "2026-08-06", "symbole": "A", "horizon": 20, "retenue": 1,
         "etape_fatale": ""},
        {"date": "2026-08-05", "symbole": "B", "horizon": 20, "retenue": 0,
         "etape_fatale": "frais"},
        {"date": "2026-07-15", "symbole": "C", "horizon": 20, "retenue": 0,
         "etape_fatale": "frais"},          # hors fenêtre : ignoré
    ]).to_csv(jc.JOURNAL_PATH, index=False)
    monkeypatch.setattr(jc, "bilan", lambda: {"murs": 0, "en_attente": 2,
                                              "lecture": "trop tôt"})
    b = rh.bloc_chaine(quand=pd.Timestamp("2026-08-07"))
    assert b["semaine"] == {"verdicts": 2, "retenus": 1,
                            "par_motif": {"frais": 1}}
    assert b["proces"]["lecture"] == "trop tôt"


def test_texte_assemble_les_blocs():
    rapport = {
        "semaine": "2026-S32",
        "comptes": {"disponible": True, "comptes": [
            {"nom": "claude", "equite": 1010.0, "semaine_%": 1.0}],
            "experience": {"horizon_court_pts": -3.5}},
        "chaine": {"disponible": True,
                   "semaine": {"verdicts": 28, "retenus": 0,
                               "par_motif": {"frais": 20, "regime": 8}},
                   "proces": {"murs": 3, "en_attente": 25,
                              "lecture": "trop tôt"}},
        "surveillance": {"disponible": True, "n": 0, "dernieres": []},
        "jalons": {"duels_iv": "0 duel(s) mûrs",
                   "regimes_suspendus": ["normal", "tendu"]},
    }
    t = rh.texte(rapport)
    assert "2026-S32" in t and "claude 1010.0 $" in t
    assert "l'horizon court fait -3.5 pts" in t.replace("−", "-")
    assert "28 verdicts" in t and "20 frais" in t
    assert "aucune garde déclenchée" in t
    assert "normal, tendu" in t
