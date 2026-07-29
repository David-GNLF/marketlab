"""Publication du site vers un hébergement cPanel par FTPS.

Pas de SSH sur un hébergement mutualisé : le transfert se fait en FTP sur TLS
(FTPS explicite), le seul protocole systématiquement disponible côté cPanel.

Configuration : `data_local/cpanel.json` (jamais versionné) —

    {
      "hote": "cloud740.thundercloud.uk",
      "utilisateur": "...",
      "mot_de_passe": "...",
      "dossier_distant": "/public_html/marketlab"
    }

Le transfert est **différentiel** : seuls les fichiers dont la taille diffère
sont renvoyés, ce qui évite de retransmettre le front à chaque publication.

Il ne supprime rien non plus, sauf dans `assets/` : Vite y met une empreinte du
contenu dans le nom du bundle, si bien qu'un fichier orphelin s'y accumule à
chaque publication. La purge (voir `purger_assets`) y efface les .js/.css que
le `index.html` publié ne référence plus — en gardant la génération
précédente, pour les navigateurs qui ont encore l'ancien index.html en cache.
Aucun autre dossier distant (donnees/, trading/, admin/, acces/, cache/…)
n'est jamais touché.
"""

import ftplib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from marketlab import config

CONFIG_PATH = config.DATA_DIR / "cpanel.json"

# Extensions purgeables : uniquement les bundles versionnés par empreinte.
# Tout le reste (polices, images, fichiers déposés à la main) est conservé.
EXTENSIONS_PURGEABLES = (".js", ".css", ".map")

# Deux fichiers envoyés au cours d'une même publication ont des dates de
# modification distantes très proches. Cette fenêtre les regroupe en une
# « génération », pour conserver le bundle précédent d'un seul tenant.
FENETRE_GENERATION_S = 3600

# Référence à un fichier de assets/ dans le index.html : src=, href=, import(),
# preload… tous passent par la même sous-chaîne « assets/<nom> ».
_MOTIF_ASSET = re.compile(r"assets/([A-Za-z0-9][A-Za-z0-9._-]*)")


def charger_config() -> dict:
    """Fichier local en priorité ; variables d'environnement en repli.

    Le repli permet l'exécution depuis GitHub Actions, où les accès sont
    fournis par les secrets du dépôt (MARKETLAB_FTP_HOTE, _UTILISATEUR,
    _MOT_DE_PASSE, _DOSSIER) et où aucun fichier de secrets n'existe.
    """
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        cfg = {
            "hote": os.environ.get("MARKETLAB_FTP_HOTE"),
            "port": os.environ.get("MARKETLAB_FTP_PORT", 21),
            "utilisateur": os.environ.get("MARKETLAB_FTP_UTILISATEUR"),
            "mot_de_passe": os.environ.get("MARKETLAB_FTP_MOT_DE_PASSE"),
            "dossier_distant": os.environ.get("MARKETLAB_FTP_DOSSIER",
                                              "/public_html/marketlab"),
        }
    manquants = [c for c in ("hote", "utilisateur", "mot_de_passe",
                             "dossier_distant") if not cfg.get(c)]
    if manquants:
        raise RuntimeError(
            f"Accès FTPS incomplets ({manquants}) : renseigner {CONFIG_PATH} "
            "ou les variables MARKETLAB_FTP_*")
    return cfg


def _connecter(cfg: dict, tentatives: int = 3) -> ftplib.FTP_TLS:
    """Ouvre une session FTPS, en réessayant si le serveur ne répond pas.

    Sans cela, un simple incident réseau fait tomber toute la publication.
    C'est arrivé le 2026-07-28 à 22h58 UTC : un `TimeoutError` sur la
    connexion a fait échouer l'étape du robot, et donc la publication de la
    nuit entière. Un hébergement mutualisé retarde ou refuse une connexion de
    temps à autre — il faut le supporter, pas s'y casser.
    """
    dernier = None
    for essai in range(1, tentatives + 1):
        try:
            session = ftplib.FTP_TLS(timeout=60)
            session.connect(cfg["hote"], int(cfg.get("port", 21)))
            session.login(cfg["utilisateur"], cfg["mot_de_passe"])
            session.prot_p()          # chiffre aussi le canal de données
            session.set_pasv(True)
            if essai > 1:
                print(f"  connexion FTPS établie au {essai}e essai")
            return session
        except (OSError, ftplib.Error) as exc:
            dernier = exc
            if essai < tentatives:
                attente = 5 * essai            # 5 s, puis 10 s
                print(f"  connexion FTPS échouée ({type(exc).__name__}: "
                      f"{str(exc)[:60]}) — nouvel essai dans {attente} s")
                time.sleep(attente)
    raise RuntimeError(f"connexion FTPS impossible après {tentatives} "
                       f"essais : {type(dernier).__name__}: {dernier}")


