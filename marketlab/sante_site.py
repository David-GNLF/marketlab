"""La sonde de santé du site : une page morte ne doit plus mourir en silence.

L'INCIDENT QUI MOTIVE CE MODULE (2026-08-02). La sonde FRED a émis un `NaN`,
le JSON publié est devenu invalide, et la page Coulisses est restée MORTE
plusieurs jours — génération verte, notifications muettes, rien ne criait.
La leçon : « le site répond » ne suffit pas, il faut vérifier que ce qu'il
sert est LISIBLE. Cette sonde fait les deux.

CE QU'ELLE VÉRIFIE, à chaque balayage de la veille horaire :
1. le serveur répond en HTTP — un 401 est un état SAIN (le mur
   d'authentification est debout), seuls l'injoignable et les 5xx sont des
   pannes ;
2. les fichiers critiques existent, sont du JSON STRICTEMENT valide
   (`parse_constant` qui lève : le token `NaN` nu est exactement ce qui a
   tué Coulisses) et l'instantané n'est pas périmé (meta.genere_le).

ALERTE SUR TRANSITION, JAMAIS SUR ÉTAT. La veille repasse toutes les
10 minutes : alerter à chaque passage tant que la panne dure produirait
six alertes par heure, et c'est la septième qu'on ne lirait plus. L'état
précédent vit sur l'hébergement (le runner est éphémère) ; panne NOUVELLE →
urgent, rétablissement → information, panne inchangée → silence.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pandas as pd

SITE_URL = "https://marketlab.gnlfconsult.com/"

# Ce que le site ne peut pas se permettre de servir cassé : la page
# d'accueil (verdicts), le concours, les coulisses, et l'horodatage général.
FICHIERS_CRITIQUES = ["donnees/meta.json", "donnees/verdicts.json",
                      "donnees/concours.json", "donnees/coulisses.json"]

# Au-delà, l'instantané est périmé : la publication est quotidienne, 48 h
# couvrent un raté ponctuel du cron (mieux-effort) sans crier pour rien.
FRAICHEUR_MAX_H = 48

# L'état précédent, conservé sur l'hébergement — public mais sans secret,
# et une future page pourra l'afficher telle quelle.
ETAT_DISTANT = "donnees/sante_site.json"

DELAI_HTTP_S = 25


def _http_etat() -> int | None:
    """Code HTTP de la racine du site, None si injoignable."""
    try:
        req = urllib.request.Request(SITE_URL, method="GET",
                                     headers={"User-Agent": "marketlab-sante"})
        with urllib.request.urlopen(req, timeout=DELAI_HTTP_S) as rep:
            return int(rep.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)          # 401/403 : le serveur a RÉPONDU
    except Exception:
        return None


def _lire_distants(chemins: list[str]) -> dict[str, bytes | None]:
    """Les fichiers critiques, par FTPS — None pour chaque absent."""
    from marketlab import ftps
    cfg = ftps.charger_config()
    session = ftps._connecter(cfg)
    base = cfg["dossier_distant"].rstrip("/")
    contenus: dict[str, bytes | None] = {}
    try:
        for chemin in chemins:
            tampon = io.BytesIO()
            try:
                session.retrbinary(f"RETR {base}/{chemin}", tampon.write)
                contenus[chemin] = tampon.getvalue()
            except Exception:
                contenus[chemin] = None
    finally:
        try:
            session.quit()
        except Exception:
            session.close()
    return contenus


def _ecrire_distant(chemin: str, contenu: bytes) -> None:
    from marketlab import ftps
    cfg = ftps.charger_config()
    session = ftps._connecter(cfg)
    try:
        session.storbinary(f"STOR {cfg['dossier_distant'].rstrip('/')}/{chemin}",
                           io.BytesIO(contenu))
    finally:
        try:
            session.quit()
        except Exception:
            session.close()


def _json_strict(brut: bytes):
    """Parse STRICT : le token NaN/Infinity nu lève, comme dans un navigateur."""
    def _interdit(constante):
        raise ValueError(f"token {constante} nu")
    return json.loads(brut.decode("utf-8"), parse_constant=_interdit)


def sonder(quand: pd.Timestamp | None = None) -> dict:
    """Un diagnostic complet ; la liste `problemes` vide = tout va bien."""
    quand = quand or pd.Timestamp.now()
    problemes: list[str] = []

    code = _http_etat()
    if code is None:
        problemes.append("site injoignable en HTTP (délai ou connexion refusée)")
    elif code >= 500:
        problemes.append(f"le serveur répond {code}")

    try:
        contenus = _lire_distants(FICHIERS_CRITIQUES)
    except Exception as exc:
        contenus = {}
        problemes.append(f"hébergement injoignable en FTPS "
                         f"({type(exc).__name__})")
    meta = None
    for chemin, brut in contenus.items():
        if brut is None:
            problemes.append(f"{chemin} absent de l'hébergement")
            continue
        try:
            donnees = _json_strict(brut)
        except Exception as exc:
            problemes.append(f"{chemin} n'est pas du JSON valide "
                             f"({str(exc)[:60]}) — la page qui le charge est "
                             f"morte pour tout navigateur")
            continue
        if chemin.endswith("meta.json"):
            meta = donnees

    if meta and meta.get("genere_le"):
        try:
            age_h = (quand - pd.Timestamp(meta["genere_le"])).total_seconds() / 3600
            if age_h > FRAICHEUR_MAX_H:
                problemes.append(f"instantané périmé : généré il y a "
                                 f"{age_h:.0f} h (plafond {FRAICHEUR_MAX_H} h)")
        except Exception:
            pass

    return {"quand": quand.strftime("%Y-%m-%d %H:%M"), "http": code,
            "problemes": problemes, "sain": not problemes}


def verifier_et_alerter() -> dict:
    """Sonde, compare à l'état précédent, alerte SUR TRANSITION seulement.

    Ne lève jamais : une sonde qui casse la veille protégerait la panne.
    """
    etat = sonder()
    try:
        precedents = _lire_distants([ETAT_DISTANT]).get(ETAT_DISTANT)
        avant = json.loads(precedents.decode("utf-8")) if precedents else {}
    except Exception:
        avant = {}
    problemes_avant = set(avant.get("problemes") or [])
    problemes_apres = set(etat["problemes"])

    messages: list[tuple[str, bool]] = []
    nouveaux = sorted(problemes_apres - problemes_avant)
    repares = sorted(problemes_avant - problemes_apres)
    if nouveaux:
        messages.append(("🚨 <b>Santé du site</b> — " + " ; ".join(nouveaux),
                         True))
    if repares and not problemes_apres:
        messages.append(("✅ <b>Santé du site</b> — rétabli : "
                         + " ; ".join(repares), False))

    envoyes = 0
    for texte, urgent in messages:
        try:
            from marketlab import notify
            if notify.envoyer(texte, urgent=urgent):
                envoyes += 1
        except Exception:
            pass
        try:
            from marketlab import fil_alertes
            import re
            fil_alertes.publier([(re.sub(r"</?b>", "", texte), urgent)])
        except Exception:
            pass

    try:
        _ecrire_distant(ETAT_DISTANT, json.dumps(
            etat, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass
    return {**etat, "transitions": len(messages), "notifies": envoyes}
