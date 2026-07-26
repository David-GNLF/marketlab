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
"""

import ftplib
import json
import os
from pathlib import Path

from marketlab import config

CONFIG_PATH = config.DATA_DIR / "cpanel.json"


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


def _connecter(cfg: dict) -> ftplib.FTP_TLS:
    session = ftplib.FTP_TLS(timeout=60)
    session.connect(cfg["hote"], int(cfg.get("port", 21)))
    session.login(cfg["utilisateur"], cfg["mot_de_passe"])
    session.prot_p()          # chiffre aussi le canal de données
    session.set_pasv(True)
    return session


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


def publier(dossier_local: Path | None = None, verbeux: bool = True) -> dict:
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
    finally:
        try:
            session.quit()
        except ftplib.all_errors:
            session.close()

    return {"envoyes": len(envoyes), "inchanges": len(ignores),
            "echecs": echecs, "destination": f"{cfg['hote']}:{base}"}


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
