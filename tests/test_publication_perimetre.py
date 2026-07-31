"""Ce que la publication a le droit d'envoyer — et ce qu'elle ne doit JAMAIS.

Ce test protège contre une perte de données, pas contre une régression
d'affichage. `site/trading/comptes/` sur l'hébergement contient les
portefeuilles réels : soldes, positions ouvertes, historique des robots. Ces
fichiers n'existent nulle part ailleurs. Une vérification locale qui laisse un
compte de test dans `site/`, et l'envoi FTPS écrase le vrai compte du même nom.

La règle est donc inversée sur cette arborescence : c'est l'hébergement qui
fait autorité, jamais le poste de travail. Elle est trop importante pour
tenir dans un commentaire.
"""


from marketlab import config, ftps, publish


def test_les_comptes_de_trading_ne_partent_jamais():
    racine = config.ROOT / "site"
    for chemin in ("trading/comptes/claude.json",
                   "trading/comptes/dav.json",
                   "trading/comptes/sous/dossier.json"):
        assert ftps._a_exclure(racine / chemin, racine), chemin


def test_les_sessions_caches_et_outils_locaux_ne_partent_pas():
    racine = config.ROOT / "site"
    for chemin in ("admin/sessions/abc.json", "cache/cours.json",
                   "cache/serie_AAPL_5m.json", "routeur-essai.php"):
        assert ftps._a_exclure(racine / chemin, racine), chemin


def test_le_site_lui_meme_part_bien():
    """Le garde-fou doit rester étroit : tout exclure serait aussi un bug."""
    racine = config.ROOT / "site"
    for chemin in ("index.html", "donnees/verdicts.json",
                   "donnees/series/AAPL.json", "trading/index.php",
                   "admin/index.php", "serie.php",
                   "marketlab-graphique.js"):
        assert not ftps._a_exclure(racine / chemin, racine), chemin


def test_un_nom_qui_commence_pareil_n_est_pas_exclu():
    """« cachette.json » n'est pas « cache/ » : l'exclusion se fait sur des
    segments de chemin entiers, pas sur un préfixe de texte."""
    racine = config.ROOT / "site"
    assert not ftps._a_exclure(racine / "cachette.json", racine)
    assert not ftps._a_exclure(racine / "trading/comptesrendus.json", racine)


def test_seuls_des_fichiers_php_nommes_sont_publies():
    """Jamais un dossier : c'est ce qui garantit qu'aucun état ne se glisse
    dans la copie depuis `deploy/`."""
    for nom in publish.RELAIS_PHP + publish.PAGES_ESPACES:
        assert nom.endswith(".php"), nom
        assert ".." not in nom, nom
        source = config.ROOT / "deploy" / nom
        assert source.is_file() or not source.exists(), nom
        if source.exists():
            assert source.is_file(), f"{nom} doit être un fichier, pas un dossier"


def test_les_htaccess_restent_a_l_hebergement():
    """Les .htaccess portent l'authentification du site. Les republier depuis
    le dépôt à chaque nuit, c'est prendre le risque de couper l'accès sur une
    différence de configuration serveur."""
    tous = publish.RELAIS_PHP + publish.PAGES_ESPACES
    assert not [n for n in tous if "htaccess" in n]


def test_le_module_graphique_est_bien_celui_du_site():
    """L'espace de trading et le site doivent afficher le MÊME graphique.
    Le module autonome est donc construit depuis les mêmes sources ; si ce
    point d'entrée disparaissait, la page de trading se retrouverait sans
    graphique sans que rien n'échoue."""
    entree = config.ROOT / "front" / "src" / "graphique-autonome.js"
    assert entree.is_file()
    texte = entree.read_text(encoding="utf-8")
    assert './terminal-chart"' in texte, "le noyau partagé doit être importé"
    assert './series"' in texte, "les règles de série doivent être importées"


def test_les_chemins_du_module_remontent_au_bon_niveau():
    """L'espace de trading vit dans un sous-dossier : le module doit viser la
    racine du site pour trouver `donnees/` et `serie.php`. Un chemin absolu
    casserait l'installation en sous-dossier, un chemin sans remontée
    chercherait `trading/donnees/`."""
    texte = (config.ROOT / "front" / "src" / "graphique-autonome.js") \
        .read_text(encoding="utf-8")
    assert 'const RACINE = "../";' in texte
