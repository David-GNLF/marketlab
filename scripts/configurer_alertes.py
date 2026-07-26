"""Configuration assistée du canal d'alertes MarketLab.

    .venv\\Scripts\\python scripts\\configurer_alertes.py

Quatre canaux, du plus simple au plus contraignant :

  1. ntfy      — notifications push sur téléphone, AUCUN COMPTE requis.
  2. e-mail    — SMTP (Gmail demande un « mot de passe d'application »).
  3. windows   — notification de bureau, aucun service tiers.
  4. telegram  — nécessite un compte Telegram et un bot @BotFather.

Les secrets saisis (mot de passe, token) ne sont jamais affichés.
"""

import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from marketlab import alerts, notify


def _demander(question: str, defaut: str = "") -> str:
    reponse = input(f"{question}{f' [{defaut}]' if defaut else ''} : ").strip()
    return reponse or defaut


# --- ntfy -------------------------------------------------------------------

def _normaliser_serveur(saisie: str) -> str:
    """Tolère « ntfy.sh » ; refuse ce qui ne peut pas être une adresse."""
    url = saisie.strip().rstrip("/")
    if not url:
        return "https://ntfy.sh"
    if not url.startswith(("http://", "https://")):
        if "." not in url.split("/")[0]:
            raise RuntimeError(
                f"« {saisie} » n'est pas une adresse de serveur. Laisse ce champ "
                "vide pour utiliser https://ntfy.sh (c'est le cas normal) — le "
                "nom du topic se saisit à la question précédente.")
        url = "https://" + url
    return url


def configurer_ntfy() -> dict:
    print("\n=== ntfy — notifications push, sans compte ===")
    print("Le nom du topic EST le secret : qui le connaît reçoit tes alertes.")
    topic = _demander("Topic à utiliser (Entrée = en générer un aléatoire)")
    if not topic:
        topic = "marketlab-" + secrets.token_hex(8)
        print(f"Topic généré : {topic}")

    print("\nServeur ntfy — laisse vide sauf si tu héberges ton propre serveur.")
    serveur = _normaliser_serveur(_demander("Serveur", "https://ntfy.sh"))
    return {"canaux": ["ntfy"], "ntfy": {"serveur": serveur, "topic": topic}}


def _apres_ntfy(cfg: dict) -> None:
    n = cfg["ntfy"]
    url = f"{n['serveur'].rstrip('/')}/{n['topic']}"
    print("\n--- Pour recevoir les alertes ---")
    print(f"  • Sur téléphone : installer l'app « ntfy » (Android/iOS),")
    print(f"    « + » puis s'abonner au topic : {n['topic']}")
    if n["serveur"].rstrip("/") != "https://ntfy.sh":
        print(f"    (serveur personnalisé : {n['serveur']})")
    print(f"  • Sur ordinateur : ouvrir {url} dans un navigateur")
    print("  Garde cette page/abonnement ouvert pour voir arriver le test.")


# --- e-mail -----------------------------------------------------------------

def configurer_email() -> dict:
    print("\n=== e-mail (SMTP) ===")
    print("Gmail : activer la validation en 2 étapes puis créer un « mot de passe")
    print("d'application » sur myaccount.google.com/apppasswords (16 caractères).")
    hote = _demander("Serveur SMTP", "smtp.gmail.com")
    port = int(_demander("Port (587 = TLS, 465 = SSL)", "587"))
    utilisateur = _demander("Identifiant (adresse complète)")
    mot_de_passe = getpass.getpass("Mot de passe (saisie masquée) : ").strip()
    destinataire = _demander("Destinataire des alertes", utilisateur)
    return {"canaux": ["email"], "email": {
        "hote": hote, "port": port, "utilisateur": utilisateur,
        "mot_de_passe": mot_de_passe, "destinataire": destinataire}}


# --- Windows ----------------------------------------------------------------