def _assurer_dossier(session: ftplib.FTP_TLS, chemin: str) -> None:
    """Crée l'arborescence distante si nécessaire."""
    courant = ""
    for element in chemin.strip("/").split("/"):
        courant += "/" + element
        try:
            session.mkd(courant)
        except ftplib.error_perm:
            pass  # existe déjà


def _tailles_distantes(session: ftplib.FTP_TLS, dossier: str) -> dict[str, int]:
    """Taille de chaque fichier distant, pour ne renvoyer que ce qui change."""
    tailles = {}

    def explorer(chemin: str, prefixe: str) -> None:
        try:
            elements = list(session.mlsd(chemin))
        except (ftplib.error_perm, ftplib.error_temp):
            return
        for nom, faits in elements:
            if nom in (".", ".."):
                continue
            relatif = f"{prefixe}{nom}"
            if faits.get("type") == "dir":
                explorer(f"{chemin}/{nom}", f"{relatif}/")
            elif faits.get("type") == "file":
                tailles[relatif] = int(faits.get("size", -1))

    explorer(dossier, "")
    return tailles


def _lister_assets(session: ftplib.FTP_TLS, dossier: str) -> dict[str, dict]:
    """Fichiers présents dans le `assets/` distant, avec leur date distante.

    Volontairement non récursif : la purge ne descend pas dans d'éventuels
    sous-dossiers, elle ne traite que les bundles posés à plat par Vite.
    """
    fichiers: dict[str, dict] = {}
    try:
        elements = list(session.mlsd(dossier))
    except (ftplib.error_perm, ftplib.error_temp):
        return fichiers
    for nom, faits in elements:
        if nom in (".", "..") or faits.get("type") != "file":
            continue
        modifie = None
        brut = faits.get("modify")
        if brut:
            try:
                modifie = datetime.strptime(brut[:14], "%Y%m%d%H%M%S")
            except ValueError:
                modifie = None
        fichiers[nom] = {"taille": int(faits.get("size", -1)),
                         "modifie": modifie}
    return fichiers


def _assets_references(dossier_local: Path) -> set[str]:
    """Noms de `assets/` que le site publié utilise réellement.

    Deux sources cumulées : le `index.html` qui vient d'être envoyé (c'est lui
    qui fait foi côté navigateur) et le contenu local de `assets/`, qui est
    reconstruit à neuf à chaque build. La seconde couvre les fichiers qu'un
    éventuel découpage en morceaux (code splitting) chargerait depuis le
    bundle plutôt que depuis le HTML.
    """
    references: set[str] = set()

    index = dossier_local / "index.html"
    if index.exists():
        references |= set(_MOTIF_ASSET.findall(
            index.read_text(encoding="utf-8", errors="replace")))

    dossier_assets = dossier_local / "assets"
    if dossier_assets.is_dir():
        references |= {p.name for p in dossier_assets.iterdir() if p.is_file()}

    return references


def _generation_precedente(candidats: list[str],
                           distants: dict[str, dict]) -> set[str]:
    """Sursis accordé au bundle de la publication précédente.

    Les orphelins les plus récents (à une heure près) sont ceux du dernier
    déploiement : un navigateur qui a encore l'ancien index.html en cache les
    réclame toujours.

    Le sursis se calcule **par extension**, car les dates distantes ne sont pas
    comparables d'un type à l'autre : le transfert différentiel ne renvoie pas
    une feuille de style inchangée d'un build à l'autre, si bien que la date
    d'un .css traîne loin derrière celle du .js de la même génération. Une
    fenêtre unique effacerait ce .css tout en gardant son .js — et la page
    reviendrait sans style chez qui a l'ancien index.html en cache.

    Un fichier dont le serveur ne donne pas la date est conservé par principe.
    """
    sursis = {n for n in candidats if distants[n]["modifie"] is None}

    par_extension: dict[str, list[str]] = {}
    for nom in candidats:
        if distants[nom]["modifie"] is not None:
            par_extension.setdefault(Path(nom).suffix.lower(), []).append(nom)

    for noms in par_extension.values():
        plus_recent = max(distants[n]["modifie"] for n in noms)
        sursis |= {n for n in noms
                   if (plus_recent - distants[n]["modifie"]).total_seconds()
                   <= FENETRE_GENERATION_S}
    return sursis


