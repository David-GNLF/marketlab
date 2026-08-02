"""La page « Coulisses » : des faits constatés, et surtout aucun secret.

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
import re

import pytest

from marketlab import config, coulisses


@pytest.fixture(autouse=True)
def _sans_reseau(monkeypatch):
    """Batterie HERMÉTIQUE — ajouté après coup, et voici pourquoi.

    `coulisses.etat()` a reçu une rubrique « analyse » qui interroge FRED. Ces
    tests n'ont pas changé, mais ils sont passés de 0,5 s à 11,7 s parce que
    la fonction qu'ils appellent, elle, s'est mise à sortir sur le réseau. En
    CI la batterie tourne AVANT la restauration du cache : rien n'était donc
    réutilisé d'un passage à l'autre.

    Ce que ces tests vérifient — l'absence de fuite de secret, la lecture des
    horaires dans les workflows — ne dépend d'aucune série macroéconomique.
    """
    from marketlab import realises, surprise
    monkeypatch.setattr(realises, "verifier", lambda *a, **k: __import__(
        "pandas").DataFrame())
    monkeypatch.setattr(realises, "correspondances_valides", lambda *a, **k: set())
    monkeypatch.setattr(surprise, "surprises", lambda *a, **k: __import__(
        "pandas").DataFrame(columns=surprise.COLONNES))
    monkeypatch.setattr(surprise, "score_par_devise", lambda *a, **k: {})
    # le duel IV/EWMA lit le CSV commité puis SORTIRAIT sur le réseau : même
    # famille de fuite que celle qui avait coûté 9 minutes par déploiement
    from marketlab import implicite
    monkeypatch.setattr(implicite, "comparer_previsionnistes",
                        lambda *a, **k: {"mesurable": False,
                                         "raison": "neutralisé en test"})
    # même famille : le bilan de la chaîne rejoue les verdicts mûrs sur les
    # cours réels dès que le CSV commité en contient
    from marketlab import journal_chaine
    monkeypatch.setattr(journal_chaine, "bilan",
                        lambda *a, **k: {"suivis": 0, "murs": 0,
                                         "lecture": "neutralisé en test"})

# Noms de champ qui trahiraient une valeur transportée par erreur.
CHAMPS_INTERDITS = [
    "MARKETLAB_FTP_MOT_DE_PASSE", "mot_de_passe", "password",
    "api_key", "apikey", "token", "ntfy.sh/",
]

# Ce qu'est VRAIMENT une fuite : une valeur d'apparence aléatoire et longue.
# Une clé FRED fait 32 caractères hexadécimaux, un jeton ntfy une vingtaine
# de caractères base64. Un mot français, même « secret », n'en est pas une.
JETON_SUSPECT = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")

# Ce qui ressemble à un jeton sans en être un, et qu'il faut donc laisser
# passer : les identifiants techniques que la page publie légitimement.
TOLERES = re.compile(r"^(MARKETLAB_[A-Z_]+|[a-z_]+(_[a-z0-9]+)+)$")


def _valeurs(objet, chemin=""):
    """Parcourt les VALEURS du bloc, en gardant leur chemin.

    Le journal des modifications est écarté : ce sont des phrases écrites par
    des humains, et un sujet de commit a parfaitement le droit de contenir le
    mot « secret » — c'est même arrivé le 2026-07-30, et le test a échoué sur
    sa propre prose. Un garde-fou qui crie au loup sur du texte libre finit
    par être désarmé ; celui-ci vise les VALEURS, là où une fuite se produit.
    """
    if chemin.endswith("derniers_commits"):
        return
    if isinstance(objet, dict):
        for k, v in objet.items():
            yield from _valeurs(v, f"{chemin}/{k}")
    elif isinstance(objet, list):
        for v in objet:
            yield from _valeurs(v, chemin)
    else:
        yield chemin, str(objet)


def test_aucun_nom_de_champ_sensible():
    """Un champ nommé « api_key » signalerait qu'une valeur voyage avec."""
    texte = json.dumps(coulisses.etat(), ensure_ascii=False, default=str).lower()
    trouves = [m for m in CHAMPS_INTERDITS if m.lower() in texte]
    assert not trouves, (
        f"champs sensibles dans coulisses.json : {trouves} — cette page est "
        "publiée en ligne")