def configurer_windows() -> dict:
    print("\n=== notification Windows ===")
    print("Aucun réglage : les alertes s'afficheront sur ce PC (session ouverte).")
    return {"canaux": ["windows"], "windows": {}}


# --- Telegram ---------------------------------------------------------------

def configurer_telegram() -> dict:
    print("\n=== Telegram ===")
    print("Prérequis : bot créé via @BotFather, ET un message envoyé à ce bot.")
    token = getpass.getpass("Token @BotFather (saisie masquée) : ").strip()
    if not token:
        raise RuntimeError("aucun token saisi")

    def appel(methode):
        r = requests.get(f"https://api.telegram.org/bot{token}/{methode}", timeout=20)
        if r.status_code == 401:
            raise RuntimeError("token refusé par Telegram (401)")
        r.raise_for_status()
        p = r.json()
        if not p.get("ok"):
            raise RuntimeError(p.get("description", "erreur Telegram"))
        return p["result"]

    bot = appel("getMe")
    print(f"Bot reconnu : @{bot.get('username')}")
    chats, vus = [], set()
    for maj in reversed(appel("getUpdates")):
        chat = (maj.get("message") or maj.get("channel_post") or {}).get("chat")
        if chat and chat["id"] not in vus:
            vus.add(chat["id"])
            nom = chat.get("first_name") or chat.get("title") or chat.get("username")
            chats.append((chat["id"], nom or "(sans nom)"))
    if not chats:
        raise RuntimeError(f"aucune conversation — écrire à @{bot.get('username')} "
                           "dans Telegram, puis relancer")
    if len(chats) == 1:
        chat_id = chats[0][0]
        print(f"Conversation : {chats[0][1]} (id {chat_id})")
    else:
        for i, (cid, nom) in enumerate(chats, 1):
            print(f"  {i}. {nom} (id {cid})")
        chat_id = chats[int(_demander("Laquelle ?", "1")) - 1][0]
    return {"canaux": ["telegram"],
            "telegram": {"bot_token": token, "chat_id": str(chat_id)}}


CHOIX = {
    "1": ("ntfy — push mobile, AUCUN compte (recommandé)", configurer_ntfy),
    "2": ("e-mail — SMTP / Gmail", configurer_email),
    "3": ("windows — notification de bureau locale", configurer_windows),
    "4": ("telegram — nécessite un compte Telegram", configurer_telegram),
}


def main() -> int:
    actifs = notify.canaux_actifs()
    if actifs:
        print(f"Canaux actuellement actifs : {', '.join(actifs)}")
    print("\nQuel canal veux-tu utiliser pour recevoir les alertes ?")
    for cle, (libelle, _) in CHOIX.items():
        print(f"  {cle}. {libelle}")
    choix = _demander("\nTon choix", "1")
    if choix not in CHOIX:
        print("Choix invalide — abandon.")
        return 1

    try:
        cfg = CHOIX[choix][1]()
    except Exception as exc:
        print(f"\nÉCHEC : {exc}")
        return 1

    notify.sauver_config(cfg)
    print(f"\nConfiguration écrite : {notify.CONFIG_PATH}")
    if choix == "1":
        _apres_ntfy(cfg)

    input("\nAppuie sur Entrée pour envoyer le message de test…")
    if notify.envoyer("✅ <b>MarketLab</b> — canal opérationnel.\n"
                      "Tu recevras ici : bascules d'avis fort, RSI extrêmes et "
                      "événements macro à fort impact."):
        print("Message de test envoyé.")
    else:
        print("ATTENTION : l'envoi de test a échoué. Vérifier la configuration "
              f"dans {notify.CONFIG_PATH}")
        return 1

    en_attente = len(alerts.build_alerts(persist=False))
    if en_attente:
        print(f"\n{en_attente} alerte(s) en attente. Elles partiront au prochain "
              "passage horaire, ou tout de suite avec :")
        print("  .venv\\Scripts\\python scripts\\alertes.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
