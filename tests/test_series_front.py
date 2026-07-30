"""Règles de série côté navigateur (front/src/series.js), exercées sous node.

POURQUOI DES TESTS ICI. Ces fonctions ne mettent pas en forme, elles
TRANCHENT : laquelle de deux barres concurrentes fait foi, où commence une
séance, combien de décimales a un prix. Une erreur y produit un graphique
plausible et faux — le pire des deux mondes. Le même patron est déjà utilisé
pour l'invariant d'équité Python/PHP : le langage change, l'exigence non.
"""

import json
import shutil
import subprocess

import pytest

from marketlab import config

node = shutil.which("node")
pytestmark = pytest.mark.skipif(not node, reason="node absent de cette machine")

SERIES = (config.ROOT / "front" / "src" / "series.js").as_uri()


def _node(corps: str):
    """Exécute un fragment ESM qui importe series.js et imprime du JSON."""
    script = f'import {{ enBougies, fusionner, dernieresSeances, precision }} ' \
             f'from "{SERIES}";\n{corps}'
    r = subprocess.run([node, "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_colonnes_vers_bougies():
    out = _node('''
      const bloc = { t: ["2026-01-01", "2026-01-02"], o: [1, 2], h: [3, 4],
                     l: [0.5, 1.5], c: [2, 3] };
      console.log(JSON.stringify(enBougies(bloc)));
    ''')
    assert out == [
        {"time": "2026-01-01", "open": 1, "high": 3, "low": 0.5, "close": 2},
        {"time": "2026-01-02", "open": 2, "high": 4, "low": 1.5, "close": 3},
    ]


def test_bougie_sans_cloture_est_ignoree():
    """Les fournisseurs renvoient des trous sur les minutes sans transaction.
    Une bougie à zéro serait un mensonge visuel — un chandelier plongeant à
    l'origine, là où il ne s'est simplement rien passé."""
    out = _node('''
      const bloc = { t: [1, 2, 3], o: [1, null, 3], h: [1, null, 3],
                     l: [1, null, 3], c: [1, null, 3] };
      console.log(JSON.stringify(enBougies(bloc).map((b) => b.time)));
    ''')
    assert out == [1, 3]


def test_a_horodatage_egal_la_barre_fraiche_gagne():
    """LA règle du module. La barre publiée à 15 h 30 a été archivée alors que
    la bougie n'était pas finie ; celle du relais est complète. Garder
    l'ancienne afficherait un plus-haut de séance faux — de quoi croire qu'un
    objectif n'a pas été touché alors qu'il l'a été."""
    out = _node('''
      const publiee = { t: [100, 200], o: [1, 2], h: [1, 2.1], l: [1, 1.9], c: [1, 2] };
      const fraiche = { t: [200, 300], o: [2, 3], h: [2.9, 3.1], l: [1.9, 2.9], c: [2.8, 3] };
      const f = fusionner(publiee, fraiche);
      console.log(JSON.stringify({ t: f.t, h: f.h, c: f.c, n: f.n }));
    ''')
    assert out["t"] == [100, 200, 300]
    assert out["h"] == [1, 2.9, 3.1]      # la barre 200 vient de la fraîche
    assert out["c"] == [1, 2.8, 3]
    assert out["n"] == 3


def test_fusion_tolere_une_source_absente():
    out = _node('''
      const p = { t: [1], o: [1], h: [1], l: [1], c: [1] };
      console.log(JSON.stringify({
        sans_fraiche: fusionner(p, null)?.t,
        sans_publiee: fusionner(null, p)?.t,
        les_deux_absentes: fusionner(null, null),
      }));
    ''')
    assert out["sans_fraiche"] == [1]
    assert out["sans_publiee"] == [1]
    assert out["les_deux_absentes"] is None


def test_decoupe_par_seance_et_pas_par_nombre_de_barres():
    """« 1 jour » doit donner UNE séance, pas les 78 dernières barres : une
    séance écourtée ou un marché 24 h n'ont pas le même nombre de bougies."""
    out = _node('''
      const jour = 86400;
      // deux séances : 3 barres le jour 10, 2 barres le jour 11
      const t = [10 * jour, 10 * jour + 300, 10 * jour + 600,
                 11 * jour, 11 * jour + 300];
      const bloc = { t, o: t.map(() => 1), h: t.map(() => 1),
                     l: t.map(() => 1), c: t.map(() => 1) };
      console.log(JSON.stringify({
        une: dernieresSeances(bloc, 1).t,
        deux: dernieresSeances(bloc, 2).t.length,
      }));
    ''')
    assert out["une"] == [11 * 86400, 11 * 86400 + 300]
    assert out["deux"] == 5


def test_precision_suit_l_ordre_de_grandeur():
    """Une paire de change se lit à 5 décimales, une action à 2. Une valeur
    unique rendrait l'une illisible et l'autre faussement précise."""
    out = _node('''
      console.log(JSON.stringify([0.6543, 1.0854, 145.32, 33500].map(precision)));
    ''')
    assert out == [6, 4, 2, 2]
