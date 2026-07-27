"""Moteur d'alertes, envoyées sur le(s) canal(aux) configuré(s).

Le choix du canal (ntfy sans compte, e-mail, notification Windows, Telegram)
et sa configuration vivent dans `marketlab/notify.py` —
voir `scripts/configurer_alertes.py` pour l'assistant de configuration.

Règles (anti-doublon via .cache/alert_state.json) :
1. Changement d'avis d'un titre vers/depuis « Achat fort » ou « Vente forte ».
2. RSI en zone extrême (<25 ou >75) — au plus une alerte par titre et par jour.
3. Événements macro à fort impact dans les prochaines 24 h — une seule fois
   par événement.
4. Publication de résultats sous 7 jours sur les titres détenus ou à avis fort.
5. Sentiment de marché en zone extrême (contrarien) — 1×/zone/jour.
6. FLASH — mouvement de séance exceptionnel (≥3 écarts-types de la volatilité
   du titre) : envoyé en priorité « urgent » (sonne même en silencieux).
7. FLASH — bascule du VIX en backwardation (stress immédiat) : urgent aussi.

Les règles 6-7 signalent un fait statistiquement rare, PAS un profit promis :
elles invitent à regarder vite, la décision reste humaine.
"""

import json

import pandas as pd

from marketlab import config, eco_calendar, notify, screener

STATE_PATH = config.CACHE_DIR / "alert_state.json"

DEFAULT_UNIVERSES = ["Actions US", "Actions EU", "Actions Asie", "Forex",
                     "Crypto", "Matières premières"]
STRONG_LABELS = {"Achat fort", "Vente forte"}
SEUIL_Z_FLASH = 3.0


# --- Envoi ------------------------------------------------------------------

def est_configure() -> bool:
    """True si au moins un canal de notification est actif."""
    return notify.est_configure()


def envoyer_message(html: str, urgent: bool = False) -> bool:
    """Envoie un message (HTML léger) sur tous les canaux actifs."""
    try:
        return notify.envoyer(html, urgent=urgent)
    except Exception:
        return False


# --- État anti-doublon ------------------------------------------------------

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            etat = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            etat.setdefault("avis", {})
            etat.setdefault("rsi_jour", {})
            etat.setdefault("evenements", [])
            return etat
        except Exception:
            pass
    return {"avis": {}, "rsi_jour": {}, "evenements": []}


def _save_state(state: dict) -> None:
    # borne la liste d'événements pour éviter une croissance infinie
    state["evenements"] = state["evenements"][-500:]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# --- Règles -----------------------------------------------------------------

