"""Le rapport hebdomadaire de gestion : l'outil rend compte, on ne le consulte plus.

CE QUE CE MODULE CORRIGE. Tout existe déjà — équités nocturnes, journal de la
chaîne, alertes de surveillance, jalons qui mûrissent — mais éparpillé entre
un JSON de concours, un CSV, un fil d'alertes et une console de diagnostic.
Personne ne fait le tour chaque semaine, et un outil de GESTION qui attend
d'être visité n'en est pas un.

LE CONDENSÉ, une fois par semaine de bourse (le vendredi, après la clôture
américaine — c'est l'heure du passage nocturne) :
- les comptes et leur semaine, avec la lecture de l'EXPÉRIENCE des trois
  robots (l'écart claude5−claude mesure l'horizon, claudefx−claude la
  spécialisation) ;
- ce que la chaîne a fait des idées sur 7 jours (retenues, écartées et à
  quel maillon) ;
- les alertes de surveillance émises ;
- l'état des jalons (filtre à l'échéance, duels IV, régimes suspendus).

DEUX CANAUX, AUCUNE PAGE NOUVELLE : la notification existante (ntfy) et une
entrée dans le fil d'alertes du site — la page « Alertes » l'affiche déjà.

IDEMPOTENT : un marqueur versionné (`rapport_hebdo_dernier.json`) retient la
dernière semaine ISO servie ; la publication tourne chaque nuit, le rapport
ne part qu'une fois par semaine, même si le vendredi est rejoué.
"""

from __future__ import annotations

import json
import re

import pandas as pd

from marketlab import config

MARQUEUR_PATH = config.DATA_DIR / "rapport_hebdo_dernier.json"
CONCOURS_PATH = config.ROOT / "site" / "donnees" / "concours.json"

# Vendredi (0 = lundi). Le passage de 22 h UTC du vendredi tombe après la
# clôture de Wall Street : la semaine de bourse est réellement finie.
JOUR_ENVOI = 4
JOURS_FENETRE = 7


def _semaine_iso(quand: pd.Timestamp) -> str:
    iso = quand.isocalendar()
    return f"{iso.year}-S{iso.week:02d}"


# ------------------------------------------------------------------- comptes

def _delta_semaine_pct(equity: list) -> float | None:
    """Variation sur ~7 jours à partir de la série d'équité [(date, valeur)].

    On prend le PREMIER point de la fenêtre : c'est l'équité au début de la
    semaine, pas une moyenne. None si la série ne couvre pas la fenêtre.
    """
    try:
        points = [(pd.Timestamp(str(d)[:10]), float(v)) for d, v in equity or []]
    except Exception:
        return None
    if len(points) < 2:
        return None
    seuil = points[-1][0] - pd.Timedelta(days=JOURS_FENETRE)
    fenetre = [(d, v) for d, v in points if d >= seuil]
    if len(fenetre) < 2 or fenetre[0][1] <= 0:
        return None
    return round((fenetre[-1][1] / fenetre[0][1] - 1) * 100, 2)


def bloc_comptes(concours: dict | None = None) -> dict:
    """Équités, semaine par compte, et la lecture de l'expérience des robots."""
    if concours is None:
        try:
            concours = json.loads(CONCOURS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"disponible": False}
    comptes = []
    for c in concours.get("comptes") or []:
        comptes.append({
            "nom": c.get("nom"), "est_robot": bool(c.get("est_robot")),
            "equite": c.get("equite"), "perf_%": c.get("perf_%"),
            "semaine_%": _delta_semaine_pct(c.get("equity") or []),
            "n_positions": c.get("n_positions"),
            "n_trades": c.get("n_trades"),
        })

    # L'expérience contrôlée : un écart par variable, jamais deux.
    perf = {c["nom"]: c.get("perf_%") for c in comptes}
    experience = {}
    if perf.get("claude") is not None:
        if perf.get("claude5") is not None:
            experience["horizon_court_pts"] = round(
                perf["claude5"] - perf["claude"], 2)
        if perf.get("claudefx") is not None:
            experience["specialisation_forex_pts"] = round(
                perf["claudefx"] - perf["claude"], 2)
    return {"disponible": True, "comptes": comptes, "experience": experience}


