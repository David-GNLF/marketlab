"""L'espace DevApp : des faits constatés, et surtout aucun secret.

Le test le plus important de ce fichier est celui des secrets. Cette page est
publiée sur un site en ligne : elle traverse le réseau, elle est mise en cache
par l'hébergeur, elle est lisible par quiconque a l'adresse. Une clé d'API qui
s'y glisserait, même par accident, même partiellement, serait exposée pour de
bon — on ne rattrape pas une valeur déjà servie.

Le reste vérifie que la page ne peut pas mentir par inertie : les horaires des
tâches sont LUS dans les workflows, la feuille de route dans un fichier du
dépôt. Une page qui recopie ces informations devient fausse à la première
modification qu'on oublie d'y reporter, sans que rien ne le signale.
"""

import json

from marketlab import config, devapp

# Fragments qui n'ont rien à faire dans un fichier publié.
INTERDITS = [
    "MARKETLAB_FTP_MOT_DE_PASSE", "mot_de_passe", "password", "secret",
    "api_key", "apikey", "token", "ntfy.sh/",
]


def test_aucun_secret_dans_le_bloc_publie():
    """Le bloc entier est sérialisé puis fouillé, valeurs comprises."""
    texte = json.dumps(devapp.etat(), ensure_ascii=False, default=str).lower()
    trouves = [m for m in INTERDITS if m.lower() in texte]
    assert not trouves, (
        f"fragments sensibles dans devapp.json : {trouves} — cette page est "
        "publiée en ligne")


def test_les_cles_ne_sont_qu_un_booleen():
    """Ni la valeur, ni sa longueur, ni un fragment : juste présent ou non."""
    for cle in devapp.sources()["cles"]:
        assert set(cle) == {"nom", "role", "configuree"}, cle
        assert isinstance(cle["configuree"], bool)


def test_les_horaires_viennent_des_workflows():
    """Recopier un horaire dans une page, c'est garantir qu'il sera faux le
    jour où le workflow changera sans qu'on y pense."""
    auto = devapp.automatisation()
    fichiers = {t["fichier"] for t in auto["taches"]}
    assert "publication.yml" in fichiers
    publication = next(t for t in auto["taches"]
                       if t["fichier"] == "publication.yml")
    reel = (config.ROOT / ".github" / "workflows" / "publication.yml") \
        .read_text(encoding="utf-8")
    for cron in publication["crons_utc"]:
        assert f'"{cron}"' in reel


def test_la_feuille_de_route_vient_du_depot():
    route = devapp.feuille_de_route()
    assert route, "docs/FEUILLE_DE_ROUTE.md introuvable ou illisible"
    assert all(set(r) == {"section", "livre", "texte"} for r in route)
    assert any(r["livre"] for r in route)
    # Une feuille de route entièrement cochée serait suspecte : elle voudrait
    # dire qu'on ne note plus ce qui reste à faire.
    assert any(not r["livre"] for r in route)


def test_le_compte_de_tests_ne_se_gonfle_pas():
    """Il compte des fonctions `def test_`, une grandeur exacte. Une version
    précédente devinait les cas paramétrés en comptant les virgules et
    annonçait 275 là où pytest en exécute 234."""
    t = devapp.tests()
    assert t["fichiers"] > 0 and t["fonctions"] > 0
    reel = sum(
        f.read_text(encoding="utf-8", errors="ignore").count("\ndef test_")
        + f.read_text(encoding="utf-8", errors="ignore").startswith("def test_")
        for f in (config.ROOT / "tests").glob("test_*.py"))
    assert t["fonctions"] == reel


def test_le_perimetre_compte_ce_qui_est_sur_le_disque():
    """Pas la config : ce qui a RÉELLEMENT été écrit. C'est toute la valeur du
    tableau de bord — annoncer 36 séries alors que 6 sont publiées serait
    exactement le mensonge qu'il doit empêcher."""
    p = devapp.perimetre()
    dossier = config.ROOT / "site" / "donnees" / "series"
    attendu = len(list(dossier.glob("*.json"))) if dossier.exists() else 0
    assert p["series_publiees"] == attendu
    assert p["fiches_attendues"] == len(config.FICHES)
    assert p["actifs_suivis"] == len(config.SUIVIS)


def test_l_etat_complet_est_serialisable():
    """Il finit dans un fichier JSON lu par un navigateur : un NaN ou un objet
    non sérialisable le rendrait illisible EN ENTIER."""
    texte = json.dumps(devapp.etat(), ensure_ascii=False, default=str)
    assert "NaN" not in texte
    json.loads(texte)