def build_alerts(universes: list[str] | None = None, event_hours: int = 24,
                 persist: bool = True) -> list[tuple[str, bool]]:
    """Évalue toutes les règles ; renvoie des couples (message, urgent).

    persist=False évalue sans consommer l'état anti-doublon : les mêmes
    alertes ressortiront au prochain passage. Indispensable tant que la
    livraison n'est pas effective, sinon une alerte est perdue sans avoir
    jamais été vue.
    """
    universes = universes or DEFAULT_UNIVERSES
    state = _load_state()
    messages: list[tuple[str, bool]] = []
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
            messages.append((
                f"{arrow} <b>{sym}</b> : {prev or 'nouveau'} → <b>{avis}</b>\n"
                f"Score {row['score']} · cours {row['cours']} · RSI {row['rsi14']} "
                f"· perf 20 j {row['perf_20j_%']} %", False))
        state["avis"][sym] = avis

        rsi = row["rsi14"]
        if rsi is not None and (rsi < 25 or rsi > 75) \
                and state["rsi_jour"].get(sym) != today:
            zone = "survente extrême" if rsi < 25 else "surachat extrême"
            messages.append((f"⚠️ <b>{sym}</b> : RSI {rsi} — {zone}", False))
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
            messages.append((
                f"📅 <b>Événements à fort impact — prochaines {event_hours} h</b> "
                f"(heure Bénin)\n" + "\n".join(lines), False))
            state["evenements"] += [eco_calendar.event_key(r) for r in fresh]
    except Exception as exc:
        messages.append((f"⚠️ Calendrier économique indisponible : "
                         f"{str(exc)[:100]}", False))

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
            messages.append((
                f"📣 <b>{sym}</b> : publication de résultats le "
                f"{prochaine['date']} (dans {prochaine['dans_jours']} j)."
                f"{detail}", False))
            state["evenements"].append(cle)
    except Exception as exc:
        print(f"[resultats] règle ignorée : {str(exc)[:100]}")

    # 5. Sentiment de marché aux extrêmes (contrarien) — 1 alerte par zone/jour
    try:
        from marketlab import sentiment_marche
        fg = sentiment_marche.indice()
        if fg["zone"] in ("peur extrême", "avidité extrême"):
            cle = f"sentiment|{today}|{fg['zone']}"
            if cle not in state["evenements"]:
                messages.append((
                    f"🌡️ <b>Sentiment de marché : {fg['zone'].upper()}</b> "
                    f"({fg['valeur']:.0f}/100)\n{fg['lecture']}", False))
                state["evenements"].append(cle)
    except Exception as exc:
        print(f"[sentiment] règle ignorée : {str(exc)[:80]}")

    # 6. FLASH — mouvement de séance exceptionnel (≥3σ de la volatilité propre).
    #    Un fait statistiquement rare, à regarder vite — pas un profit promis.
    try:
        from marketlab.data import get_ohlcv
        for sym in symbols:
            try:
                cours = get_ohlcv(sym, lookback_days=400)["close"]
            except Exception:
                continue
            rendements = cours.pct_change().dropna()
            if len(rendements) < 60:
                continue
            dernier = float(rendements.iloc[-1])
            sigma = float(rendements.iloc[:-1].tail(120).std())
            if sigma <= 0:
                continue
            z = dernier / sigma
            date_barre = rendements.index[-1].date().isoformat()
            cle = f"flash|{sym}|{date_barre}"
            if abs(z) >= SEUIL_Z_FLASH and cle not in state["evenements"]:
                sens = "📈 BOND" if z > 0 else "📉 CHUTE"
                nom = config.NOMS_ACTIFS.get(sym, sym)
                messages.append((
                    f"🚨 <b>{sens} exceptionnel : {nom}</b>\n"
                    f"{dernier * 100:+.2f} % sur la séance, soit "
                    f"{abs(z):.1f} écarts-types de sa volatilité habituelle "
                    f"(cours {float(cours.iloc[-1]):,.4g}).\n"
                    f"Fait rare — à examiner rapidement ; la décision reste "
                    f"la tienne.", True))
                state["evenements"].append(cle)
    except Exception as exc:
        print(f"[flash] règle ignorée : {str(exc)[:80]}")

    # 7. FLASH — le VIX bascule en backwardation : stress immédiat du marché
    try:
        from marketlab.data import get_ohlcv
        vix = float(get_ohlcv("^VIX", lookback_days=60)["close"].iloc[-1])
        vix3m = float(get_ohlcv("^VIX3M", lookback_days=60)["close"].iloc[-1])
        ratio = vix / vix3m if vix3m > 0 else 0.0
        cle = f"vixflip|{today}"
        if ratio >= 1.0 and cle not in state["evenements"]:
            messages.append((
                f"🚨 <b>VIX en BACKWARDATION</b> (VIX {vix:.1f} / VIX3M "
                f"{vix3m:.1f} = {ratio:.2f})\nLa peur immédiate dépasse la "
                f"peur à terme : régime de stress. Historiquement : "
                f"volatilité forte, stops élargis, tailles réduites.", True))
            state["evenements"].append(cle)
    except Exception as exc:
        print(f"[vix] règle ignorée : {str(exc)[:80]}")

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
    envoyes: list[tuple[str, bool]] = []
    for texte, urgent in messages:
        if not livraison_reelle:
            etiquette = " [URGENT]" if urgent else ""
            print(f"--- ALERTE{etiquette} ---\n{texte}\n")
        elif envoyer_message(texte, urgent=urgent):
            sent += 1
            envoyes.append((notify.html_vers_texte(texte), urgent))
    if livraison_reelle and sent < len(messages):
        _save_state(etat_avant)  # envoi partiel : le prochain passage réessaiera
    return {"alertes": len(messages), "envoyees": sent,
            "messages_envoyes": envoyes,
            "canaux": notify.canaux_actifs(), "configure": configured,
            "dry_run": dry_run}
