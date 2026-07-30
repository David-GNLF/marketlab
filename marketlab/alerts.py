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
8. FLASH — SECOUSSE : une barre de 5 minutes hors norme (≥8 écarts-types),
   au plus une par titre et par séance. La règle 6 mesure l'AMPLEUR du
   mouvement depuis la clôture précédente, celle-ci sa VITESSE : une dérive
   lente et un décrochage brutal de même taille lui sont identiques, alors que
   le second est une nouvelle et le premier une tendance.

Les règles 6-8 signalent un fait statistiquement rare, PAS un profit promis :
elles invitent à regarder vite, la décision reste humaine.
"""

import json

import numpy as np
import pandas as pd

from marketlab import config, eco_calendar, intraday, notify, screener


def _regle_secousse(symbols, state) -> list[tuple]:
    """Règle 8 — une barre de 5 minutes hors norme. Renvoie des messages.

    CE QUE LA RÈGLE 6 NE VOIT PAS. Elle mesure le mouvement CUMULÉ depuis la
    clôture précédente — et elle le voit bien en séance, la barre quotidienne
    de Yahoo se mettant à jour en continu (vérifié). Ce qui lui échappe, c'est
    la VITESSE : une dérive lente de 3 % sur six heures et un décrochage de
    3 % en un quart d'heure lui sont identiques, alors que le second est une
    nouvelle et le premier une tendance. Un choc se voit à la barre.

    SEUIL MESURÉ, PAS SUPPOSÉ. Sur 365 059 barres de 5 minutes, 58 titres,
    60 jours : les 3σ de la règle quotidienne donneraient 95 alertes par jour,
    les rendements de 5 minutes ayant des queues bien plus épaisses (|z|
    observé jusqu'à 68). À 8σ, une seule par titre et par séance : 3,1 par jour
    sur tout l'univers. Contrôle de bon sens sur les plus fortes secousses
    relevées — EURUSD, AUDUSD, GBPUSD, USDCHF et l'or secoués LE MÊME JOUR,
    soit un choc dollar unique, pas du bruit.
    """
    messages = []
    for sym in symbols:
        try:
            barres = intraday.lire(sym, intraday.INTERVALLE_DEFAUT)
        except Exception:
            continue
        if barres is None or barres.empty or len(barres) < FENETRE_SECOUSSE + 20:
            continue
        cours = pd.to_numeric(barres["close"], errors="coerce").dropna()
        jour = pd.DatetimeIndex(cours.index).normalize()
        # rendements calculés DANS chaque séance : le saut de cotation d'une
        # clôture à l'ouverture suivante n'est pas une secousse intraséance
        rendements = np.log(cours).groupby(jour).diff().dropna()
        if len(rendements) < FENETRE_SECOUSSE + 20:
            continue
        sigma = float(rendements.iloc[:-1].tail(FENETRE_SECOUSSE).std())
        if not np.isfinite(sigma) or sigma <= 0:
            continue
        dernier = float(rendements.iloc[-1])
        z = dernier / sigma
        horodatage = rendements.index[-1]
        # une seule secousse par titre et par séance : les barres qui suivent
        # un choc sont elles aussi hors norme, et les enchaîner transformerait
        # un événement en avalanche de notifications
        cle = f"secousse|{sym}|{horodatage.date().isoformat()}"
        if abs(z) < SEUIL_Z_SECOUSSE or cle in state["evenements"]:
            continue
        sens = "📈 flambée" if z > 0 else "📉 décrochage"
        nom = config.NOMS_ACTIFS.get(sym, sym)
        prix = float(cours.iloc[-1])
        messages.append((
            f"⚡ <b>SECOUSSE — {nom}</b>\n"
            f"{sens} de {dernier * 100:+.2f} % en 5 minutes, soit "
            f"{abs(z):.0f} écarts-types de son agitation habituelle "
            f"(cours {prix:,.4g} à {horodatage.strftime('%H:%M')} UTC).\n"
            f"Mouvement BRUTAL, pas forcément durable — souvent une nouvelle. "
            f"À vérifier avant toute décision.", True,
            {"regle": "secousse_intraseance", "symbole": sym,
             "prix": prix, "z": round(z, 1),
             "sens": "hausse" if z > 0 else "baisse"}))
        state["evenements"].append(cle)
    return messages

STATE_PATH = config.CACHE_DIR / "alert_state.json"

DEFAULT_UNIVERSES = ["Actions US", "Actions EU", "Actions Asie", "Forex",
                     "Crypto", "Matières premières"]
STRONG_LABELS = {"Achat fort", "Vente forte"}
SEUIL_Z_FLASH = 3.0

# Secousse intraséance (règle 8). 8 et non 3 : mesuré sur 365 059 barres de
# 5 minutes, 58 titres, 60 jours — les 3σ de la règle quotidienne donneraient
# 95 alertes par jour, les rendements de 5 minutes ayant des queues bien plus
# épaisses. À 8σ et une alerte par titre et par séance : 3,1 par jour.
SEUIL_Z_SECOUSSE = 8.0

# Barres de référence pour l'agitation habituelle (~4 séances actions, ~1 en
# 24 h). Assez long pour être stable, assez court pour suivre un changement
# de régime.
FENETRE_SECOUSSE = 300


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
                 persist: bool = True) -> list[tuple[str, bool, dict]]:
    """Évalue toutes les règles ; renvoie (message, urgent, métadonnées).

    Les métadonnées (règle, symbole, prix, sens attendu) sont ce qui rend
    l'alerte JUGEABLE plus tard : sans elles, impossible de savoir quelle
    règle mérite d'être gardée et laquelle ne fait que du bruit.

    persist=False évalue sans consommer l'état anti-doublon : les mêmes
    alertes ressortiront au prochain passage. Indispensable tant que la
    livraison n'est pas effective, sinon une alerte est perdue sans avoir
    jamais été vue.
    """
    universes = universes or DEFAULT_UNIVERSES
    state = _load_state()
    messages: list[tuple[str, bool, dict]] = []
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
                f"· perf 20 j {row['perf_20j_%']} %", False,
                {"regle": "bascule_avis", "symbole": sym, "prix": row["cours"],
                 "sens": "hausse" if "Achat" in avis else
                         "baisse" if "Vente" in avis else "neutre"}))
        state["avis"][sym] = avis

        rsi = row["rsi14"]
        if rsi is not None and (rsi < 25 or rsi > 75) \
                and state["rsi_jour"].get(sym) != today:
            zone = "survente extrême" if rsi < 25 else "surachat extrême"
            messages.append((f"⚠️ <b>{sym}</b> : RSI {rsi} — {zone}", False,
                {"regle": "rsi_extreme", "symbole": sym, "prix": row["cours"],
                 "sens": "hausse" if rsi < 25 else "baisse"}))
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
                f"(heure Bénin)\n" + "\n".join(lines), False,
                {"regle": "agenda_macro"}))
            state["evenements"] += [eco_calendar.event_key(r) for r in fresh]
    except Exception as exc:
        messages.append((f"⚠️ Calendrier économique indisponible : "
                         f"{str(exc)[:100]}", False, {"regle": "incident"}))

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
                f"{detail}", False,
                {"regle": "resultats_proches", "symbole": sym}))
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
                    f"({fg['valeur']:.0f}/100)\n{fg['lecture']}", False,
                    {"regle": "sentiment_extreme"}))
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
                    f"la tienne.", True,
                    {"regle": "mouvement_flash", "symbole": sym,
                     "prix": float(cours.iloc[-1]),
                     "sens": "hausse" if z > 0 else "baisse"}))
                state["evenements"].append(cle)
    except Exception as exc:
        print(f"[flash] règle ignorée : {str(exc)[:80]}")

    # 8. FLASH — secousse intraséance (voir _regle_secousse).
    try:
        messages.extend(_regle_secousse(symbols, state))
    except Exception as exc:
        print(f"[secousse] règle ignorée : {str(exc)[:80]}")

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
                f"volatilité forte, stops élargis, tailles réduites.", True,
                {"regle": "vix_backwardation"}))
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
    livrees: list[dict] = []
    for texte, urgent, meta in messages:
        if not livraison_reelle:
            etiquette = " [URGENT]" if urgent else ""
            print(f"--- ALERTE{etiquette} ---\n{texte}\n")
        elif envoyer_message(texte, urgent=urgent):
            sent += 1
            envoyes.append((notify.html_vers_texte(texte), urgent))
            livrees.append({**meta, "urgent": urgent})
    if livraison_reelle and sent < len(messages):
        _save_state(etat_avant)  # envoi partiel : le prochain passage réessaiera
    if livrees:
        # Une alerte envoyée est une prédiction implicite : on la consigne
        # pour pouvoir la juger plus tard, comme on juge les verdicts.
        try:
            journaliser_alertes(livrees)
        except Exception as exc:
            print(f"journal des alertes indisponible : {str(exc)[:80]}")
    return {"alertes": len(messages), "envoyees": sent,
            "messages_envoyes": envoyes,
            "canaux": notify.canaux_actifs(), "configure": configured,
            "dry_run": dry_run}


# --- Le tribunal des alertes -------------------------------------------------
#
# Les verdicts ont leur bilan depuis le début ; les alertes, non. Une règle qui
# crie pour rien coûte l'attention de son lecteur — c'est la ressource la plus
# rare. On consigne donc chaque alerte livrée, et on mesure ce qui s'est passé
# ensuite pour les règles qui portent une direction (bascule d'avis, RSI
# extrême, mouvement flash). Les règles de contexte (agenda macro, sentiment,
# VIX) sont consignées mais non notées : elles n'annoncent pas un sens.

JOURNAL_ALERTES = config.DATA_DIR / "journal_alertes.csv"
REGLES_DIRECTIONNELLES = {"bascule_avis", "rsi_extreme", "mouvement_flash"}
HORIZON_ALERTE = 5          # séances : une alerte parle du court terme


def journaliser_alertes(alertes: list[dict]) -> int:
    """Consigne les alertes livrées (une ligne par alerte)."""
    lignes = [{"date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
               "regle": a.get("regle", "?"), "symbole": a.get("symbole"),
               "prix": a.get("prix"), "sens": a.get("sens"),
               "urgent": bool(a.get("urgent"))} for a in alertes]
    nouveau = pd.DataFrame(lignes)
    if JOURNAL_ALERTES.exists():
        nouveau = pd.concat([pd.read_csv(JOURNAL_ALERTES), nouveau],
                            ignore_index=True)
    JOURNAL_ALERTES.parent.mkdir(parents=True, exist_ok=True)
    nouveau.to_csv(JOURNAL_ALERTES, index=False)
    return len(lignes)


def bilan_alertes(horizon: int = HORIZON_ALERTE) -> dict:
    """Ce que les alertes ont annoncé, et ce qui s'est réellement passé.

    Pour chaque alerte directionnelle, on mesure le rendement de l'actif sur
    les `horizon` séances suivantes et on regarde s'il est allé dans le sens
    annoncé. Une règle sous 50 % de justesse sur un échantillon suffisant est
    une règle à revoir — ou à supprimer.
    """
    from marketlab.data import get_ohlcv
    if not JOURNAL_ALERTES.exists():
        return {"n": 0, "message": "Aucune alerte journalisée pour l'instant : "
                                   "le bilan se remplira au fil des passages."}
    j = pd.read_csv(JOURNAL_ALERTES)
    j["date"] = pd.to_datetime(j["date"])
    total = len(j)
    jugeables = j[j["regle"].isin(REGLES_DIRECTIONNELLES)
                  & j["symbole"].notna() & j["prix"].notna()].copy()

    evalues = []
    for symbole, groupe in jugeables.groupby("symbole"):
        try:
            cours = get_ohlcv(str(symbole), lookback_days=400)["close"]
        except Exception:
            continue
        for _, l in groupe.iterrows():
            futurs = cours[cours.index > l["date"]]
            if len(futurs) < horizon:
                continue
            rendement = float(futurs.iloc[horizon - 1] / l["prix"] - 1) * 100
            attendu = 1 if l["sens"] == "hausse" else -1 if l["sens"] == "baisse" else 0
            evalues.append({"regle": l["regle"], "rendement_%": rendement,
                            "juste": (rendement * attendu > 0) if attendu else None})
    if not evalues:
        return {"n": total, "evaluees": 0,
                "message": f"{total} alerte(s) consignée(s) ; aucune n'a encore "
                           f"atteint son horizon de {horizon} séances."}
    df = pd.DataFrame(evalues)
    par_regle = []
    for regle, g in df.groupby("regle"):
        justes = g["juste"].dropna()
        par_regle.append({
            "regle": regle, "n": len(g),
            "justesse_%": round(float(justes.mean()) * 100, 1) if len(justes) else None,
            "mouvement_moyen_%": round(float(g["rendement_%"].mean()), 2),
        })
    return {"n": total, "evaluees": len(df), "horizon_seances": horizon,
            "par_regle": par_regle,
            "lecture": ("Une alerte est une prédiction implicite : ce tableau "
                        "dit si elle s'est vérifiée. Sous 50 % de justesse sur "
                        "un échantillon suffisant, la règle coûte plus "
                        "d'attention qu'elle n'en mérite.")}
