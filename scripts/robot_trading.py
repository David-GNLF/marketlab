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

from marketlab import (config, ftps, notify, rapport_seance,
                       risque_portefeuille, surveillance)
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
    # Même chose que « claude » en tout point, SAUF qu'il obéit à la
    # suspension de régime. La seule variable est donc l'obéissance au veto,
    # et l'écart avec « claude » mesure ce que l'abstention a coûté ou
    # rapporté. Sans lui, on suspendrait sans jamais savoir si on a bien fait.
    "claudeprudent": {"cle": "dossiers", "horizon": 20, "classes": None,
                      "respecte_suspension": True,
                      "libelle": "tous marchés, 20 séances, respecte "
                                 "l'abstention de régime"},
}
SPREAD_PCT = 0.05
MAX_POSITIONS = 4
PART_EQUITE = 0.05
TAUX_PORTAGE_ANNUEL = 0.06   # coût annuel de la part empruntée (levier)
LEVIERS = {"Forex": 5, "Matières": 3, "Actions": 2, "Crypto": 2, "Indices": 2}
VERDICTS_LOCAL = config.ROOT / "site" / "donnees" / "verdicts.json"
RAPPORT_LOCAL = config.ROOT / "site" / "donnees" / "rapport_seance.json"
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

def _seances_a_confronter(compte: dict, debut, df) -> list:
    """Toutes les séances MANQUÉES depuis la dernière tenue, pas juste la dernière.

    INCIDENT DES 07-09/08/2026. La publication est tombée trois nuits de
    suite (un test qui pourrissait avec l'horloge) et la tenue avec elle.
    Au retour, l'ancienne tenue n'aurait confronté les stops qu'à la DERNIÈRE
    séance : les extrêmes de jeudi et vendredi seraient passés à la trappe,
    et un stop touché jeudi serait resté ouvert comme si de rien n'était.

    La borne est le plus tardif de : la dernière tenue (horodatage du dernier
    point d'équité — il n'est ajouté qu'après une tenue réussie) et
    l'ouverture de la position ou de l'ordre (`debut`) — une position ouverte
    aujourd'hui ne doit pas être jugée sur des extrêmes d'avant sa naissance.
    Sans index daté ou sans repère : la dernière séance seule, l'exact
    comportement d'avant (re-confronter une séance déjà tenue est inoffensif,
    le résultat est déterministe sur les mêmes extrêmes).
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        return [df.iloc[-1]]
    borne = None
    try:
        equity = compte.get("equity") or []
        borne = pd.Timestamp(str(equity[-1][0])[:10])
    except Exception:
        borne = None
    try:
        naissance = pd.Timestamp(str(debut)[:10])
        borne = naissance if borne is None else max(borne, naissance)
    except Exception:
        pass
    if borne is None:
        return [df.iloc[-1]]
    fenetre = df[df.index.normalize() > borne]
    if fenetre.empty:
        return [df.iloc[-1]]
    return [ligne for _, ligne in fenetre.iterrows()]


def tenir_compte(compte: dict) -> list[str]:
    """Stops, objectifs et liquidations sur les extrêmes de séance.

    Chaque séance manquée est rejouée dans l'ordre CHRONOLOGIQUE : la
    première protection touchée gagne, comme si la tenue avait eu lieu le
    soir même. À l'intérieur d'une séance, l'ordre prudent reste
    liquidation puis stop puis objectif.
    """
    evenements = []
    restantes = []
    for p in compte.get("positions", []):
        try:
            df = get_ohlcv(p["symbole"], lookback_days=30)
            seances = _seances_a_confronter(compte, p.get("ouvert_le"), df)
        except Exception:
            restantes.append(p)
            continue
        sens = 1 if p["sens"] == "long" else -1
        prix_liquidation = p["prix_entree"] - sens * p["prix_entree"] \
            / max(p["levier"], 1)

        sortie, motif = None, None
        for jour in seances:
            try:
                haut, bas = float(jour["high"]), float(jour["low"])
            except Exception:
                continue
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
                break

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
            # STOP SUIVEUR (opt-in au placement, jamais imposé) : la position
            # a survécu à la séance, le stop remonte pour conserver l'ÉCART
            # INITIAL par rapport au dernier cours de clôture. Il ne descend
            # JAMAIS — un stop suiveur qui recule n'est plus une protection.
            # Les robots ne posent pas ce drapeau : leurs règles d'expérience
            # restent intactes.
            try:
                evenements += _suivre_stop(p, seances)
            except Exception:
                pass                     # le suiveur en panne s'efface
            restantes.append(p)
    compte["positions"] = restantes
    return evenements


def _suivre_stop(p: dict, seances: list) -> list[str]:
    """Remonte le stop d'une position marquée `suiveur`. Liste des événements."""
    distance = float(p.get("suiveur_distance_pct") or 0) / 100
    if not p.get("suiveur") or not p.get("stop") or distance <= 0 or not seances:
        return []
    try:
        cloture = float(seances[-1]["close"])
    except Exception:
        return []
    sens = 1 if p["sens"] == "long" else -1
    candidat = cloture * (1 - sens * distance)
    ancien = float(p["stop"])
    if (sens == 1 and candidat > ancien) or (sens == -1 and candidat < ancien):
        p["stop"] = round(candidat, 4)
        return [f"{p['symbole']} : stop suiveur resserré {ancien:.4f} → "
                f"{p['stop']:.4f} (clôture {cloture:.4f}, écart gardé "
                f"{distance * 100:.2f} %)"]
    return []


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
            seances = _seances_a_confronter(compte, o.get("cree_le"), df)
        except Exception:
            restants.append(o)
            continue
        sens = 1 if o["sens"] == "long" else -1
        # achat limite / vente stop : le marché descend au prix (bas) ;
        # achat stop / vente limite : le marché monte au prix (haut).
        # Chaque séance manquée compte : un déclenchement raté jeudi ne doit
        # pas attendre qu'un NOUVEAU jour retouche le prix.
        touche = False
        for jour in seances:
            try:
                haut, bas = float(jour["high"]), float(jour["low"])
            except Exception:
                continue
            if (o["sens"] == "long") == (o["type"] == "limite"):
                touche = bas <= float(o["prix"])
            else:
                touche = haut >= float(o["prix"])
            if touche:
                break
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
            # le stop suiveur choisi au placement de l'ordre suit la position
            "suiveur": o.get("suiveur"),
            "suiveur_distance_pct": o.get("suiveur_distance_pct"),
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

