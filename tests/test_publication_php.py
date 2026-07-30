"""Une page PHP publiée ne doit jamais exiger un fichier qui ne l'est pas.

INCIDENT DU 2026-07-30. `admin/index.php` a reçu un
`require __DIR__ . '/comptes_lib.php'` sans que le fichier soit ajouté à
`PAGES_ESPACES` — la liste est explicite, fichier par fichier, pour ne jamais
risquer d'écraser `trading/comptes/` en copiant un dossier entier. La
publication a donc mis en ligne un index qui exige un fichier absent : erreur
fatale PHP, panneau d'administration injoignable. Rien ne l'avait signalé,
puisque côté dépôt tout était cohérent.
"""

import pytest

from marketlab import publish


def test_toutes_les_dependances_declarees_sont_publiees():
    """Le garde-fou sur le vrai contenu du dépôt : si quelqu'un ajoute un
    require sans compléter la liste, ce test tombe avant la publication."""
    copies = publish.copier_php()
    assert publish._dependances_manquantes(copies) == []


def test_les_nouvelles_pages_sont_bien_declarees():
    for page in ("admin/comptes_lib.php", "admin/compte.php",
                 "admin/comparer.php"):
        assert page in publish.PAGES_ESPACES, f"{page} absente de PAGES_ESPACES"


def test_une_dependance_manquante_est_detectee(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "RACINE_SITE", tmp_path)
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "index.php").write_text(
        "<?php require __DIR__ . '/absent.php'; ?>", encoding="utf-8")
    manquantes = publish._dependances_manquantes(["admin/index.php"])
    assert manquantes == ["admin/index.php exige admin/absent.php"]


def test_une_dependance_publiee_ne_declenche_rien(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "RACINE_SITE", tmp_path)
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "index.php").write_text(
        "<?php require __DIR__ . '/commun.php'; ?>", encoding="utf-8")
    (tmp_path / "admin" / "commun.php").write_text("<?php", encoding="utf-8")
    assert publish._dependances_manquantes(
        ["admin/index.php", "admin/commun.php"]) == []


def test_les_chemins_relatifs_sont_normalises(tmp_path, monkeypatch):
    """`trading/index.php` exige `__DIR__ . '/../cours_lib.php'`, qui désigne
    le fichier de la racine, déjà publié. Sans normalisation des « .. », le
    garde-fou criait au loup sur trois pages parfaitement correctes."""
    monkeypatch.setattr(publish, "RACINE_SITE", tmp_path)
    (tmp_path / "trading").mkdir()
    (tmp_path / "trading" / "index.php").write_text(
        "<?php require __DIR__ . '/../cours_lib.php';", encoding="utf-8")
    (tmp_path / "cours_lib.php").write_text("<?php", encoding="utf-8")
    assert publish._dependances_manquantes(
        ["trading/index.php", "cours_lib.php"]) == []


def test_require_once_et_include_sont_couverts(tmp_path, monkeypatch):
    """Les quatre formes existent dans le dépôt : require, require_once,
    include, include_once."""
    monkeypatch.setattr(publish, "RACINE_SITE", tmp_path)
    (tmp_path / "a.php").write_text(
        "<?php require_once __DIR__ . '/x.php';\n"
        "include __DIR__ . '/y.php';\n"
        "include_once __DIR__ . '/z.php';", encoding="utf-8")
    manquantes = publish._dependances_manquantes(["a.php"])
    assert sorted(manquantes) == ["a.php exige x.php", "a.php exige y.php",
                                  "a.php exige z.php"]


def test_copier_php_leve_si_une_dependance_manque(tmp_path, monkeypatch):
    """Mieux vaut une publication qui échoue bruyamment qu'un espace en erreur
    fatale que personne ne verra avant de s'y connecter."""
    depot = tmp_path / "depot"
    (depot / "deploy" / "admin").mkdir(parents=True)
    (depot / "deploy" / "admin" / "index.php").write_text(
        "<?php require __DIR__ . '/oublie.php';", encoding="utf-8")
    monkeypatch.setattr(publish.config, "ROOT", depot)
    monkeypatch.setattr(publish, "RACINE_SITE", tmp_path / "site")
    monkeypatch.setattr(publish, "RELAIS_PHP", [])
    monkeypatch.setattr(publish, "PAGES_ESPACES", ["admin/index.php"])
    with pytest.raises(RuntimeError, match="PAGES_ESPACES"):
        publish.copier_php()
