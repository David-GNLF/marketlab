"""Le banc d'essai du côté VENTE : voir venir le jour où vendre paiera.

LA DÉCISION QUE CE MODULE PRÉPARE. Les robots n'ouvrent que des achats, et
c'est une règle MESURÉE : sur 2 020 verdicts mûrs, les avis Défavorable ont
précédé des hausses de +2,10 % en moyenne — pire signal vendeur possible.
Mais une règle mesurée hier n'est pas une loi éternelle : si le marché change
et qu'un contexte de vente se met à payer, il faut le voir VENIR, pas le
découvrir en subissant. « Pas de short tant que le bilan ne le justifie
pas » exige qu'un bilan du côté vente EXISTE.

CE QUE FAIT LE BANC. Chaque nuit, pour chaque titre que l'outil juge
vendable (avis Défavorable, ou note franchement négative), un plan de VENTE
complet est construit par le même moteur que les achats
(`levels.plan(sens="vente")` : stop au-dessus, objectif en dessous,
espérance et coûts du short) et consigné — jamais exécuté. À l'échéance, le
trade hypothétique est rejoué sur les cours réels et jugé NET des coûts
connus au moment de la décision. Le jour où les ventes mûres gagnent
durablement, la sonde le dira en tête de console — et la règle du robot
tombera sur preuve, comme elle a été posée sur preuve.

Même discipline que le journal de la chaîne : relevé immuable, premier
écrit gagne, aucune conclusion sous dix verdicts mûrs.
"""

from __future__ import annotations

import pandas as pd

from marketlab import config

JOURNAL_PATH = config.DATA_DIR / "banc_ventes.csv"
COLONNES = ["date", "symbole", "horizon", "note", "avis", "entree", "stop",
            "objectif", "esperance_nette_%", "cout_actif_%"]

# Un candidat vendeur : l'avis le dit, ou la note penche franchement. Le seuil
# est plus bas que celui des achats (−20 contre +30) : on n'exécute rien, on
# MESURE — et un banc qui ne journalise presque jamais ne prouvera jamais rien.
NOTE_MAX_CANDIDAT = -20.0

MURS_MIN_POUR_CONCLURE = 10


def candidats(dossiers: list[dict]) -> list[dict]:
    """Les dossiers dont l'outil dirait « à vendre » aujourd'hui."""
    retenus = []
    for d in dossiers:
        if "erreur" in d or not d.get("symbole"):
            continue
        note = float(d.get("note_globale") or 0)
        if d.get("avis") == "Défavorable" or note <= NOTE_MAX_CANDIDAT:
            retenus.append(d)
    return retenus


def journaliser(dossiers: list[dict]) -> int:
    """Construit et consigne le plan de vente de chaque candidat du jour.

    Le plan est calculé ICI (le moteur de décision ne produit que des plans
    d'achat) : même levels.plan, sens inversé, mêmes coûts. Une ligne par
    (date, symbole, horizon), le premier écrit gagne.
    """
    from marketlab import levels
    lignes = []
    for d in candidats(dossiers):
        try:
            plan = levels.plan(d["symbole"], sens="vente",
                               horizon=int(d["horizon"]))
        except Exception:
            continue                    # un titre sans plan n'entre pas au banc
        frais = plan.get("couts") or {}
        lignes.append({
            "date": d["date"], "symbole": d["symbole"],
            "horizon": int(d["horizon"]),
            "note": d.get("note_globale"), "avis": d.get("avis"),
            "entree": plan.get("entree"), "stop": plan.get("stop"),
            "objectif": plan.get("objectif"),
            "esperance_nette_%": plan.get("esperance_nette_%"),
            "cout_actif_%": frais.get("seuil_actif_%"),
        })
    if not lignes:
        return 0
    nouveau = pd.DataFrame(lignes, columns=COLONNES)
    if JOURNAL_PATH.exists():
        journal = pd.concat([pd.read_csv(JOURNAL_PATH), nouveau],
                            ignore_index=True)
        journal = journal.drop_duplicates(subset=["date", "symbole", "horizon"],
                                          keep="first")
    else:
        journal = nouveau
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    journal.sort_values(["date", "symbole", "horizon"]).to_csv(
        JOURNAL_PATH, index=False)
    return len(lignes)