def _lecture(d: dict, respecte_suspension: bool) -> tuple[str, float]:
    """Avis et taille vus par ce robot : avec ou sans la suspension.

    Le site, lui, montre toujours l'avis suspendu — c'est le conseil, et
    il reste prudent. Ce qui est séparé ici, c'est la MESURE.
    """
    if respecte_suspension or "avis_hors_suspension" not in d:
        return d["avis"], float(d.get("taille_multiplicateur", 0) or 0)
    return (d["avis_hors_suspension"],
            float(d.get("taille_hors_suspension", 0) or 0))


def decisions_robot(compte: dict, verdicts: list[dict],
                    respecte_suspension: bool = False) -> list[str]:
    journal = []
    par_symbole = {d["symbole"]: d for d in verdicts if "erreur" not in d}

    # 1. clôtures : le verdict s'est retourné
    restantes = []
    for p in compte["positions"]:
        d = par_symbole.get(p["symbole"])
        avis_vu = _lecture(d, respecte_suspension)[0] if d else None
        if avis_vu in ("Défavorable", "S'abstenir"):
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
                "motif": f"verdict retombé à « {avis_vu} »"})
            journal.append(f"FERMÉ {p['symbole']} (verdict « {avis_vu} ») : "
                           f"P&L {pnl:+.2f} $")
        else:
            restantes.append(p)
    compte["positions"] = restantes

    # 2. ouvertures : Favorable + plan + taille > 0
    detenues = {p["symbole"] for p in compte["positions"]}
    candidats = sorted(
        [d for d in verdicts if "erreur" not in d
         and _lecture(d, respecte_suspension)[0] == "Favorable"
         and d.get("plan")
         and _lecture(d, respecte_suspension)[1] > 0
         and d["symbole"] not in detenues],
        key=lambda d: -d["note_globale"])

    for d in candidats:
        if len(compte["positions"]) >= MAX_POSITIONS:
            journal.append(f"IGNORÉ {d['symbole']} (Favorable "
                           f"{d['note_globale']:+.0f}) : {MAX_POSITIONS} "
                           "positions déjà ouvertes")
            continue
        equite = _equite(compte)
        mise = round(equite * PART_EQUITE
                     * _lecture(d, respecte_suspension)[1], 2)
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

