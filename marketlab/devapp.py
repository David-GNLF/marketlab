"""État de l'application, publié comme le reste : l'espace DevApp.

POURQUOI CE MODULE. Une plateforme d'analyse qui affiche des chiffres doit
pouvoir dire d'où ils viennent, quand ils ont été produits, et ce qui a
échoué en chemin. Jusqu'ici ces réponses étaient éparpillées : dans les
journaux GitHub, dans `meta.json`, dans l'historique git, dans la tête de
celui qui avait fait la modification. L'espace DevApp les rassemble en une
page, servie comme tout le reste — un fichier JSON produit à la génération.

CE QU'IL NE FAIT PAS. Il ne remplace pas la surveillance : un site statique
ne peut pas se signaler lui-même en panne, c'est le rôle des notifications.
Il ne publie AUCUN secret : des clés d'API, il ne dit que « configurée » ou
« absente », jamais la valeur, jamais un fragment. Et il ne dit rien qu'il ne
puisse constater — pas d'état « en cours » déclaratif que personne ne
remettrait à jour.

Les faits viennent de trois sources vérifiables : le dépôt (git), le contenu
réellement publié (les fichiers de `site/donnees`), et la configuration
(périmètres, fournisseurs, tâches planifiées).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from marketlab import config

# Ce dont on cherche la présence, jamais la valeur.
CLES_ATTENDUES = {
    "MARKETLAB_FTP_HOTE": "publication du site (FTPS)",
    "MARKETLAB_NTFY_TOPIC": "notifications d'alerte",
    "MARKETLAB_FRED_CLE": "séries macroéconomiques (FRED)",
    "MARKETLAB_TWELVE_CLE": "cotations de secours (Twelve Data)",
}


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=config.ROOT,
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def depot() -> dict:
    """Ce que dit le dépôt : dernière modification, rythme, volume de code.

    Les adresses e-mail des auteurs ne sont PAS reprises : le site est en
    ligne, et l'historique git n'a pas à devenir un annuaire.
    """
    journal = _git("log", "-25", "--pretty=%h\x1f%ad\x1f%s", "--date=short")
    commits = []
    for ligne in journal.splitlines():
        morceaux = ligne.split("\x1f")
        if len(morceaux) == 3:
            commits.append({"abrege": morceaux[0], "date": morceaux[1],
                            "sujet": morceaux[2]})
    total = _git("rev-list", "--count", "HEAD")
    return {
        "version": _version_front(),
        "branche": _git("rev-parse", "--abbrev-ref", "HEAD") or "inconnue",
        "commits_total": int(total) if total.isdigit() else None,
        "derniers_commits": commits,
        "lignes_python": _compter("marketlab", "*.py") + _compter("scripts", "*.py"),
        "lignes_tests": _compter("tests", "*.py"),
        "lignes_front": _compter("front/src", "*.js") + _compter("front/src", "*.jsx"),
        "lignes_php": _compter("deploy", "*.php"),
    }


def _version_front() -> str:
    try:
        d = json.loads((config.ROOT / "front" / "package.json")
                       .read_text(encoding="utf-8"))
        return str(d.get("version", "—"))
    except Exception:
        return "—"


def _compter(dossier: str, motif: str) -> int:
    racine = config.ROOT / dossier
    if not racine.exists():
        return 0
    total = 0
    for f in racine.rglob(motif):
        if "__pycache__" in f.parts or "node_modules" in f.parts:
            continue
        try:
            total += sum(1 for _ in f.open(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
    return total


def tests() -> dict:
    """Fonctions de test, comptées dans les fichiers.

    On compte les FONCTIONS, pas les cas exécutés. La différence n'est pas
    cosmétique : un test paramétré produit plusieurs cas, et une première
    version de ce compteur essayait de les deviner en comptant les virgules —
    elle annonçait 275 là où pytest en exécute 234. Un tableau de bord qui
    exagère ses propres chiffres ne vaut rien ; mieux vaut une grandeur exacte
    et modeste qu'une estimation flatteuse.
    """
    racine = config.ROOT / "tests"
    fichiers, fonctions = 0, 0
    for f in sorted(racine.glob("test_*.py")):
        fichiers += 1
        texte = f.read_text(encoding="utf-8", errors="ignore")
        fonctions += len(re.findall(r"^def test_", texte, re.M))
    return {"fichiers": fichiers, "fonctions": fonctions}


def perimetre() -> dict:
    """Ce que la plateforme suit, et ce qu'elle a réellement publié."""
    donnees = config.ROOT / "site" / "donnees"
    series = sorted((donnees / "series").glob("*.json")) \
        if (donnees / "series").exists() else []
    fiches = sorted((donnees / "titres").glob("*.json")) \
        if (donnees / "titres").exists() else []

    profondeurs = {"quotidien": 0, "horaire": 0, "intraday": 0}
    manquants = []
    for f in series:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for bloc in profondeurs:
            if d.get(bloc, {}).get("t"):
                profondeurs[bloc] += 1
    for sym in config.FICHES:
        if not (donnees / "series" / f"{sym}.json").exists():
            manquants.append(sym)

    return {
        "actifs_suivis": len(config.SUIVIS),
        "fiches_attendues": len(config.FICHES),
        "fiches_publiees": len(fiches),
        "series_publiees": len(series),
        "series_par_socle": profondeurs,
        "series_manquantes": manquants,
        "univers": {nom: len(liste) for nom, liste in config.UNIVERS.items()},
        "poids_donnees_ko": round(sum(
            f.stat().st_size for f in donnees.rglob("*.json")) / 1024, 1)
        if donnees.exists() else 0,
    }


