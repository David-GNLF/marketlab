"""Le robot de trading virtuel « claude » + tenue quotidienne des comptes.

Rôle 1 — TENUE DES COMPTES (tous, robot et humains) : chaque soir, les
positions ouvertes sont confrontées aux extrêmes de la séance (plus haut /
plus bas réels) : stop touché → clôture au stop ; objectif touché → clôture
à l'objectif ; si les deux le même jour, le STOP est réputé touché en
premier (hypothèse prudente) ; marge épuisée → liquidation à zéro (pas de
solde négatif). Un point d'équité est ajouté à chaque compte.

Rôle 2 — LE ROBOT : le compte « claude » (1 000 $ virtuels) applique les
verdicts de l'outil, avec des règles ÉCRITES :
- n'ouvre que sur avis Favorable avec plan et taille > 0 (les vetos du
  verdict s'appliquent donc d'office) ;
- LONG uniquement — le bilan sur 2 ans a montré que les avis Défavorable
  précédaient des hausses : pas de short tant que le bilan ne le justifie pas ;
- mise = 5 % de l'équité × multiplicateur de taille du verdict ; levier par
  classe d'actif (forex ×5, matières ×3, actions/crypto ×2) ; 4 positions
  maximum ; jamais deux fois le même actif ;
- stop et objectif = ceux du plan du verdict ; clôture anticipée si l'avis
  retombe à Défavorable ou S'abstenir ;
- chaque décision est journalisée avec sa raison — l'inaction aussi.

Les comptes vivent sur l'hébergement (trading/comptes/*.json) : lecture et
écriture par FTPS. Cette page et le robot n'écrivent jamais le même fichier
en dehors de la fenêtre de tenue quotidienne, brève et nocturne.
"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from marketlab import config, ftps, notify, risque_portefeuille
from marketlab.data import get_ohlcv

CAPITAL_DEPART = 1000.0
# TROIS robots, mêmes règles, UNE variable chacun. C'est une expérience
# contrôlée : « claude » est la référence, « claude5 » n'en diffère que par
# l'horizon, « claudefx » que par l'univers. Changer deux choses à la fois
# rendrait tout écart ininterprétable.
ROBOTS = {
    # tous marchés, horizon officiel : la référence
    "claude": {"cle": "dossiers", "horizon": 20, "classes": None,
               "libelle": "tous marchés, 20 séances"},
    # même univers, horizon court : isole la valeur de l'HORIZON
    "claude5": {"cle": "dossiers_court", "horizon": 5, "classes": None,
                "libelle": "tous marchés, 5 séances"},
    # même horizon que la référence, univers restreint au forex : isole la
    # valeur de la SPÉCIALISATION. Le forex a ses propres moteurs (carry,
    # différentiels de taux) et une volatilité bien plus faible que les
    # actions ou les matières — d'où un levier ×5 contre ×2.
    "claudefx": {"cle": "dossiers", "horizon": 20, "classes": ["Forex"],
                 "libelle": "forex uniquement, 20 séances"},
}
SPREAD_PCT = 0.05
MAX_POSITIONS = 4
PART_EQUITE = 0.05
TAUX_PORTAGE_ANNUEL = 0.06   # coût annuel de la part empruntée (levier)
LEVIERS = {"Forex": 5, "Matières": 3, "Actions": 2, "Crypto": 2, "Indices": 2}
VERDICTS_LOCAL = config.ROOT / "site" / "donnees" / "verdicts.json"
CONCOURS_LOCAL = config.ROOT / "site" / "donnees" / "concours.json"


def _maintenant() -> str:
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------- comptes

def _lister_comptes(session, base: str) -> list[str]:
    try:
        return [n for n, f in session.mlsd(f"{base}/trading/comptes")
                if f.get("type") == "file" and n.endswith(".json")]
    except Exception:
        return []


def _telecharger(session, base: str, nom_fichier: str) -> dict | None:
    tampon = io.BytesIO()
    try:
        session.retrbinary(f"RETR {base}/trading/comptes/{nom_fichier}",
                           tampon.write)
        return json.loads(tampon.getvalue().decode("utf-8"))
    except Exception:
        return None


def _televerser(session, base: str, nom_fichier: str, compte: dict) -> None:
    contenu = json.dumps(compte, ensure_ascii=False, indent=1).encode("utf-8")
    session.storbinary(f"STOR {base}/trading/comptes/{nom_fichier}",
                       io.BytesIO(contenu))


# --------------------------------------------------------------- tenue du jour

def tenir_compte(compte: dict) -> list[str]:
    """Stops, objectifs et liquidations sur les extrêmes de séance."""
    evenements = []
    restantes = []
    for p in compte.get("positions", []):
        try:
            df = get_ohlcv(p["symbole"], lookback_days=30)
            jour = df.iloc[-1]
            haut, bas, clot = float(jour["high"]), float(jour["low"]), \
                float(jour["close"])
        except Exception:
            restantes.append(p)
            continue
        sens = 1 if p["sens"] == "long" else -1
        prix_liquidation = p["prix_entree"] - sens * p["prix_entree"] \
            / max(p["levier"], 1)

        sortie, motif = None, None
        # ordre prudent : liquidation puis stop puis objectif
        if (sens == 1 and bas <= prix_liquidation) or \
           (sens == -1 and haut >= prix_liquidation):
            sortie, motif = prix_liquidation, "LIQUIDATION (marge épuisée)"
        elif p.get("stop") and ((sens == 1 and bas <= p["stop"])
                                or (sens == -1 and haut >= p["stop"])):
            sortie, motif = float(p["stop"]), "stop touché"
        elif p.get("objectif") and ((sens == 1 and haut >= p["objectif"])
                                    or (sens == -1 and bas <= p["objectif"])):
            sortie, motif = float(p["objectif"]), "objectif atteint"

        if sortie is not None:
            pnl = (sortie - p["prix_entree"]) * p["quantite"] * sens
            compte["solde"] += max(0.0, p["marge"] + pnl)
            compte.setdefault("historique", []).append({
                "symbole": p["symbole"], "sens": p["sens"], "marge": p["marge"],
                "levier": p["levier"], "entree": p["prix_entree"],
                "sortie": round(sortie, 4), "pnl": round(pnl, 2),
                "ouvert_le": p["ouvert_le"], "ferme_le": _maintenant(),
                "motif": motif})
            evenements.append(f"{p['symbole']} {p['sens']} : {motif} "
                              f"(P&L {pnl:+.2f} $)")
        else:
            restantes.append(p)
    compte["positions"] = restantes
    return evenements


def executer_ordres(compte: dict) -> list[str]:
    """Ordres limite/stop placés sur la page trading : exécution dès que la
    séance a touché le prix demandé (haut/bas réels). La mise a déjà été
    réservée au placement ; la position ouverte ne sera confrontée aux
    extrêmes qu'à partir du lendemain (l'heure de déclenchement dans la
    séance est inconnue)."""
    evenements = []
    restants = []
    aujourdhui = pd.Timestamp.now().strftime("%Y-%m-%d")
    for o in compte.get("ordres", []):
        # échéance d'abord : un ordre périmé rend sa mise, il ne s'exécute pas
        if o.get("expire_le") and str(o["expire_le"]) < aujourdhui:
            compte["solde"] += float(o.get("marge", 0))
            evenements.append(
                f"{o['symbole']} : ordre {o.get('type', '?')} expiré "
                f"(placé le {o.get('cree_le', '?')}, jamais touché) — "
                f"{float(o.get('marge', 0)):.2f} $ rendus au solde")
            continue
        try:
            df = get_ohlcv(o["symbole"], lookback_days=30)
            jour = df.iloc[-1]
            haut, bas = float(jour["high"]), float(jour["low"])
        except Exception:
            restants.append(o)
            continue
        sens = 1 if o["sens"] == "long" else -1
        # achat limite / vente stop : le marché descend au prix (bas) ;
        # achat stop / vente limite : le marché monte au prix (haut)
        if (o["sens"] == "long") == (o["type"] == "limite"):
            touche = bas <= float(o["prix"])
        else:
            touche = haut >= float(o["prix"])
        if not touche:
            restants.append(o)
            continue
        prix = float(o["prix"]) * (1 + sens * SPREAD_PCT / 100)
        notionnel = float(o["marge"]) * float(o["levier"])
        compte.setdefault("positions", []).append({
            "id": o["id"], "symbole": o["symbole"], "sens": o["sens"],
            "marge": o["marge"], "levier": o["levier"],
            "notionnel": round(notionnel, 2),
            "quantite": notionnel / prix, "prix_entree": prix,
            "stop": o.get("stop"), "objectif": o.get("objectif"),
            "ouvert_le": _maintenant(), "source": "ordre"})
        evenements.append(f"{o['symbole']} : ordre {o['type']} {o['sens']} "
                          f"exécuté @ {prix:.4f} (la séance a touché "
                          f"{o['prix']})")
    compte["ordres"] = restants
    return evenements


def facturer_portage(compte: dict) -> list[str]:
    """Frais de portage (swap) sur les positions gardées d'un jour à l'autre.

    Le levier n'est pas gratuit dans la vraie vie : on emprunte la différence
    entre le notionnel et sa propre mise, et cet emprunt se paie chaque nuit.
    Sans ce coût, garder un levier ×20 pendant un mois paraîtrait indolore et
    le robot serait jugé plus favorablement qu'il ne le mérite.

    Taux retenu : TAUX_PORTAGE_ANNUEL sur la part empruntée, prélevé une fois
    par jour calendaire. Volontairement simple et unique pour toutes les
    classes d'actifs — mieux vaut un coût approximatif qu'un coût absent.
    """
    evenements = []
    aujourdhui = pd.Timestamp.now().strftime("%Y-%m-%d")
    total = 0.0
    for p in compte.get("positions", []):
        if p.get("portage_le") == aujourdhui:
            continue                      # déjà facturé aujourd'hui
        emprunte = max(0.0, float(p.get("notionnel", 0)) - float(p["marge"]))
        frais = emprunte * TAUX_PORTAGE_ANNUEL / 365
        if frais <= 0:
            continue
        p["frais_portage_cumules"] = round(
            float(p.get("frais_portage_cumules", 0)) + frais, 4)
        p["portage_le"] = aujourdhui
        total += frais
    if total > 0:
        compte["solde"] -= total
        evenements.append(f"frais de portage de la nuit : -{total:.2f} $ "
                          f"({len(compte.get('positions', []))} position(s) "
                          f"à levier)")
    return evenements


def _cours_publie(symbole: str) -> float | None:
    """Le dernier cours PUBLIÉ par le site — la référence de valorisation
    unique de toute la plateforme (page trading, panneau admin, concours).
    Repli sur le cours frais si la fiche manque."""
    fiche = config.ROOT / "site" / "donnees" / "titres" / f"{symbole}.json"
    try:
        if fiche.exists():
            prix = json.loads(fiche.read_text(encoding="utf-8"))
            valeur = prix.get("signaux", {}).get("close")
            if valeur:
                return float(valeur)
    except Exception:
        pass
    try:
        return float(get_ohlcv(symbole, lookback_days=30)["close"].iloc[-1])
    except Exception:
        return None


def _equite(compte: dict) -> float:
    total = compte["solde"]
    # la mise réservée par les ordres en attente reste la propriété du compte
    total += sum(float(o.get("marge", 0)) for o in compte.get("ordres", []))
    for p in compte.get("positions", []):
        prix = _cours_publie(p["symbole"])
        if prix is None:
            total += p["marge"]
            continue
        sens = 1 if p["sens"] == "long" else -1
        total += p["marge"] + (prix - p["prix_entree"]) * p["quantite"] * sens
    return total


# ------------------------------------------------------------------- le robot

def decisions_robot(compte: dict, verdicts: list[dict]) -> list[str]:
    journal = []
    par_symbole = {d["symbole"]: d for d in verdicts if "erreur" not in d}

    # 1. clôtures : le verdict s'est retourné
    restantes = []
    for p in compte["positions"]:
        d = par_symbole.get(p["symbole"])
        if d and d["avis"] in ("Défavorable", "S'abstenir"):
            try:
                prix = float(get_ohlcv(p["symbole"],
                                       lookback_days=30)["close"].iloc[-1])
            except Exception:
                restantes.append(p)
                continue
            prix *= 1 - SPREAD_PCT / 100
            sens = 1 if p["sens"] == "long" else -1
            pnl = (prix - p["prix_entree"]) * p["quantite"] * sens
            compte["solde"] += max(0.0, p["marge"] + pnl)
            compte.setdefault("historique", []).append({
                "symbole": p["symbole"], "sens": p["sens"], "marge": p["marge"],
                "levier": p["levier"], "entree": p["prix_entree"],
                "sortie": round(prix, 4), "pnl": round(pnl, 2),
                "ouvert_le": p["ouvert_le"], "ferme_le": _maintenant(),
                "motif": f"verdict retombé à « {d['avis']} »"})
            journal.append(f"FERMÉ {p['symbole']} (verdict « {d['avis']} ») : "
                           f"P&L {pnl:+.2f} $")
        else:
            restantes.append(p)
    compte["positions"] = restantes

    # 2. ouvertures : Favorable + plan + taille > 0
    detenues = {p["symbole"] for p in compte["positions"]}
    candidats = sorted(
        [d for d in verdicts if "erreur" not in d
         and d["avis"] == "Favorable" and d.get("plan")
         and d.get("taille_multiplicateur", 0) > 0
         and d["symbole"] not in detenues],
        key=lambda d: -d["note_globale"])

    for d in candidats:
        if len(compte["positions"]) >= MAX_POSITIONS:
            journal.append(f"IGNORÉ {d['symbole']} (Favorable "
                           f"{d['note_globale']:+.0f}) : {MAX_POSITIONS} "
                           "positions déjà ouvertes")
            continue
        equite = _equite(compte)
        mise = round(equite * PART_EQUITE * d["taille_multiplicateur"], 2)
        levier = LEVIERS.get(d.get("classe", "Actions"), 2)

        # RISQUE D'ENSEMBLE. Jusqu'ici chaque position était dimensionnée
        # SEULE : quatre lignes à 5 % passaient pour quatre paris alors
        # qu'elles pouvaient n'en être qu'un. Le 17/06/2026, les plus fortes
        # secousses ont touché EURUSD, AUDUSD, GBPUSD, USDCHF et l'or le même
        # jour — un choc du dollar, un seul.
        # Le facteur se calcule sur les corrélations de RÉGIME TENDU et non sur
        # la moyenne : se croire diversifié les jours de stress est exactement
        # l'erreur qu'on cherche à éviter.
        try:
            risque = risque_portefeuille.evaluer(
                compte["positions"], equite,
                {"symbole": d["symbole"], "sens": "long",
                 "marge": mise, "levier": levier})
        except Exception as exc:      # un garde-fou en panne s'efface
            risque = {"facteur": 1.0,
                      "raisons": [f"non mesurable : {str(exc)[:60]}"]}
        if risque["facteur"] <= 0:
            journal.append(f"ÉCARTÉ {d['symbole']} (concentration) : "
                           + " ; ".join(risque["raisons"]))
            continue
        if risque["facteur"] < 1:
            mise = round(mise * risque["facteur"], 2)
            journal.append(f"TAILLE RÉDUITE {d['symbole']} → {mise} $ : "
                           + " ; ".join(risque["raisons"]))

        if mise < 10 or mise > compte["solde"]:
            journal.append(f"IGNORÉ {d['symbole']} : mise calculée {mise} $ "
                           "hors limites")
            continue
        prix = float(d["plan"]["entree"]) * (1 + SPREAD_PCT / 100)
        notionnel = mise * levier
        compte["solde"] -= mise
        compte["positions"].append({
            "id": f"rb{len(compte.get('historique', [])) + len(compte['positions'])}",
            "symbole": d["symbole"], "sens": "long", "marge": mise,
            "levier": levier, "notionnel": round(notionnel, 2),
            "quantite": notionnel / prix, "prix_entree": prix,
            "stop": d["plan"]["stop"], "objectif": d["plan"]["objectif"],
            "ouvert_le": _maintenant(), "source": "robot",
            "raison": f"verdict Favorable {d['note_globale']:+.0f} "
                      f"(concordance {d.get('concordance_%', '?')} %), "
                      f"taille ×{d['taille_multiplicateur']}"})
        journal.append(f"OUVERT long {d['symbole']} : {mise} $ × {levier} "
                       f"(note {d['note_globale']:+.0f}, stop "
                       f"{d['plan']['stop']}, objectif {d['plan']['objectif']})")

    if not journal:
        journal.append("aucune action : pas de signal exploitable aujourd'hui")
    return journal


# ---------------------------------------------------------------------- main

def main() -> int:
    if not VERDICTS_LOCAL.exists():
        print("verdicts.json absent : lancer la génération d'abord")
        return 1
    publie = json.loads(VERDICTS_LOCAL.read_text(encoding="utf-8"))
    verdicts_par_robot = {}
    for nom, cfg_robot in ROBOTS.items():
        v = publie.get(cfg_robot["cle"]) or []
        classes = cfg_robot.get("classes")
        if classes:
            v = [d for d in v if d.get("classe") in classes]
        verdicts_par_robot[nom] = v
    for nom, v in verdicts_par_robot.items():
        print(f"robot « {nom} » ({ROBOTS[nom].get('libelle', '')}) : "
              f"{len(v)} verdict(s) retenu(s)")

    cfg = ftps.charger_config()
    session = ftps._connecter(cfg)
    base = cfg["dossier_distant"].rstrip("/")
    classement = []
    comptes_robots: list[dict] = []   # conservés pour le rapport de séance
    mouvements: list[tuple[str, list[str], float]] = []
    try:
        ftps._assurer_dossier(session, f"{base}/trading/comptes")
        fichiers = _lister_comptes(session, base)

        # les comptes des robots sont créés au premier passage
        for nom, cfg_robot in ROBOTS.items():
            if f"{nom}.json" in fichiers:
                continue
            _televerser(session, base, f"{nom}.json", {
                "nom": nom, "capital_initial": CAPITAL_DEPART,
                "solde": CAPITAL_DEPART, "positions": [], "ordres": [],
                "historique": [],
                "equity": [[_maintenant(), CAPITAL_DEPART]],
                "horizon": cfg_robot["horizon"],
                "journal_robot": [f"compte créé — verdicts à "
                                  f"{cfg_robot['horizon']} séances"],
                "cree_le": _maintenant()})
            fichiers.append(f"{nom}.json")
            print(f"compte robot « {nom} » créé ({CAPITAL_DEPART:.0f} $ "
                  f"virtuels, horizon {cfg_robot['horizon']})")

        for fichier in fichiers:
            compte = _telecharger(session, base, fichier)
            if not compte:
                continue
            evenements = tenir_compte(compte)
            evenements += executer_ordres(compte)
            evenements += facturer_portage(compte)
            est_robot = compte["nom"] in ROBOTS
            if est_robot:
                v = verdicts_par_robot.get(compte["nom"]) or []
                if v:
                    evenements += decisions_robot(compte, v)
                else:
                    evenements.append("aucun verdict disponible pour cet "
                                      "horizon : robot en attente")
                compte["horizon"] = ROBOTS[compte["nom"]]["horizon"]
                compte.setdefault("journal_robot", []).extend(
                    [f"[{_maintenant()}] {e}" for e in evenements])
                compte["journal_robot"] = compte["journal_robot"][-60:]
            equite = round(_equite(compte), 2)
            compte.setdefault("equity", []).append([_maintenant(), equite])
            compte["equity"] = compte["equity"][-400:]
            _televerser(session, base, fichier, compte)
            for e in evenements:
                print(f"  {compte['nom']} : {e}")
            if evenements:
                mouvements.append((compte["nom"], evenements, equite))
            if est_robot:
                comptes_robots.append(compte)

            classement.append({
                "nom": compte["nom"], "est_robot": est_robot,
                "horizon": ROBOTS.get(compte["nom"], {}).get("horizon"),
                "specialite": ROBOTS.get(compte["nom"], {}).get("libelle"),
                "equite": equite,
                "perf_%": round((equite / compte["capital_initial"] - 1) * 100, 2),
                "n_positions": len(compte["positions"]),
                "n_trades": len(compte.get("historique", [])),
                "positions": ([{k: p.get(k) for k in
                                ("symbole", "sens", "levier", "marge",
                                 "prix_entree", "stop", "objectif", "raison")}
                               for p in compte["positions"]]
                              if est_robot else None),
                "journal": (compte.get("journal_robot", [])[-15:]
                            if est_robot else None),
                # Le détail des trades clos est exposé pour TOUS les comptes,
                # robots comme humains : sans lui, on constate une perte sans
                # jamais savoir sur quoi ni pourquoi.
                "bilan_trades": bilan_trades(compte),
                "trades": _detail_trades(compte),
                "equity": compte["equity"][-120:],
            })
    finally:
        try:
            session.quit()
        except Exception:
            session.close()

    classement.sort(key=lambda c: -c["perf_%"])
    CONCOURS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    CONCOURS_LOCAL.write_text(json.dumps({
        "date": _maintenant(),
        "capital_depart": CAPITAL_DEPART,
        "comptes": classement,
        "regles_robot": ("long uniquement sur verdict Favorable avec plan ; "
                         "mise 5 % de l'équité × taille du verdict ; levier "
                         "forex ×5, matières ×3, actions/crypto ×2 ; 4 "
                         "positions max ; stop/objectif du plan ; clôture si "
                         "le verdict se retourne ; tout est journalisé."),
        "robots": [{"nom": n, "horizon": c["horizon"],
                    "specialite": c.get("libelle")}
                   for n, c in ROBOTS.items()],
        "experience": ("Trois robots suivent EXACTEMENT les mêmes règles et ne "
                       "diffèrent que par un paramètre chacun. « claude » "
                       "(tous marchés, 20 séances) est la référence. "
                       "« claude5 » n'en diffère que par l'HORIZON "
                       "(5 séances) : l'écart mesure ce que vaut "
                       "l'horizon. « claudefx » n'en diffère que par "
                       "l'UNIVERS (forex uniquement) : l'écart mesure ce "
                       "que vaut la spécialisation. Une variable à la "
                       "fois, sinon on ne saurait pas à quoi attribuer "
                       "la différence."),
        "avertissement": "Argent virtuel — l'environnement mesure la "
                         "fiabilité de l'outil, il ne constitue pas un "
                         "conseil en investissement.",
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\nconcours.json écrit : {len(classement)} compte(s)")
    # Rapport de séance : ce qui était prévu confronté à ce qui est advenu.
    # Il rejoue chaque trade sur les cours réels — c'est ce matériau qui
    # permet de corriger les règles au lieu de les deviner.
    try:
        r = rapport_seance.rapport_global(comptes_robots)
        RAPPORT_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        RAPPORT_LOCAL.write_text(json.dumps(r, ensure_ascii=False),
                                 encoding="utf-8")
        for bloc in r["robots"]:
            print(f"  rapport {bloc['nom']} : {bloc['n']} trade(s) rejoué(s)")
            for c in bloc.get("constats", []):
                print(f"    - {c}")
    except Exception as exc:
        print(f"rapport de séance non produit (non bloquant) : {str(exc)[:90]}")

    notifier_mouvements(mouvements)
    return 0


def bilan_trades(compte: dict) -> dict:
    """Appréciation chiffrée des trades clos d'un compte.

    Sans ce bilan, le site n'affichait qu'une équité : on voyait qu'on
    perdait, sans savoir sur quoi ni pourquoi. Or c'est exactement ce détail
    qui permet de corriger les règles — un stop trop serré, un motif de
    sortie qui revient trop souvent, une classe d'actif qui coûte.
    """
    hist = compte.get("historique", [])
    if not hist:
        return {"n": 0, "message": "aucun trade clos pour l'instant"}
    pnls = [float(t.get("pnl", 0)) for t in hist]
    gains = [p for p in pnls if p > 0]
    pertes = [p for p in pnls if p <= 0]

    par_motif: dict[str, dict] = {}
    for t in hist:
        m = par_motif.setdefault(str(t.get("motif", "?")), {"n": 0, "pnl": 0.0})
        m["n"] += 1
        m["pnl"] += float(t.get("pnl", 0))
    for m in par_motif.values():
        m["pnl"] = round(m["pnl"], 2)

    pire = min(hist, key=lambda t: float(t.get("pnl", 0)))
    meilleur = max(hist, key=lambda t: float(t.get("pnl", 0)))
    return {
        "n": len(hist),
        "pnl_total": round(sum(pnls), 2),
        "gagnants": len(gains), "perdants": len(pertes),
        "taux_reussite_%": round(len(gains) / len(hist) * 100, 1),
        "gain_moyen": round(sum(gains) / len(gains), 2) if gains else None,
        "perte_moyenne": round(sum(pertes) / len(pertes), 2) if pertes else None,
        "pire_trade": {"symbole": pire["symbole"], "pnl": round(float(pire["pnl"]), 2),
                       "motif": pire.get("motif")},
        "meilleur_trade": {"symbole": meilleur["symbole"],
                           "pnl": round(float(meilleur["pnl"]), 2),
                           "motif": meilleur.get("motif")},
        "par_motif": par_motif,
    }


def _detail_trades(compte: dict, maxi: int = 25) -> list[dict]:
    """Les derniers trades clos, du plus récent au plus ancien, avec de quoi
    juger chacun : combien on a risqué, ce que ça a rendu, et pourquoi c'est
    sorti."""
    detail = []
    for t in reversed(compte.get("historique", [])[-maxi:]):
        marge = float(t.get("marge", 0)) or 1.0
        entree, sortie = float(t.get("entree", 0)), float(t.get("sortie", 0))
        detail.append({
            "symbole": t.get("symbole"), "sens": t.get("sens"),
            "levier": t.get("levier"), "marge_$": round(marge, 2),
            "entrée": round(entree, 4), "sortie": round(sortie, 4),
            "variation_%": round((sortie / entree - 1) * 100, 2) if entree else None,
            "P&L_$": round(float(t.get("pnl", 0)), 2),
            "P&L_sur_mise_%": round(float(t.get("pnl", 0)) / marge * 100, 1),
            "motif": t.get("motif"),
            "ouvert_le": t.get("ouvert_le"), "fermé_le": t.get("ferme_le"),
        })
    return detail


def notifier_mouvements(mouvements: list[tuple[str, list[str], float]]) -> bool:
    """Prévient dès qu'un compte a bougé : le robot ne doit plus agir en
    silence, et un stop touché sur VOTRE compte doit se savoir sans avoir à
    visiter le site. Une nuit sans mouvement n'envoie rien — le bruit tue
    l'attention."""
    if not mouvements:
        print("aucun mouvement : pas de notification")
        return False
    lignes = []
    for nom, evenements, equite in mouvements:
        icone = "🤖" if nom == "claude" else "👤"
        lignes.append(f"{icone} <b>{nom}</b> — équité {equite:.2f} $")
        lignes.extend(f"• {e}" for e in evenements)
    corps = "<b>Mouvements de trading</b>\n" + "\n".join(lignes)
    # urgent si une liquidation a eu lieu : c'est la seule qui ne se rattrape pas
    urgent = any("LIQUIDATION" in e for _, evs, _ in mouvements for e in evs)
    try:
        envoye = notify.envoyer(corps, urgent=urgent)
    except Exception as exc:
        print(f"notification impossible (non bloquant) : {str(exc)[:80]}")
        return False
    print("notification envoyée" if envoye
          else "aucun canal de notification configuré")
    return envoye


if __name__ == "__main__":
    raise SystemExit(main())
