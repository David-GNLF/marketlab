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


def test_les_couleurs_sont_toutes_distinctes():
    """DÉFAUT CONSTATÉ À L'ÉCRAN : la couleur venait d'un hachage du nom
    (crc32 % 8) et deux comptes sur cinq — frejus et claude5 — ont reçu le
    même orange. Une légende où deux entrées portent la même pastille ne sert
    plus à rien."""
    noms = ["claude", "claude5", "claudefx", "david", "frejus"]
    liste = "['" + "', '".join(noms) + "']"
    c = _appeler(f"ml_couleurs_comptes({liste})")
    assert len(set(c.values())) == len(noms)


def test_la_couleur_dun_compte_est_stable():
    """Elle ne doit pas changer d'une page à l'autre, sinon on ne peut plus
    relier une courbe à sa légende."""
    noms = "['claude', 'claude5', 'david']"
    a = _appeler(f"ml_couleurs_comptes({noms})['claude']")
    b = _appeler(f"ml_couleur_compte('claude', {noms})")
    assert a == b and a.startswith("#")


# ---------------------------------------------------------------------------
# Affichage des prix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valeur,attendu_decimales", [
    (2224.1516, 2), (321.82083, 3), (6.395196, 4), (0.5432198, 5),
])
def test_le_prix_sadapte_a_sa_grandeur(valeur, attendu_decimales):
    """Les cours arrivent bruts du relais. Huit décimales sur un indice à
    2 224 donnent une fausse impression de précision."""
    rendu = _appeler(f"ml_prix({valeur})")
    assert rendu.count(",") == 1
    assert len(rendu.split(",")[1]) == attendu_decimales


def test_un_prix_minuscule_garde_ses_chiffres():
    rendu = _appeler("ml_prix(0.00003214)")
    assert "3214" in rendu.replace(" ", "").replace(" ", "")


# ---------------------------------------------------------------------------
# Détail d'une position ouverte
# ---------------------------------------------------------------------------

def _position(**kw):
    base = {"symbole": "AAPL", "sens": "long", "marge": 50.0, "levier": 2,
            "quantite": 1.0, "prix_entree": 100.0, "stop": 90.0,
            "objectif": 120.0}
    base.update(kw)
    return base


def test_pnl_latent_et_variation_sont_distincts():
    """Le titre monte de 10 %, mais la position est à effet deux : le gain sur
    la MISE est de 20 %. Confondre les deux fait lire un risque pour un autre."""
    d = _appeler("ml_position_detail($compte, ['prix' => 110.0])",
                 _position(quantite=1.0))
    assert d["variation_%"] == pytest.approx(10.0)
    assert d["pnl"] == pytest.approx(10.0)
    assert d["pnl_%_marge"] == pytest.approx(20.0)


def test_le_sens_court_inverse_la_variation():
    d = _appeler("ml_position_detail($compte, ['prix' => 90.0])",
                 _position(sens="short"))
    assert d["variation_%"] == pytest.approx(10.0)   # baisse = gain
    assert d["pnl"] > 0


def test_prix_de_liquidation():
    """La mise est perdue quand le cours a parcouru 1/levier depuis l'entrée."""
    d = _appeler("ml_position_detail($compte, null)", _position(levier=2))
    assert d["liquidation"] == pytest.approx(50.0)
    d = _appeler("ml_position_detail($compte, null)", _position(levier=5))
    assert d["liquidation"] == pytest.approx(80.0)


def test_distances_mesurees_depuis_le_cours_actuel():
    """C'est ce qu'il RESTE à parcourir qui compte, pas l'écart d'origine."""
    d = _appeler("ml_position_detail($compte, ['prix' => 110.0])", _position())
    assert d["vers_stop_%"] == pytest.approx(90 / 110 * 100 - 100)
    assert d["vers_objectif_%"] == pytest.approx(120 / 110 * 100 - 100)
    # ratio = ce qui reste à gagner ÷ ce qu'on risque encore
    assert d["ratio"] == pytest.approx(
        abs(120 / 110 - 1) / abs(90 / 110 - 1), rel=1e-6)


def test_position_sans_cotation_ne_ment_pas():
    """Un symbole qui n'a pas répondu ne doit pas produire un P&L de zéro,
    qui se lirait comme « position à l'équilibre »."""
    d = _appeler("ml_position_detail($compte, null)", _position())
    assert d["pnl"] is None and d["variation_%"] is None
    assert d["notionnel"] == pytest.approx(100.0)   # marge × levier


# ---------------------------------------------------------------------------
# Filtrage par période
# ---------------------------------------------------------------------------

def test_filtrer_sur_tout_ne_retire_rien():
    serie = [{"t": "2020-01-01 22:00", "v": 1000.0},
             {"t": "2026-07-30 22:00", "v": 1100.0}]
    assert len(_appeler("ml_filtrer_serie($serie, null)", None, serie)) == 2