def sources() -> dict:
    """Fournisseurs de données et clés d'accès — présence seulement."""
    import os
    fichier_cles = config.ROOT / "data_local" / "providers.json"
    locales = {}
    if fichier_cles.is_file():
        try:
            locales = json.loads(fichier_cles.read_text(encoding="utf-8"))
        except Exception:
            locales = {}
    return {
        "fournisseurs": [
            {"nom": "Yahoo Finance", "usage": "cours quotidiens et "
             "intrajournaliers, la plupart des actifs", "cle": False},
            {"nom": "Binance", "usage": "crypto, temps réel", "cle": False},
            {"nom": "FRED", "usage": "séries macroéconomiques", "cle": True},
            {"nom": "Twelve Data", "usage": "secours sur les cotations",
             "cle": True},
            {"nom": "BRVM", "usage": "place d'Abidjan, import CSV manuel",
             "cle": False},
        ],
        # Uniquement un booléen : jamais la valeur, jamais un fragment.
        "cles": [
            {"nom": nom, "role": role,
             # Vraie question posée : la clé est-elle présente dans
             # l'environnement qui PUBLIE ? En local elle apparaîtra souvent
             # absente, et c'est exact — c'est GitHub Actions qui publie.
             "configuree": bool(os.environ.get(nom))
             or bool(locales.get(nom.lower().replace("marketlab_", "")))}
            for nom, role in CLES_ATTENDUES.items()
        ],
    }


def automatisation() -> dict:
    """Tâches planifiées, lues dans les workflows plutôt que recopiées.

    Recopier des horaires dans une page, c'est garantir qu'ils seront faux le
    jour où quelqu'un modifiera le workflow sans y penser.
    """
    dossier = config.ROOT / ".github" / "workflows"
    taches = []
    for f in sorted(dossier.glob("*.yml")) if dossier.exists() else []:
        texte = f.read_text(encoding="utf-8", errors="ignore")
        nom = re.search(r"^name:\s*(.+)$", texte, re.M)
        crons = re.findall(r'cron:\s*"([^"]+)"', texte)
        taches.append({
            "fichier": f.name,
            "nom": nom.group(1).strip() if nom else f.stem,
            "crons_utc": crons,
            "manuel": "workflow_dispatch" in texte,
        })
    return {
        "taches": taches,
        # Ce chiffre a été MESURÉ, il n'est pas une estimation : voir
        # scripts/alertes.py, qui explique pourquoi la veille dure au lieu de
        # se répéter.
        "note_planification": (
            "Les exécutions planifiées gratuites de GitHub sont au "
            "mieux-effort : mesuré sur 24 h, un cron horaire n'a donné que "
            "10 passages sur 24, et tripler les crons n'a rien changé "
            "(environ 15 % d'honorés). La veille mise donc sur la DURÉE — un "
            "processus tenu trois heures, qui balaie toutes les 10 minutes — "
            "plutôt que sur la fréquence."),
    }


def sante(meta: dict | None = None) -> dict:
    """Ce qui a échoué à la dernière génération, tel quel."""
    if meta is None:
        f = config.ROOT / "site" / "donnees" / "meta.json"
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    erreurs = meta.get("erreurs", {}) or {}
    return {
        "genere_le": meta.get("genere_le"),
        "blocs_produits": len(meta.get("blocs", [])),
        "fiches_produites": len(meta.get("titres", [])),
        "erreurs": [{"quoi": k, "message": str(v)[:200]}
                    for k, v in erreurs.items()],
    }


def feuille_de_route() -> list[dict]:
    """Feuille de route, lue dans `docs/FEUILLE_DE_ROUTE.md`.

    Format attendu, une ligne par élément :
        - [x] Titre — précision facultative
    Les cases cochées sont livrées, les autres non. Le fichier est versionné :
    la page ne peut donc pas prétendre à un état que le dépôt ne porte pas.
    """
    f = config.ROOT / "docs" / "FEUILLE_DE_ROUTE.md"
    if not f.is_file():
        return []
    elements, section = [], "Général"
    for ligne in f.read_text(encoding="utf-8").splitlines():
        titre = re.match(r"^##\s+(.+)$", ligne)
        if titre:
            section = titre.group(1).strip()
            continue
        item = re.match(r"^\s*-\s*\[([ xX])\]\s*(.+)$", ligne)
        if item:
            elements.append({"section": section,
                             "livre": item.group(1).lower() == "x",
                             "texte": item.group(2).strip()})
    return elements


def etat() -> dict:
    """Le bloc complet publié dans `donnees/devapp.json`."""
    return {
        "depot": depot(),
        "tests": tests(),
        "perimetre": perimetre(),
        "sources": sources(),
        "automatisation": automatisation(),
        "sante": sante(),
        "feuille_de_route": feuille_de_route(),
    }
