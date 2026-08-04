"""Les pages PHP publiées doivent au moins être du PHP valide.

POURQUOI CE FICHIER EXISTE. Le 2026-08-04, `deploy/cours.php` est parti en
production avec une chaîne à guillemets simples contenant « l'âge » et
« c'est » : deux apostrophes non échappées qui terminaient la chaîne en plein
milieu. Le fichier ne se parsait plus, et RIEN ne l'a signalé — ni les tests,
ni l'audit de cohérence, ni la publication, qui a téléversé le fichier cassé
avec un run vert.

Le dépôt contenait pourtant déjà un test qui vérifie le JavaScript EMBARQUÉ
dans les pages PHP (`test_js_pages_php.py`), dont le commentaire d'en-tête
explique que « php -l valide le PHP et rien d'autre ». Personne n'avait
remarqué que ce `php -l` n'était nulle part.

Ce test-ci est donc le complément manquant : il lance `php -l` sur CHAQUE
fichier que la publication envoie réellement, la liste étant tirée de
`publish.RELAIS_PHP + publish.PAGES_ESPACES` et non recopiée — un fichier
ajouté à la publication est couvert d'office.

Sur un poste sans PHP le test se saute proprement ; le runner de CI en a un,
et c'est lui qui garde la porte avant l'envoi FTPS.
"""

import shutil
import subprocess

import pytest

from marketlab import config, publish

PHP = shutil.which("php")
PAGES = list(publish.RELAIS_PHP) + list(publish.PAGES_ESPACES)


@pytest.mark.skipif(PHP is None, reason="php absent de ce poste")
@pytest.mark.parametrize("nom", PAGES)
def test_la_page_php_se_parse(nom):
    chemin = config.ROOT / "deploy" / nom
    if not chemin.is_file():
        pytest.skip(f"{nom} absent de deploy/")
    r = subprocess.run([PHP, "-l", str(chemin)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"{nom} ne se parse pas :\n{r.stdout}\n{r.stderr}")


@pytest.mark.skipif(PHP is None, reason="php absent de ce poste")
def test_le_linter_detecte_vraiment_une_erreur(tmp_path):
    """Un contrôle de syntaxe qui ne peut pas échouer ne protège rien.

    On lui présente EXACTEMENT le défaut qui est passé en production : une
    apostrophe française dans une chaîne à guillemets simples.
    """
    casse = tmp_path / "casse.php"
    casse.write_text("<?php $x = 'donne l'age reel'; ?>", encoding="utf-8")
    r = subprocess.run([PHP, "-l", str(casse)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0, "le linter laisse passer une apostrophe non échappée"


def test_la_liste_vient_de_la_publication():
    """Recopier la liste des pages ici la ferait diverger : une page ajoutée à
    la publication passerait alors sans contrôle."""
    assert PAGES, "aucune page PHP déclarée à la publication"
    assert all(n.endswith(".php") for n in PAGES)