def charger_verdicts_publies() -> dict:
    """Les verdicts du jour s'ils existent et se lisent — {} sinon.

    LA TENUE NE DÉPEND PAS DE LA GÉNÉRATION. Avant, un verdicts.json absent
    arrêtait tout le script : trois nuits de publication en panne (07-09/08)
    ont donc aussi gelé stops, objectifs, ordres et portage de TOUS les
    comptes — un test de graphique PHP qui pourrit et ce sont les protections
    des positions qui cessent d'être honorées. Sans verdicts, le robot ne
    prend simplement aucune décision NOUVELLE ; la tenue, elle, a lieu.
    """
    if not VERDICTS_LOCAL.exists():
        print("verdicts.json absent : TENUE SEULE — stops, objectifs, ordres "
              "et portage restent honorés ; aucune décision nouvelle du robot")
        return {}
    try:
        return json.loads(VERDICTS_LOCAL.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"verdicts.json illisible ({type(exc).__name__}) : TENUE SEULE")
        return {}


def main() -> int:
    publie = charger_verdicts_publies()
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

        gardes_fil: list[tuple[str, bool]] = []
        for fichier in fichiers:
            compte = _telecharger(session, base, fichier)
            if not compte:
                continue
            evenements = tenir_compte(compte)
            evenements += executer_ordres(compte)
            evenements += facturer_portage(compte)
            # SURVEILLANCE DES POSITIONS OUVERTES. La chaîne d'encadrement
            # jugeait l'idée à l'entrée puis ne regardait plus jamais la
            # position — or régime, sauts, concentration et portage bougent
            # pendant la vie du trade. Les gardes sont rejoués ici, dans la
            # même fenêtre nocturne : SIGNALER seulement, jamais agir.
            # Le type de l'erreur est imprimé (leçon du NameError avalé) :
            # une surveillance qui tombe en panne doit se voir.
            try:
                gardes = surveillance.examiner(compte)
            except Exception as exc:
                gardes = []
                print(f"surveillance en panne (non bloquant) : "
                      f"{type(exc).__name__}: {str(exc)[:90]}")
            evenements += gardes
            gardes_fil += [(f"🛡️ compte {compte.get('nom', '?')} — {g}", False)
                           for g in gardes]
            est_robot = compte["nom"] in ROBOTS
            if est_robot:
                v = verdicts_par_robot.get(compte["nom"]) or []
                if v:
                    evenements += decisions_robot(
                        compte, v,
                        respecte_suspension=ROBOTS[compte["nom"]]
                        .get("respecte_suspension", False))
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

    # Les alertes de surveillance rejoignent le fil public du site : elles y
    # côtoient celles du scanner horaire, avec le même contrat (le silence
    # est une information, une panne d'envoi n'empêche pas la tenue).
    if gardes_fil:
        try:
            from marketlab import fil_alertes
            r = fil_alertes.publier(gardes_fil)
            print(f"fil d'alertes : {r['publiees']} garde(s) publiée(s)")
        except Exception as exc:
            print(f"fil d'alertes injoignable (non bloquant) : "
                  f"{type(exc).__name__}: {str(exc)[:80]}")

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
                    "specialite": c.get("libelle"),
                    "respecte_suspension": c.get("respecte_suspension", False)}
                   for n, c in ROBOTS.items()],
        # ÉTAT DU TÉMOIN, publié pour être affiché. Sans cette mention, la page
        # montre deux comptes aux positions rigoureusement identiques et le
        # lecteur conclut — à juste titre — que l'un duplique l'autre. Il n'en
        # est rien : ils décident séparément et aboutissent au même résultat
        # parce qu'il n'y a rien à quoi obéir. Une page qui laisse tirer une
        # conclusion fausse d'une observation juste doit fournir le chaînon
        # manquant, pas compter sur la mémoire du lecteur.
        "temoin": _etat_temoin(),
        "experience": ("Quatre robots, mêmes règles, UNE variable chacun par "
                       "rapport à la référence. « claude » (tous marchés, "
                       "20 séances) est la référence. « claude5 » n'en diffère "
                       "que par l'HORIZON (5 séances). « claudefx » que par "
                       "l'UNIVERS (forex seul). « claudeprudent » que par "
                       "l'OBÉISSANCE au veto de régime : il s'abstient quand "
                       "le site s'abstient, là où les trois autres continuent "
                       "d'appliquer le verdict brut. "
                       "POURQUOI CETTE DERNIÈRE VARIABLE. Le site s'abstient "
                       "désormais dans la plupart des régimes, par prudence. "
                       "Mais les robots sont l'appareil de mesure : les faire "
                       "taire refermerait la boucle qui pourrait un jour "
                       "justifier de lever le silence. Ils continuent donc de "
                       "trader en argent virtuel — où s'abstenir ne protège "
                       "rien — et l'écart avec « claudeprudent » mesurera ce "
                       "que l'abstention aura coûté ou rapporté."),
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
        # Le TYPE de l'erreur est imprimé, pas seulement son message : ce
        # bloc a avalé un NameError pendant des semaines (« rapport_seance »
        # n'était pas importé, « RAPPORT_LOCAL » n'existait pas), et un
        # NameError a un message si court qu'il passait pour une donnée
        # manquante. Un garde-fou qui masque la nature de la panne ne
        # protège que la panne.
        print(f"rapport de séance non produit (non bloquant) : "
              f"{type(exc).__name__}: {str(exc)[:90]}")

    notifier_mouvements(mouvements)
    return 0


