"""Moteur d'alertes, envoyées sur le(s) canal(aux) configuré(s).

Le choix du canal (ntfy sans compte, e-mail, notification Windows, Telegram)
et sa configuration vivent dans `marketlab/notify.py` —
voir `scripts/configurer_alertes.py` pour l'assistant de configuration.

Règles d'alerte (anti-doublon via .cache/alert_state.json) :
1. Changement d'avis d'un titre vers/depuis « Achat fort » ou « Vente forte ».
2. RSI en zone extrême (<25 ou >75) — au plus une alerte par titre et par jour.
3. Événements macro à fort impact dans les prochaines 24 h — une seule fois
   par événement.
"""

import json

import pandas as pd

from marketlab import config, eco_calendar, notify, screener

STATE_PATH = config.CACHE_DIR / "alert_state.json"

DEFAULT_UNIVERSES = ["Actions US", "Actions EU", "Forex", "Crypto",
                     "Matières premières"]
STRONG_LABELS = {"Achat fort", "Vente forte"}


# --- Envoi ------------------------------------------------------------------

def est_configure() -> bool:
    """True si au moins un canal de notification est actif."""
    return notify.est_configure()


def envoyer_message(html: str) -> bool:
    """Envoie un message (HTML léger) sur tous les canaux actifs."""
    try:
        return notify.envoyer(html)
    except Exception:
        return False


# --- État anti-doublon ------------------------------------------------------

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"avis": {}, "rsi_jour": {}, "evenements": []}


def _save_state(state: dict) -> None:
    # borne la liste d'événements pour éviter une croissance infinie
    state["evenements"] = state["evenements"][-500:]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# --- Règles -----------------------------------------------------------------

def build_alerts(universes: list[str] | None = None, event_hours: int = 24,
                 persist: bool = True) -> list[str]:
    """Évalue toutes les règles et renvoie les messages à envoyer.

    persist=False évalue sans consommer l'état anti-doublon : les mêmes
    alertes ressortiront au prochain passage. Indispensable tant que la
    livraison n'est pas effective, sinon une alerte est perdue sans avoir
    jamais été vue.
    """
    universes = universes or DEFAULT_UNIVERSES
    state = _load_state()
    messages: list[str] = []
    today = pd.Timestamp.today().date().isoformat()

    # 1+2. Signaux techniques sur les univers suivis
    symbols = [s for u in universes for s in config.UNIVERS.get(u, [])]
    table = screener.scan(symbols)
    for _, row in table.iterrows():
        sym, avis = row["symbole"], row["avis"]
        if row["score"] is None:
            continue
        prev = state["avis"].get(sym)
        if prev != avis and (avis in STRONG_LABELS or prev in STRONG_LABELS):
            arrow = "🟢" if "Achat" in avis else "🔴" if "Vente" in avis else "⚪"
            messages.append(
                f"{arrow} <b>{sym}</b> : {prev or 'nouveau'} → <b>{avis}</b>\n"
                f"Score {row['score']} · cours {row['cours']} · RSI {row['rsi14']} "
                f"· perf 20 j {row['perf_20j_%']} %"
            )
        state["avis"][sym] = avis

        rsi = row["rsi14"]
        if rsi is not None and (rsi < 25 or rsi > 75) \
                and state["rsi_jour"].get(sym) != today:
            zone = "survente extrême" if rsi < 25 else "surachat extrême"
            messages.append(f"⚠️ <b>{sym}</b> : RSI {rsi} — {zone}")
            state["rsi_jour"][sym] = today

    # 3. Événements macro à fort impact imminents
    try:
        macro_a_venir = eco_calendar.upcoming(hours=event_hours)
        fresh = [r for _, r in macro_a_venir.iterrows()
                 if eco_calendar.event_key(r) not in state["evenements"]]
        if fresh:
            lines = [f"• {r['quand'].strftime('%a %d/%m %H:%M')} — "
                     f"<b>{r['devise']}</b> {r['evenement']}"
                     + (f" (prév. {r['prevision']})" if r["prevision"] else "")
                     for r in fresh]
            messages.append(
                f"📅 <b>Événements à fort impact — prochaines {event_hours} h</b> "
                f"(heure Bénin)\n" + "\n".join(lines)
            )
            state["evenements"] += [eco_calendar.event_key(r) for r in fresh]
    except Exception as exc:
        messages.append(f"⚠️ Calendrier économique indisponible : {str(exc)[:100]}")

    # 4. Publications de résultats imminentes sur les titres qui comptent :
    #    positions détenues en papier + titres à avis fort. Un écart de
    #    publication traverse un stop sans prévenir.
    try:
        from marketlab import events as evt, paper as pf_mod
        surveilles = set()
        try:
            surveilles |= set(pf_mod.load()["positions"])
        except RuntimeError:
            pass
        surveilles |= {s for s, a in state["avis"].items() if a in STRONG_LABELS}
        for sym in sorted(surveilles):
            if not evt.a_des_resultats(sym):
                continue
            prochaine = evt.prochaine_publication(sym)
            if not prochaine or prochaine["dans_jours"] > 7:
                continue
            cle = f"resultats|{sym}|{prochaine['date']}"
            if cle in state["evenements"]:
                continue
            detail = ""
            try:
                amplitude = evt.etude(sym)["reaction_jour_j"]["amplitude_moyenne_%"]
                detail = f" Amplitude historique : ±{amplitude} % en une séance."
            except RuntimeError:
                pass
            messages.append(
                f"📣 <b>{sym}</b> : publication de résultats le "
                f"{prochaine['date']} (dans {prochaine['dans_jours']} j).{detail}"
            )
            state["evenements"].append(cle)
    except Exception as exc:
        print(f"[resultats] règle ignorée : {str(exc)[:100]}")

    if persist:
        _save_state(state)
    return messages


def run(universes: list[str] | None = None, dry_run: bool = False) -> dict:
    """Évalue les règles puis envoie (ou affiche en dry-run). Renvoie un bilan.

    L'état anti-doublon n'est consommé que si toutes les alertes ont réellement
    été livrées : un dry-run, aucun canal configuré ou un envoi en échec
    laissent l'état intact pour que le prochain passage réessaie.
    """
    configured = est_configure()
    livraison_reelle = configured and not dry_run
    etat_avant = _load_state()  # pour rétablir si un envoi échoue
    messages = build_alerts(universes, persist=livraison_reelle)
    sent = 0
    for msg in messages:
        if not livraison_reelle:
            print("--- ALERTE ---\n" + msg + "\n")
        elif envoyer_message(msg):
            sent += 1
    if livraison_reelle and sent < len(messages):
        _save_state(etat_avant)  # envoi partiel : le prochain passage réessaiera
    return {"alertes": len(messages), "envoyees": sent,
            "canaux": notify.canaux_actifs(), "configure": configured,
            "dry_run": dry_run}
