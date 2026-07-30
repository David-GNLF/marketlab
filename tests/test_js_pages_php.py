"""Le JavaScript embarqué dans les pages PHP doit au moins être analysable.

POURQUOI CE TEST EXISTE. `php -l` valide le PHP et rien d'autre : le
JavaScript inline lui est totalement opaque. Le 2026-07-30, une apostrophe mal
échappée dans une chaîne a produit une page dont le PHP était parfaitement
valide et dont TOUT le JavaScript était mort — filtres du tableau, graphique,
tracé du plan. Silencieusement : pas d'erreur serveur, pas de page blanche,
juste des boutons qui ne répondent plus.

Une erreur de syntaxe dans un bloc `<script>` ne tue pas la ligne fautive :
elle tue le bloc ENTIER. C'est ce qui rend ce défaut coûteux, et facile à ne
pas voir en relisant.

LES BALISES PHP SONT NEUTRALISÉES. Le bloc de script contient des valeurs
injectées par PHP. Les laisser telles quelles ferait échouer l'analyse sur du
PHP parfaitement valide ; exécuter la page pour les résoudre demanderait de
reconstituer tout son environnement (comptes, sessions, données publiées).
Chaque balise est donc remplacée par un littéral neutre : ce qui reste est la
STRUCTURE du script — exactement là où vivent les erreurs de guillemet, de
parenthèse et d'accolade que ce test doit attraper.

Le test ne juge pas le comportement, seulement que le navigateur pourra
analyser le fichier. C'est un plancher, pas un plafond.
"""

import re
import shutil
import subprocess

import pytest

from marketlab import config

node = shutil.which("node")
pytestmark = pytest.mark.skipif(not node, reason="node absent de cette machine")

PAGES = ["trading/index.php", "admin/index.php", "acces/index.php"]

# Une balise PHP dans du JavaScript produit toujours une VALEUR : un nombre,
# le contenu d'une chaîne déjà entre guillemets dans le source, un fragment de
# JSON. La remplacer par 0 garde la phrase grammaticalement entière.
BALISE_PHP = re.compile(r"<\?(?:php|=)?.*?\?>", re.S)
BLOC_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def js_inline(chemin) -> str:
    """Le JavaScript des blocs <script> d'une page, balises PHP neutralisées."""
    texte = chemin.read_text(encoding="utf-8")
    return "\n".join(BALISE_PHP.sub("0", b)
                     for b in BLOC_SCRIPT.findall(texte))


@pytest.mark.parametrize("page", PAGES)
def test_le_javascript_inline_est_analysable(page, tmp_path):
    chemin = config.ROOT / "deploy" / page
    if not chemin.is_file():
        pytest.skip(f"{page} absent")
    js = js_inline(chemin)
    if not js.strip():
        pytest.skip(f"{page} n'a pas de JavaScript inline")

    fichier = tmp_path / "inline.js"
    fichier.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(fichier)],
                       capture_output=True, timeout=60)
    assert r.returncode == 0, (
        f"JavaScript invalide dans {page} — le navigateur n'exécutera AUCUN "
        "script de cette page :\n"
        + r.stderr.decode("utf-8", errors="replace")[:700])


def test_l_espace_de_trading_a_bien_son_javascript():
    """Garde-fou du garde-fou : si l'extraction cessait de trouver quoi que ce
    soit, le test au-dessus passerait en ne vérifiant rien."""
    js = js_inline(config.ROOT / "deploy" / "trading" / "index.php")
    assert len(js) > 5000, (
        "JavaScript de l'espace de trading suspicieusement court "
        f"({len(js)} caractères) : l'extraction est cassée")
    assert "MarketLabGraphique" in js, (
        "le graphique n'est plus branché sur la page de trading")


def test_le_garde_fou_attrape_bien_le_defaut_qui_l_a_motive(tmp_path):
    """Le défaut EXACT du 2026-07-30, rejoué.

    Un test qui ne peut pas échouer ne protège rien : celui-ci vérifie que
    l'outil employé refuse bien une apostrophe non échappée.
    """
    fichier = tmp_path / "fautif.js"
    fichier.write_text("const m = 'plutôt que d'afficher ceci';",
                       encoding="utf-8")
    r = subprocess.run([node, "--check", str(fichier)],
                       capture_output=True, timeout=60)
    assert r.returncode != 0