def _rejouer_vente(df: pd.DataFrame, date: str, entree: float, stop, objectif,
                   horizon: int) -> dict | None:
    """Le short hypothétique sur les cours réels. None s'il n'est pas mûr.

    Symétrique de la tenue des comptes : pour un VENDEUR, le stop est
    AU-DESSUS (touché par le haut de séance) et l'objectif EN DESSOUS (touché
    par le bas). Si les deux sont touchés le même jour, le STOP est réputé
    premier — même hypothèse prudente que partout. Le rendement est celui du
    PARI : (entrée − sortie) / entrée.
    """
    if not entree or entree <= 0:
        return None
    apres = df[df.index > pd.Timestamp(str(date)[:10])]
    if len(apres) < horizon:
        return None
    fenetre = apres.iloc[:horizon]
    sortie, issue = float(fenetre["close"].iloc[-1]), "echeance"
    for _, jour in fenetre.iterrows():
        if stop and float(jour["high"]) >= float(stop):
            sortie, issue = float(stop), "stop"
            break
        if objectif and float(jour["low"]) <= float(objectif):
            sortie, issue = float(objectif), "objectif"
            break
    return {"rendement_pari_%": (float(entree) - sortie) / float(entree) * 100,
            "issue": issue}


def bilan() -> dict:
    """Les ventes hypothétiques mûres, nettes des coûts — le bilan qui manquait.

    C'est LA sonde d'anticipation : tant qu'elle dit « le refus de vendre
    reste justifié », la règle des robots tient ; le jour où elle dit
    « SIGNAL », le côté vente mérite d'être rejugé — avant d'être surpris.
    """
    if not JOURNAL_PATH.exists():
        return {"suivis": 0, "murs": 0,
                "lecture": "Banc vide : il se remplit à chaque publication, "
                           "au rythme des avis vendeurs de l'outil."}
    journal = pd.read_csv(JOURNAL_PATH)
    if journal.empty:
        return {"suivis": 0, "murs": 0, "lecture": "Banc vide."}

    from marketlab.data import get_ohlcv
    cours: dict[str, pd.DataFrame | None] = {}
    resultats = []
    inverifiables = 0
    for _, ligne in journal.iterrows():
        sym = str(ligne["symbole"])
        if sym not in cours:
            try:
                # même lookback que la génération : même cache, zéro réseau
                # en plus la nuit où le bilan tourne
                cours[sym] = get_ohlcv(sym, lookback_days=1825)
            except Exception:
                cours[sym] = None
        if cours[sym] is None:
            inverifiables += 1
            continue
        r = _rejouer_vente(cours[sym], ligne["date"], ligne["entree"],
                           ligne["stop"], ligne["objectif"],
                           int(ligne["horizon"]))
        if r is None:
            continue
        cout = float(ligne["cout_actif_%"]) \
            if pd.notna(ligne["cout_actif_%"]) else 0.0
        resultats.append({"net": r["rendement_pari_%"] - cout,
                          "issue": r["issue"]})

    murs = len(resultats)
    sortie = {"suivis": int(len(journal)), "murs": murs,
              "en_attente": int(len(journal)) - murs - inverifiables,
              "inverifiables": inverifiables}
    if murs:
        nets = pd.Series([r["net"] for r in resultats])
        issues: dict[str, int] = {}
        for r in resultats:
            issues[r["issue"]] = issues.get(r["issue"], 0) + 1
        sortie["rendement_net_moyen_%"] = round(float(nets.mean()), 2)
        sortie["rendement_net_median_%"] = round(float(nets.median()), 2)
        sortie["part_gagnante_%"] = round(float((nets > 0).mean() * 100), 1)
        sortie["issues"] = issues

    if murs < MURS_MIN_POUR_CONCLURE:
        sortie["lecture"] = (f"Trop tôt pour juger le côté vente : {murs} "
                             f"short(s) hypothétique(s) mûr(s) sur "
                             f"{MURS_MIN_POUR_CONCLURE} requis.")
    elif sortie["rendement_net_moyen_%"] > 0:
        sortie["lecture"] = (
            f"SIGNAL : les ventes hypothétiques mûres rendent "
            f"{sortie['rendement_net_moyen_%']:+.2f} % net en moyenne "
            f"({sortie['part_gagnante_%']:.0f} % gagnantes sur {murs}). Si "
            f"l'avantage persiste en grandissant, le côté vente mérite d'être "
            f"rejugé — c'est exactement ce que ce banc existe pour voir venir.")
    else:
        sortie["lecture"] = (
            f"Le refus de vendre reste justifié : les shorts hypothétiques "
            f"mûrs rendent {sortie['rendement_net_moyen_%']:+.2f} % net en "
            f"moyenne sur {murs} — la règle « long uniquement » des robots "
            f"tient toujours sur preuve.")
    return sortie
