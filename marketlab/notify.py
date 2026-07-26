"""Canaux de notification des alertes — Telegram n'est qu'une option parmi d'autres.

Configuration : `data_local/notifications.json`, par exemple

    {
      "canaux": ["ntfy"],
      "ntfy": {"serveur": "https://ntfy.sh", "topic": "marketlab-xxxxxxxx"}
    }

`canaux` liste les canaux actifs (plusieurs possibles, envoi à chacun).

Canaux disponibles
------------------
- **ntfy** — notifications push sur téléphone, **sans aucun compte**. Publier
  sur un topic ; s'abonner au même topic depuis l'app ntfy (Android/iOS) ou
  ntfy.sh/<topic> dans un navigateur. Le nom du topic EST le secret : le
  garder long et aléatoire (quiconque le connaît lit les alertes).
- **email** — SMTP classique (Gmail exige un « mot de passe d'application »).
- **windows** — notification de bureau locale, aucun service tiers, mais
  visible seulement devant le PC.
- **telegram** — historique ; lit aussi l'ancien `data_local/telegram.json`.

Ajouter un canal = une fonction `_envoyer_<nom>(cfg, titre, texte, html)`
inscrite dans CANAUX.
"""

import json
import re
import smtplib
import subprocess
from email.message import EmailMessage

import requests

from marketlab import config

CONFIG_PATH = config.DATA_DIR / "notifications.json"
LEGACY_TELEGRAM_PATH = config.DATA_DIR / "telegram.json"


# --- Configuration ----------------------------------------------------------

def charger_config() -> dict:
    """Config des notifications, avec reprise de l'ancien telegram.json."""
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if not cfg.get("canaux") and LEGACY_TELEGRAM_PATH.exists():
        try:
            ancien = json.loads(LEGACY_TELEGRAM_PATH.read_text(encoding="utf-8"))
            if ancien.get("bot_token") and ancien.get("chat_id"):
                cfg = {"canaux": ["telegram"], "telegram": ancien}
        except Exception:
            pass
    return cfg


def canaux_actifs() -> list[str]:
    cfg = charger_config()
    return [c for c in cfg.get("canaux", []) if c in CANAUX]


def est_configure() -> bool:
    return bool(canaux_actifs())


def sauver_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                           encoding="utf-8")


# --- Mise en forme ----------------------------------------------------------

def html_vers_texte(html: str) -> str:
    """Les messages sont écrits en HTML léger (<b>) pour Telegram ; les autres
    canaux veulent du texte brut."""
    txt = re.sub(r"<br\s*/?>", "\n", html)
    txt = re.sub(r"<[^>]+>", "", txt)
    return (txt.replace("&lt;", "<").replace("&gt;", ">")
               .replace("&amp;", "&").strip())


def _titre(texte: str) -> str:
    """Première ligne utile, sans emoji de tête, tronquée."""
    ligne = next((l for l in texte.splitlines() if l.strip()), "MarketLab")
    ligne = re.sub(r"^[^\w(]+", "", ligne).strip()
    return (ligne[:80] + "…") if len(ligne) > 80 else (ligne or "MarketLab")


# --- Canaux -----------------------------------------------------------------

def _envoyer_ntfy(cfg: dict, titre: str, texte: str, html: str) -> bool:
    topic = cfg.get("topic")
    if not topic:
        return False
    serveur = (cfg.get("serveur") or "https://ntfy.sh").strip().rstrip("/")
    if not serveur.startswith(("http://", "https://")):
        serveur = "https://" + serveur  # tolère « ntfy.sh » saisi sans schéma
    entetes = {
        "Title": titre.encode("utf-8").decode("latin-1", "ignore"),  # en-têtes = latin-1
        "Tags": "chart_with_upwards_trend",
        "Priority": str(cfg.get("priorite", "default")),
    }
    if cfg.get("jeton"):  # serveur ntfy protégé (auto-hébergé)
        entetes["Authorization"] = f"Bearer {cfg['jeton']}"
    resp = requests.post(f"{serveur}/{topic}", data=texte.encode("utf-8"),
                         headers=entetes, timeout=20)
    return resp.ok


def _envoyer_email(cfg: dict, titre: str, texte: str, html: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = f"[MarketLab] {titre}"
    msg["From"] = cfg.get("expediteur") or cfg["utilisateur"]
    msg["To"] = cfg["destinataire"]
    msg.set_content(texte)
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")

    hote, port = cfg["hote"], int(cfg.get("port", 587))
    if port == 465:
        serveur = smtplib.SMTP_SSL(hote, port, timeout=30)
    else:
        serveur = smtplib.SMTP(hote, port, timeout=30)
        serveur.starttls()
    with serveur:
        if cfg.get("utilisateur"):
            serveur.login(cfg["utilisateur"], cfg["mot_de_passe"])
        serveur.send_message(msg)
    return True


def _envoyer_windows(cfg: dict, titre: str, texte: str, html: str) -> bool:
    """Notification de bureau via l'API Windows, sans dépendance Python."""
    corps = texte[:250].replace("'", "''")
    titre_ps = titre[:60].replace("'", "''")
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
$modele = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textes = $modele.GetElementsByTagName('text')
$textes.Item(0).AppendChild($modele.CreateTextNode('{titre_ps}')) > $null
$textes.Item(1).AppendChild($modele.CreateTextNode('{corps}')) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($modele)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MarketLab').Show($toast)
"""
    res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                          "-Command", script],
                         capture_output=True, timeout=30)
    return res.returncode == 0


def _envoyer_telegram(cfg: dict, titre: str, texte: str, html: str) -> bool:
    resp = requests.post(
        f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
        json={"chat_id": cfg["chat_id"], "text": html, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20)
    return bool(resp.ok and resp.json().get("ok"))


CANAUX = {
    "ntfy": _envoyer_ntfy,
    "email": _envoyer_email,
    "windows": _envoyer_windows,
    "telegram": _envoyer_telegram,
}


# --- Point d'entrée ---------------------------------------------------------

def envoyer(message_html: str) -> bool:
    """Envoie sur tous les canaux actifs. True si AU MOINS un a réussi.

    Un canal en échec n'empêche pas les autres ; le résultat pilote la
    conservation de l'état anti-doublon côté alerts.py.
    """
    cfg = charger_config()
    actifs = canaux_actifs()
    if not actifs:
        return False
    texte = html_vers_texte(message_html)
    titre = _titre(texte)
    succes = False
    for nom in actifs:
        try:
            if CANAUX[nom](cfg.get(nom, {}), titre, texte, message_html):
                succes = True
        except Exception as exc:
            print(f"[{nom}] échec de l'envoi : {str(exc)[:120]}")
    return succes
