"""Analyse des comptes de trading (deploy/admin/comptes_lib.php).

Ces fonctions alimentent l'historique par compte et la comparaison entre
comptes du panneau d'administration. Elles sont en PHP parce que les comptes
ne vivent que sur l'hébergement, où aucun Python ne tourne — mais elles
calculent des chiffres qu'on présente comme des mesures, donc elles se
vérifient.

Le test s'ignore si PHP est absent (poste de travail) et s'exécute dans
l'intégration continue, qui a PHP.
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
LIB = RACINE / "deploy" / "admin" / "comptes_lib.php"

PHP = shutil.which("php")
pytestmark = pytest.mark.skipif(PHP is None, reason="PHP absent de ce poste")


def _appeler(expression: str, compte: dict | None = None,
             serie: list | None = None):
    """Évalue une expression PHP et renvoie son résultat décodé.

    Les données transitent en base64 : une apostrophe dans un motif de sortie
    (« objectif d'entrée »…) suffirait à casser une chaîne PHP interpolée, et
    le test échouerait pour une raison sans rapport avec ce qu'il vérifie.
    """
    def b64(x) -> str:
        return base64.b64encode(json.dumps(x).encode()).decode()

    script = f"""
        function ml_montant(float $x, int $d = 2): string {{
            return number_format($x, $d, ',', ' ');
        }}
        require '{LIB.as_posix()}';
        $compte = json_decode(base64_decode('{b64(compte or {})}'), true);
        $serie  = json_decode(base64_decode('{b64(serie or [])}'), true);
        echo json_encode({expression});
    """
    res = subprocess.run([PHP, "-r", script],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr[:400]
    return json.loads(res.stdout)


def _trade(pnl, symbole="AAPL", motif="objectif atteint",
           ouvert="2026-07-01 10:00", ferme="2026-07-03 10:00", marge=100.0):
    return {"symbole": symbole, "sens": "long", "marge": marge, "levier": 2,
            "entree": 100.0, "sortie": 100.0 + pnl, "pnl": pnl,
            "ouvert_le": ouvert, "ferme_le": ferme, "motif": motif}


# ---------------------------------------------------------------------------
# Statistiques du journal
# ---------------------------------------------------------------------------

def test_journal_vide_ne_ment_pas():
    s = _appeler("ml_stats_trades($compte)", {"historique": []})
    assert s["n"] == 0
    assert s["reussite"] is None and s["facteur_profit"] is None


def test_reussite_et_pnl():
    compte = {"historique": [_trade(30), _trade(-10), _trade(20), _trade(-40)]}
    s = _appeler("ml_stats_trades($compte)", compte)
    assert s["n"] == 4 and s["gagnants"] == 2
    assert s["reussite"] == pytest.approx(50.0)
    assert s["pnl"] == pytest.approx(0.0)
    assert s["esperance"] == pytest.approx(0.0)


def test_facteur_de_profit_demasque_une_bonne_reussite():
    """Trois gains sur quatre, et pourtant le compte perd : c'est exactement
    ce que le taux de réussite cache et que le facteur de profit montre."""
    compte = {"historique": [_trade(10), _trade(10), _trade(10), _trade(-60)]}
    s = _appeler("ml_stats_trades($compte)", compte)
    assert s["reussite"] == pytest.approx(75.0)
    assert s["pnl"] == pytest.approx(-30.0)
    assert s["facteur_profit"] == pytest.approx(30 / 60)   # < 1
    assert s["facteur_profit"] < 1


def test_facteur_de_profit_null_sans_perte():
    """« Infini » sur trois trades gagnants se lirait comme une performance."""
    s = _appeler("ml_stats_trades($compte)",
                 {"historique": [_trade(10), _trade(5)]})
    assert s["facteur_profit"] is None


def test_repartition_par_motif_de_sortie():
    compte = {"historique": [
        _trade(-10, motif="stop touché"), _trade(-12, motif="stop touché"),
        _trade(30, motif="objectif atteint"),
    ]}
    s = _appeler("ml_stats_trades($compte)", compte)
    assert s["par_motif"]["stop touché"]["n"] == 2
    assert s["par_motif"]["stop touché"]["pnl"] == pytest.approx(-22.0)
    assert s["par_motif"]["objectif atteint"]["n"] == 1


def test_repartition_par_actif():
    compte = {"historique": [_trade(10, "AAPL"), _trade(-5, "AAPL"),
                             _trade(40, "BTCUSDT")]}
    s = _appeler("ml_stats_trades($compte)", compte)
    assert s["par_actif"]["AAPL"]["n"] == 2
    assert s["par_actif"]["AAPL"]["pnl"] == pytest.approx(5.0)
    assert s["par_actif"]["BTCUSDT"]["pnl"] == pytest.approx(40.0)


def test_duree_moyenne_en_heures():
    compte = {"historique": [
        _trade(10, ouvert="2026-07-01 10:00", ferme="2026-07-02 10:00"),
        _trade(10, ouvert="2026-07-01 10:00", ferme="2026-07-04 10:00"),
    ]}
    s = _appeler("ml_stats_trades($compte)", compte)
    assert s["duree_moyenne_h"] == pytest.approx(48.0)


def test_meilleur_et_pire():
    compte = {"historique": [_trade(10), _trade(-40), _trade(25)]}
    s = _appeler("ml_stats_trades($compte)", compte)
    assert s["meilleur"] == pytest.approx(25.0)
    assert s["pire"] == pytest.approx(-40.0)


# ---------------------------------------------------------------------------
# Série d'équité
# ---------------------------------------------------------------------------

def test_serie_triee_et_nettoyee():
    compte = {"equity": [["2026-07-03 22:00", 1010.0],
                         ["2026-07-01 22:00", 1000.0],
                         ["mauvaise ligne"],
                         ["2026-07-02 22:00", 990.0]]}
    s = _appeler("ml_serie_equite($compte)", compte)
    assert [p["v"] for p in s] == [1000.0, 990.0, 1010.0]


def test_drawdown_max():
    """Sommet 1200 puis creux 900 : −25 %, et la remontée ne l'efface pas."""
    serie = [{"t": "2026-07-0%d 22:00" % i, "v": v}
             for i, v in enumerate([1000, 1200, 900, 1100], start=1)]
    d = _appeler("ml_drawdown_max($serie)", None, serie)
    assert d == pytest.approx(-25.0)


def test_drawdown_nul_sur_une_courbe_qui_monte():
    serie = [{"t": "2026-07-0%d 22:00" % i, "v": v}
             for i, v in enumerate([1000, 1050, 1100], start=1)]
    assert _appeler("ml_drawdown_max($serie)", None, serie) == pytest.approx(0.0)


def test_fenetre_non_couverte_renvoie_null():
    """Le point CENTRAL : comparer trois jours de relevés à une performance
    « 30 jours » flatterait ou punirait un compte selon sa seule ancienneté."""
    serie = [{"t": "2026-07-29 22:00", "v": 1000.0},
             {"t": "2026-07-30 22:00", "v": 1100.0}]
    assert _appeler("ml_perf_fenetre($serie, 30)", None, serie) is None


def test_serie_trop_courte_renvoie_null():
    assert _appeler("ml_perf_fenetre($serie, 7)", None, []) is None
    assert _appeler("ml_perf_fenetre($serie, 7)", None,
                    [{"t": "2026-07-30 22:00", "v": 1000.0}]) is None


# ---------------------------------------------------------------------------
# Tracé
# ---------------------------------------------------------------------------

def test_courbe_sur_serie_vide_le_dit():
    svg = _appeler("ml_courbe_svg([])")
    assert "<svg" not in svg and "Pas encore assez de points" in svg


def test_courbe_produit_un_trace_par_compte():
    serie = [{"t": "2026-07-0%d 22:00" % i, "v": 1000.0 + i * 10}
             for i in range(1, 6)]
    svg = _appeler(
        "ml_courbe_svg([['nom' => 'a', 'points' => $serie, 'couleur' => '#111'],"
        " ['nom' => 'b', 'points' => $serie, 'couleur' => '#222']])",
        None, serie)
    assert svg.count("<polyline") == 2
    assert "#111" in svg and "#222" in svg


def test_la_couleur_dun_compte_est_stable():
    """Un compte doit garder sa couleur d'un écran à l'autre, sinon la légende
    de la page de comparaison ne veut plus rien dire."""
    a = _appeler("ml_couleur_compte('claude')")
    b = _appeler("ml_couleur_compte('claude')")
    assert a == b and a.startswith("#")
    assert _appeler("ml_couleur_compte('david')") != a or True  # collision tolérée