def _etat_temoin() -> dict:
    """Le robot témoin est-il actuellement distinguable de la référence ?

    « claudeprudent » ne diffère de « claude » que par l'obéissance au veto de
    régime. Tant qu'aucun régime n'est suspendu, il n'a rien à quoi obéir : les
    deux comptes prennent les mêmes décisions et affichent les mêmes positions.
    C'est voulu, et c'est même verrouillé par un test — mais vu de la page, cela
    ressemble à une duplication, et la conclusion est alors fausse pour une
    observation juste.
    """
    try:
        from marketlab import regimes
        courant = regimes.regime_courant()
        suspendus = regimes.charger_verdict().get("suspendus") or []
    except Exception as exc:
        return {"actif": None,
                "lecture": "État du régime indisponible "
                           f"({type(exc).__name__}) : impossible de dire si le "
                           "témoin se distingue aujourd'hui."}

    etiquette = regimes.ETIQUETTES.get(courant, courant)
    autres = [regimes.ETIQUETTES.get(r, r) for r in suspendus]
    actif = courant in suspendus

    if actif:
        lecture = (
            f"Nous sommes en {etiquette}, où l'avis directionnel est "
            "suspendu : « claudeprudent » s'abstient là où « claude » applique "
            "le verdict brut. C'est à partir de maintenant que l'écart entre "
            "les deux mesure ce que l'abstention coûte ou rapporte.")
    elif autres:
        lecture = (
            f"Nous sommes en {etiquette}, qui n'est pas suspendu : "
            "« claudeprudent » n'a rien à quoi obéir et prend donc exactement "
            "les mêmes décisions que « claude ». Les deux comptes sont "
            "identiques PAR CONSTRUCTION — ils décident séparément et "
            "aboutissent au même résultat, ce n'est pas une duplication. Leur "
            "écart n'apparaîtra qu'en " + " ou en ".join(autres) + ".")
    else:
        lecture = (
            f"Nous sommes en {etiquette}, et aucun régime n'est actuellement "
            "suspendu : « claudeprudent » se comporte exactement comme "
            "« claude ». Il ne s'en distinguera que le jour où une suspension "
            "sera active.")
    return {"actif": actif, "regime": courant, "suspendus": suspendus,
            "lecture": lecture}


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