# -------------------------------------------------------------------- chaîne

def bloc_chaine(quand: pd.Timestamp | None = None) -> dict:
    """Ce que la chaîne a fait des idées sur 7 jours, et où en est son procès."""
    from marketlab import journal_chaine
    quand = quand or pd.Timestamp.now()
    sortie: dict = {"disponible": journal_chaine.JOURNAL_PATH.exists()}
    if sortie["disponible"]:
        j = pd.read_csv(journal_chaine.JOURNAL_PATH)
        j = j[pd.to_datetime(j["date"])
              >= quand.normalize() - pd.Timedelta(days=JOURS_FENETRE)]
        sortie["semaine"] = {
            "verdicts": int(len(j)),
            "retenus": int(j["retenue"].sum()),
            "par_motif": {str(k): int(v) for k, v in
                          j[j["retenue"] == 0]["etape_fatale"]
                          .value_counts().items()},
        }
    try:
        b = journal_chaine.bilan()
        sortie["proces"] = {"murs": b.get("murs"),
                           "en_attente": b.get("en_attente"),
                           "lecture": b.get("lecture")}
    except Exception as exc:
        sortie["proces"] = {"erreur": f"{type(exc).__name__}: {str(exc)[:60]}"}
    return sortie


# -------------------------------------------------------------- surveillance

def bloc_surveillance(quand: pd.Timestamp | None = None) -> dict:
    """Les gardes qui ont parlé cette semaine, lus dans le fil du site."""
    import io
    from marketlab import ftps
    quand = quand or pd.Timestamp.now()
    try:
        cfg = ftps.charger_config()
        session = ftps._connecter(cfg)
        try:
            tampon = io.BytesIO()
            session.retrbinary(f"RETR {cfg['dossier_distant'].rstrip('/')}/"
                               f"donnees/alertes_recentes.json", tampon.write)
        finally:
            try:
                session.quit()
            except Exception:
                session.close()
        fil = json.loads(tampon.getvalue().decode("utf-8"))
    except Exception:
        return {"disponible": False}
    seuil = quand - pd.Timedelta(days=JOURS_FENETRE)
    gardes = []
    for a in fil.get("alertes") or []:
        texte = str(a.get("texte", ""))
        if "🛡️" not in texte:
            continue
        try:
            if pd.Timestamp(a.get("quand")) < seuil:
                continue
        except Exception:
            pass
        gardes.append(texte)
    return {"disponible": True, "n": len(gardes), "dernieres": gardes[:5]}


# ------------------------------------------------------------------- jalons

def bloc_jalons() -> dict:
    """Ce qui mûrit tout seul, dit tel quel."""
    sortie = {}
    try:
        from marketlab import implicite
        d = implicite.comparer_previsionnistes()
        sortie["duels_iv"] = d.get("raison") if not d.get("mesurable") else (
            f"{d.get('gagnant')} gagne sur {d.get('n_duels')} fenêtres mûres")
    except Exception:
        sortie["duels_iv"] = "indisponible"
    try:
        from marketlab import regimes
        suspendus = regimes.charger_verdict().get("suspendus") or []
        sortie["regimes_suspendus"] = suspendus
    except Exception:
        sortie["regimes_suspendus"] = None
    return sortie


# ------------------------------------------------------------------ assemblage

def composer(quand: pd.Timestamp | None = None) -> dict:
    quand = quand or pd.Timestamp.now()
    return {
        "semaine": _semaine_iso(quand),
        "au": quand.strftime("%Y-%m-%d"),
        "comptes": bloc_comptes(),
        "chaine": bloc_chaine(quand),
        "surveillance": bloc_surveillance(quand),
        "jalons": bloc_jalons(),
    }