def test_aucune_valeur_ressemblant_a_une_cle():
    """Le vrai test : pas de chaîne longue et aléatoire dans les valeurs."""
    suspects = []
    for chemin, valeur in _valeurs(coulisses.etat()):
        for jeton in JETON_SUSPECT.findall(valeur):
            if not TOLERES.match(jeton):
                suspects.append((chemin, jeton[:8] + "…"))
    assert not suspects, (
        f"valeurs d'apparence secrète dans coulisses.json : {suspects}")


def test_le_detecteur_attrape_bien_une_vraie_cle():
    """Un test de sécurité qui ne peut pas échouer ne protège rien : on lui
    présente une clé de la forme exacte d'une clé FRED."""
    faux = {"sources": {"cles": [{"nom": "FRED",
                                  "valeur": "015add9fefeeacd9805631665efd60c6"}]}}
    trouve = [j for _, v in _valeurs(faux) for j in JETON_SUSPECT.findall(v)
              if not TOLERES.match(j)]
    assert trouve, "le détecteur laisserait passer une clé de 32 hexadécimaux"


def test_les_cles_ne_sont_qu_un_booleen():
    """Ni la valeur, ni sa longueur, ni un fragment : juste présent ou non."""
    s = coulisses.sources()
    for cle in s["cles"]:
        assert set(cle) == {"nom", "role", "configuree"}, cle
        assert isinstance(cle["configuree"], bool)
    for cle in s["cles_autres_etapes"]:
        assert set(cle) == {"nom", "role", "etape", "visible_ici"}, cle
        assert isinstance(cle["visible_ici"], bool)


def test_les_secrets_des_autres_etapes_ne_sont_pas_dits_absents():
    """Ils sont configurés — simplement remis à une AUTRE étape que celle qui
    génère cette page. Les annoncer « absents » enverrait chercher une panne
    qui n'existe pas, et c'est exactement le tort qu'une console de
    diagnostic ne doit pas causer."""
    for cle in coulisses.sources()["cles_autres_etapes"]:
        assert "configuree" not in cle, (
            f"{cle['nom']} ne doit pas porter de verdict de configuration")
        assert cle["etape"], "l'étape qui l'emploie doit être nommée"


def test_les_horaires_viennent_des_workflows():
    """Recopier un horaire dans une page, c'est garantir qu'il sera faux le
    jour où le workflow changera sans qu'on y pense."""
    auto = coulisses.automatisation()
    fichiers = {t["fichier"] for t in auto["taches"]}
    assert "publication.yml" in fichiers
    publication = next(t for t in auto["taches"]
                       if t["fichier"] == "publication.yml")
    reel = (config.ROOT / ".github" / "workflows" / "publication.yml") \
        .read_text(encoding="utf-8")
    for cron in publication["crons_utc"]:
        assert f'"{cron}"' in reel


def test_la_feuille_de_route_vient_du_depot():
    route = coulisses.feuille_de_route()
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
    t = coulisses.tests()
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
    p = coulisses.perimetre()
    dossier = config.ROOT / "site" / "donnees" / "series"
    attendu = len(list(dossier.glob("*.json"))) if dossier.exists() else 0
    assert p["series_publiees"] == attendu
    assert p["fiches_attendues"] == len(config.FICHES)
    assert p["actifs_suivis"] == len(config.SUIVIS)


def test_l_etat_complet_est_serialisable(tmp_path):
    """Il finit dans un fichier JSON lu par un navigateur : un NaN ou un objet
    non sérialisable le rendrait illisible EN ENTIER.

    DEUX corrections nées du même incident (2026-08-02) :
    - le test sérialisait avec son PROPRE json.dumps au lieu du chemin réel —
      or c'est `publish._ecrire` qui écrit le fichier, et c'est LUI qui porte
      désormais l'assainissement NaN → null ; tester un autre chemin, c'est
      tester un autre programme ;
    - il cherchait la SOUS-CHAÎNE « NaN », et a explosé sur… un sujet de
      commit contenant le mot NaN (les derniers commits font partie de l'état
      publié). Un « NaN » entre guillemets est du JSON parfaitement valide ;
      seul le token nu est interdit — c'est ce que vérifie la relecture
      STRICTE (parse_constant qui lève)."""
    from marketlab import publish
    chemin = tmp_path / "coulisses.json"
    publish._ecrire(chemin, coulisses.etat())

    def _interdit(constante):
        raise AssertionError(f"token {constante} nu dans le JSON publié")
    json.loads(chemin.read_text(encoding="utf-8"), parse_constant=_interdit)