def test_filtrer_ecarte_les_points_trop_anciens():
    """INCIDENT DES 07-09/08/2026 : ce test portait une date EN DUR
    (« 2026-07-30 ») face à un filtre « 7 derniers jours » calculé sur
    l'horloge RÉELLE. Écrit le 30 juillet, il était condamné à mourir le
    7 août — et comme les tests verrouillent la publication, il a gelé le
    site ET la tenue des comptes trois nuits de suite. Troisième incident
    de la même famille en une semaine : face à une fenêtre sur l'horloge
    réelle, le point « récent » se calcule depuis MAINTENANT, jamais depuis
    le jour où le test fut écrit."""
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    serie = [{"t": "2020-01-01 22:00", "v": 1000.0},
             {"t": recent, "v": 1100.0}]
    garde = _appeler("ml_filtrer_serie($serie, 7)", None, serie)
    assert len(garde) == 1 and garde[0]["v"] == 1100.0


# ---------------------------------------------------------------------------
# Tracé enrichi
# ---------------------------------------------------------------------------

def test_un_marqueur_par_releve():
    """Quatre points reliés sans marqueurs se lisent comme une mesure
    continue : le trait suggère une densité de données qui n'existe pas."""
    serie = [{"t": "2026-07-0%d 22:00" % i, "v": 1000.0 + i}
             for i in range(1, 6)]
    svg = _appeler("ml_courbe_svg([['nom' => 'a', 'points' => $serie]])",
                   None, serie)
    assert svg.count("<circle") >= 5
    assert "<title>" in svg          # infobulle native, sans JavaScript


def test_la_grille_est_chiffree():
    serie = [{"t": "2026-07-0%d 22:00" % i, "v": 1000.0 + i}
             for i in range(1, 6)]
    svg = _appeler("ml_courbe_svg([['nom' => 'a', 'points' => $serie]])",
                   None, serie)
    assert svg.count("<line") >= 5   # 5 graduations horizontales
    assert svg.count("<text") >= 7   # valeurs + dates


# ---------------------------------------------------------------------------
# Vos biais : le moteur de mesure retourné sur vos propres décisions
# ---------------------------------------------------------------------------

def test_sous_le_seuil_aucun_biais_nest_annonce():
    """Conclure sur trois trades serait pire que ne rien dire."""
    b = _appeler("ml_biais_trader($compte)",
                 {"historique": [_trade(10), _trade(-5)]})
    assert b["assez"] is False
    assert "rien de mesurable" in b["message"]


def test_les_groupes_trop_petits_sont_marques_non_fiables():
    """Un écart sur deux trades est du bruit : la ligne existe, mais elle est
    signalée comme telle plutôt que commentée."""
    trades = [_trade(10, "AAPL") for _ in range(6)] + [_trade(-40, "GC=F")]
    b = _appeler("ml_biais_trader($compte)", {"historique": trades})
    par_classe = {l["valeur"]: l for l in b["classe"]}
    assert par_classe["Actions"]["fiable"] is True
    assert par_classe["Matières"]["fiable"] is False


def test_un_ecart_net_entre_classes_est_constate():
    """Le cas utile : une classe rapporte, l'autre coûte, et les deux ont assez
    de trades pour qu'on puisse le dire."""
    trades = ([_trade(30, "AAPL", marge=100.0) for _ in range(6)]
              + [_trade(-40, "GC=F", marge=100.0) for _ in range(6)])
    b = _appeler("ml_biais_trader($compte)", {"historique": trades})
    assert b["assez"] is True
    constats = " ".join(b["constats"])
    assert "classes d'actif" in constats
    assert "Actions" in constats and "Matières" in constats


def test_sans_ecart_marque_on_le_dit_aussi():
    """« Rien ne ressort » est une information, pas un échec de la mesure."""
    trades = [_trade(10, "AAPL", marge=100.0) for _ in range(6)] + \
             [_trade(11, "MSFT", marge=100.0) for _ in range(6)]
    b = _appeler("ml_biais_trader($compte)", {"historique": trades})
    assert any("Aucun écart marqué" in c for c in b["constats"])


def test_le_rendement_est_rapporte_a_la_mise():
    """Sans cela, un gros trade écrase les autres et le classement ne mesure
    plus que la taille des positions."""
    trades = ([_trade(50, "AAPL", marge=1000.0) for _ in range(5)]      # +5 %
              + [_trade(20, "GC=F", marge=100.0) for _ in range(5)])    # +20 %
    b = _appeler("ml_biais_trader($compte)", {"historique": trades})
    par_classe = {l["valeur"]: l for l in b["classe"]}
    # en dollars AAPL gagne (250 contre 100), en rendement sur mise c'est GC=F
    assert par_classe["Actions"]["pnl"] > par_classe["Matières"]["pnl"]
    assert par_classe["Matières"]["rendement_moyen"] > par_classe["Actions"]["rendement_moyen"]


def test_les_quatre_axes_sont_produits():
    trades = [_trade(10, "AAPL", marge=100.0) for _ in range(6)]
    b = _appeler("ml_biais_trader($compte)", {"historique": trades})
    for axe in ("classe", "duree", "sens", "jour"):
        assert axe in b and b[axe], f"axe manquant : {axe}"