def purger_assets(session: ftplib.FTP_TLS, base: str, dossier_local: Path,
                  verbeux: bool = True) -> dict:
    """Efface les bundles orphelins du `assets/` distant. Renvoie le bilan.

    La purge s'abstient au moindre doute : pas de référence lisible, ou bundle
    courant introuvable côté serveur (transfert incomplet), et rien n'est
    supprimé — mieux vaut laisser grossir le dossier que casser le site.
    """
    bilan: dict = {"supprimes": [], "conserves": [], "echecs": {},
                   "octets_liberes": 0, "abandon": None}

    references = _assets_references(dossier_local)
    if not references:
        bilan["abandon"] = ("aucune référence d'asset lisible dans "
                            f"{dossier_local / 'index.html'}")
        return bilan

    distants = _lister_assets(session, f"{base}/assets")
    if not distants:
        bilan["abandon"] = f"{base}/assets illisible ou vide"
        return bilan

    manquants = sorted(n for n in references
                       if n.lower().endswith(EXTENSIONS_PURGEABLES)
                       and n not in distants)
    if manquants:
        bilan["abandon"] = ("bundle courant absent du serveur "
                            f"({', '.join(manquants)}) — transfert incomplet ?")
        return bilan

    candidats = sorted(n for n in distants
                       if n.lower().endswith(EXTENSIONS_PURGEABLES)
                       and n not in references)
    sursis = _generation_precedente(candidats, distants)
    bilan["conserves"] = sorted(sursis)

    for nom in candidats:
        if nom in sursis:
            if verbeux:
                print(f"  conservé (génération précédente)  assets/{nom}",
                      flush=True)
            continue
        try:
            session.delete(f"{base}/assets/{nom}")
        except ftplib.all_errors as exc:
            bilan["echecs"][nom] = str(exc)[:100]
            if verbeux:
                print(f"  ECHEC purge  assets/{nom} : {str(exc)[:80]}",
                      flush=True)
            continue
        bilan["supprimes"].append(nom)
        taille = distants[nom]["taille"]
        if taille > 0:
            bilan["octets_liberes"] += taille
        if verbeux:
            print(f"  purgé   assets/{nom}", flush=True)

    return bilan


def publier(dossier_local: Path | None = None, verbeux: bool = True,
            purger: bool = True) -> dict:
    """Envoie le contenu de `site/` vers l'hébergement. Renvoie le bilan."""
    dossier_local = Path(dossier_local or (config.ROOT / "site"))
    if not dossier_local.exists():
        raise RuntimeError(f"Rien à publier : {dossier_local} est absent. "
                           "Lancer d'abord la génération.")

    cfg = charger_config()
    base = cfg["dossier_distant"].rstrip("/")
    fichiers = sorted(p for p in dossier_local.rglob("*") if p.is_file())
    if not fichiers:
        raise RuntimeError(f"Aucun fichier dans {dossier_local}")

    session = _connecter(cfg)
    envoyes, ignores, echecs = [], [], {}
    purge: dict | None = None
    try:
        _assurer_dossier(session, base)
        distants = _tailles_distantes(session, base)
        dossiers_crees = set()

        for chemin in fichiers:
            relatif = chemin.relative_to(dossier_local).as_posix()
            taille = chemin.stat().st_size
            # Le saut « même taille » ne vaut que pour assets/ : Vite y met une
            # empreinte du contenu dans le nom, même nom + même taille = même
            # fichier. Ailleurs c'est faux — meta.json change d'horodatage sans
            # forcément changer de taille — donc tout le reste part toujours.
            if relatif.startswith("assets/") and distants.get(relatif) == taille:
                ignores.append(relatif)
                continue

            sous_dossier = str(Path(relatif).parent).replace("\\", "/")
            if sous_dossier not in (".", "") and sous_dossier not in dossiers_crees:
                _assurer_dossier(session, f"{base}/{sous_dossier}")
                dossiers_crees.add(sous_dossier)

            try:
                with chemin.open("rb") as flux:
                    session.storbinary(f"STOR {base}/{relatif}", flux)
                envoyes.append(relatif)
                if verbeux:
                    print(f"  envoyé  {relatif}", flush=True)
            except ftplib.all_errors as exc:
                echecs[relatif] = str(exc)[:100]
                if verbeux:
                    print(f"  ECHEC   {relatif} : {str(exc)[:80]}", flush=True)

        if purger and not echecs:
            purge = purger_assets(session, base, dossier_local, verbeux)
        elif purger:
            purge = {"supprimes": [], "conserves": [], "echecs": {},
                     "octets_liberes": 0,
                     "abandon": "transfert incomplet (échecs d'envoi)"}
    finally:
        try:
            session.quit()
        except ftplib.all_errors:
            session.close()

    return {"envoyes": len(envoyes), "inchanges": len(ignores),
            "echecs": echecs, "destination": f"{cfg['hote']}:{base}",
            "purge": purge}


def tester_connexion() -> dict:
    """Vérifie identifiants et dossier distant, sans rien transférer."""
    cfg = charger_config()
    session = _connecter(cfg)
    try:
        bienvenue = session.getwelcome()
        base = cfg["dossier_distant"].rstrip("/")
        _assurer_dossier(session, base)
        session.cwd(base)
        contenu = session.nlst()
        return {"hote": cfg["hote"], "bienvenue": bienvenue,
                "dossier_distant": base,
                "elements_presents": len(contenu), "ok": True}
    finally:
        try:
            session.quit()
        except ftplib.all_errors:
            session.close()