def texte(rapport: dict) -> str:
    """Le condensé en français, balises <b> comme le reste des notifications."""
    lignes = [f"<b>MarketLab — la semaine {rapport['semaine']}</b>"]

    c = rapport.get("comptes") or {}
    if c.get("disponible"):
        morceaux = []
        for cpt in c.get("comptes") or []:
            sem = cpt.get("semaine_%")
            morceaux.append(
                f"{cpt['nom']} {cpt.get('equite')} $"
                + (f" ({sem:+.1f} % sem.)" if sem is not None else ""))
        if morceaux:
            lignes.append("Comptes : " + " · ".join(morceaux))
        exp = c.get("experience") or {}
        if "horizon_court_pts" in exp:
            lignes.append(
                f"L'expérience : l'horizon court fait "
                f"{exp['horizon_court_pts']:+.1f} pts vs référence"
                + (f", le forex seul {exp['specialisation_forex_pts']:+.1f} pts"
                   if "specialisation_forex_pts" in exp else "") + ".")

    ch = rapport.get("chaine") or {}
    if ch.get("disponible") and ch.get("semaine"):
        s = ch["semaine"]
        motifs = " ; ".join(f"{n} {m}" for m, n in s["par_motif"].items())
        lignes.append(f"Chaîne (7 j) : {s['verdicts']} verdicts — "
                      f"{s['retenus']} retenus"
                      + (f", écartés : {motifs}" if motifs else "") + ".")
    proces = ch.get("proces") or {}
    if proces.get("lecture"):
        lignes.append(f"Procès du filtre : {proces['murs']} mûr(s), "
                      f"{proces['en_attente']} en attente — {proces['lecture']}")

    sv = rapport.get("surveillance") or {}
    if sv.get("disponible"):
        lignes.append("Surveillance : aucune garde déclenchée cette semaine."
                      if not sv["n"] else
                      f"Surveillance : {sv['n']} garde(s) déclenchée(s) — "
                      + " | ".join(sv["dernieres"][:2]))

    j = rapport.get("jalons") or {}
    if j.get("duels_iv"):
        lignes.append(f"Duels IV : {j['duels_iv']}.")
    if j.get("regimes_suspendus") is not None:
        lignes.append("Avis directionnel suspendu en : "
                      + (", ".join(j["regimes_suspendus"])
                         if j["regimes_suspendus"] else "aucun régime") + ".")
    return "\n".join(lignes)


def _texte_brut(html: str) -> str:
    return re.sub(r"</?b>", "", html)


def envoyer(force: bool = False, quand: pd.Timestamp | None = None) -> dict:
    """Compose et envoie le rapport si c'est le moment. Sinon dit pourquoi.

    Appelé chaque nuit par la publication ; ne part que le vendredi et une
    seule fois par semaine ISO. `force=True` court-circuite les deux gardes
    (essai manuel) mais ÉCRIT quand même le marqueur — un essai qui compte
    double la semaine serait pire que pas d'essai.
    """
    quand = quand or pd.Timestamp.now()
    semaine = _semaine_iso(quand)
    if not force and quand.dayofweek != JOUR_ENVOI:
        return {"envoye": False,
                "raison": f"pas vendredi (jour {quand.dayofweek})"}
    try:
        marque = json.loads(MARQUEUR_PATH.read_text(encoding="utf-8"))
    except Exception:
        marque = {}
    if not force and marque.get("semaine") == semaine:
        return {"envoye": False, "raison": f"déjà servi pour {semaine}"}

    rapport = composer(quand)
    corps = texte(rapport)

    resultat = {"envoye": False, "semaine": semaine, "notification": False,
                "fil": False}
    try:
        from marketlab import notify
        resultat["notification"] = bool(notify.envoyer(corps, urgent=False))
    except Exception as exc:
        resultat["notification_erreur"] = f"{type(exc).__name__}: {str(exc)[:60]}"
    try:
        from marketlab import fil_alertes
        fil_alertes.publier([("📒 " + _texte_brut(corps), False)])
        resultat["fil"] = True
    except Exception as exc:
        resultat["fil_erreur"] = f"{type(exc).__name__}: {str(exc)[:60]}"

    resultat["envoye"] = resultat["notification"] or resultat["fil"]
    if resultat["envoye"]:
        MARQUEUR_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARQUEUR_PATH.write_text(json.dumps(
            {"semaine": semaine, "envoye_le": quand.strftime("%Y-%m-%d %H:%M")},
            ensure_ascii=False), encoding="utf-8")
    return resultat
