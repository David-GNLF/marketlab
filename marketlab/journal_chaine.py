"""Le journal des verdicts de la CHAÎNE : rendre le filtre falsifiable.

LE TROU QUE CE MODULE COMBLE. La chaîne de dimensionnement écarte trois idées
sur quatre — aux frais, au régime, à la mise minimale — et personne ne mesure
si elle a RAISON d'écarter. Le journal des décisions existant consigne la
note et l'avis ; il ne dit rien de ce que la chaîne a fait de l'idée. Or un
filtre qu'on ne juge jamais est une croyance, pas un garde-fou : si les
écartées battent les retenues une fois leurs frais comptés, la chaîne détruit
de la valeur et il faut le savoir.

LE PRINCIPE, le même que le banc d'essai IV contre EWMA : consigner
aujourd'hui, juger à l'échéance, ne rien conclure avant. Chaque évaluation
est archivée (verdict, étape fatale, plan, coût CONNU au moment de la
décision) ; quand l'horizon est écoulé, le trade hypothétique est rejoué sur
les cours réels — entrée au prix du plan, stop et objectif confrontés aux
extrêmes de chaque séance, sortie à l'échéance sinon — et son rendement NET
du coût enregistré est comparé entre retenues et écartées.

PAS DE RÉTRO-REMPLISSAGE, et c'est voulu : reconstituer les verdicts passés
de la chaîne exigerait les coûts, régimes et parts de saut d'alors — les
recalculer aujourd'hui serait regarder le passé avec les yeux du présent. Le
journal commence le jour où il existe ; l'horizon court (5 séances) donne ses
premiers verdicts mûrs en une semaine.

Relevé IMMUABLE (union, premier écrit gagne), comme tous les relevés du
projet : une ligne consignée ne se réécrit jamais.
"""

from __future__ import annotations

import pandas as pd

from marketlab import config

JOURNAL_PATH = config.DATA_DIR / "journal_chaine.csv"
COLONNES = ["date", "symbole", "horizon", "retenue", "etape_fatale", "mise",
            "entree", "stop", "objectif", "esperance_nette_%", "cout_actif_%"]

# En dessous, on affiche les chiffres mais on refuse de conclure : dix
# verdicts mûrs ne départagent rien.
MURS_MIN_POUR_CONCLURE = 10


def _etape_fatale(dim: dict) -> str:
    """Le maillon qui a tué l'idée, en un mot — vide si elle est passée.

    Déduit des étapes ÉCRITES par la chaîne : la classification suit le texte
    source plutôt qu'une liste recopiée qui divergerait en silence.
    """
    if dim.get("retenue"):
        return ""
    texte = " ".join(str(e) for e in dim.get("etapes", []))
    if "ne survit pas aux frais" in texte:
        return "frais"
    if "avis directionnel suspendu" in texte:
        return "regime"
    if "distance au stop inconnue" in texte:
        return "stop_inconnu"
    if "mise résiduelle sous" in texte:
        return "mise_min"
    if "aucune équité ou aucun plan" in texte:
        return "sans_plan"
    return "autre"


def journaliser(dossiers: list[dict]) -> int:
    """Consigne le verdict de la chaîne pour chaque dossier portant un plan.

    Une ligne par (date, symbole, horizon) ; le premier écrit gagne — le
    verdict du jour ne se réécrit pas si la génération repasse.
    """
    lignes = []
    for d in dossiers:
        if "erreur" in d or not d.get("plan"):
            continue
        dim = d.get("dimensionnement") or {}
        # La chaîne n'a pas tourné (dossier d'avant son branchement, ou en
        # erreur) : rien à juger. Consigner « écartée » un dossier que la
        # chaîne n'a jamais vu fausserait la comparaison qu'on veut honnête.
        if "erreur" in dim or "retenue" not in dim:
            continue
        plan = d["plan"]
        frais = plan.get("couts") or {}
        lignes.append({
            "date": d["date"], "symbole": d["symbole"],
            "horizon": int(d["horizon"]),
            "retenue": int(bool(dim.get("retenue"))),
            "etape_fatale": _etape_fatale(dim),
            "mise": float(dim.get("mise") or 0),
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


def _rejouer(df: pd.DataFrame, date: str, entree: float, stop, objectif,
             horizon: int) -> dict | None:
    """Le trade hypothétique, rejoué sur les cours réels. None s'il n'est pas mûr.

    Mêmes règles que la tenue des comptes : stop et objectif confrontés aux
    extrêmes de chaque séance ; si les deux sont touchés le même jour, le STOP
    est réputé premier (hypothèse prudente) ; ni l'un ni l'autre → sortie au
    dernier cours de l'horizon. Plans à l'ACHAT uniquement — c'est le seul
    sens que `levels.plan` produit pour la chaîne.
    """
    if not entree or entree <= 0:
        return None
    apres = df[df.index > pd.Timestamp(str(date)[:10])]
    if len(apres) < horizon:
        return None                    # l'échéance n'est pas encore passée
    fenetre = apres.iloc[:horizon]
    sortie, issue = float(fenetre["close"].iloc[-1]), "echeance"
    for _, jour in fenetre.iterrows():
        if stop and float(jour["low"]) <= float(stop):
            sortie, issue = float(stop), "stop"
            break
        if objectif and float(jour["high"]) >= float(objectif):
            sortie, issue = float(objectif), "objectif"
            break
    return {"rendement_brut_%": (sortie / float(entree) - 1) * 100,
            "issue": issue}


def _stats(groupe: list[dict]) -> dict:
    nets = [g["net"] for g in groupe]
    issues: dict[str, int] = {}
    for g in groupe:
        issues[g["issue"]] = issues.get(g["issue"], 0) + 1
    s = pd.Series(nets)
    return {"n": len(groupe),
            "rendement_net_moyen_%": round(float(s.mean()), 2),
            "rendement_net_median_%": round(float(s.median()), 2),
            "part_positive_%": round(float((s > 0).mean() * 100), 1),
            "issues": issues}


def bilan(horizon: int | None = None) -> dict:
    """Retenues contre écartées, net des coûts connus au moment de décider.

    Ne conclut RIEN sous MURS_MIN_POUR_CONCLURE verdicts mûrs : dix trades ne
    départagent pas un filtre, et un tableau de bord qui conclut trop tôt
    fait pire que se taire.
    """
    if not JOURNAL_PATH.exists():
        return {"suivis": 0, "murs": 0,
                "lecture": "Journal absent : il se remplit à chaque "
                           "publication, les premiers verdicts mûrs arrivent "
                           "avec l'horizon court (5 séances)."}
    journal = pd.read_csv(JOURNAL_PATH)
    if horizon is not None:
        journal = journal[journal["horizon"] == int(horizon)]
    if journal.empty:
        return {"suivis": 0, "murs": 0, "lecture": "Journal vide."}

    from marketlab.data import get_ohlcv
    cours: dict[str, pd.DataFrame | None] = {}
    groupes: dict[str, list[dict]] = {}
    murs = invérifiables = 0
    for _, ligne in journal.iterrows():
        sym = str(ligne["symbole"])
        if sym not in cours:
            try:
                # 1825 jours : le MÊME appel que la génération des dossiers,
                # donc le même cache — le rejeu ne coûte aucun aller réseau
                # de plus dans la nuit où il tourne.
                cours[sym] = get_ohlcv(sym, lookback_days=1825)
            except Exception:
                cours[sym] = None
        if cours[sym] is None:
            invérifiables += 1
            continue
        r = _rejouer(cours[sym], ligne["date"], ligne["entree"],
                     ligne["stop"], ligne["objectif"], int(ligne["horizon"]))
        if r is None:
            continue
        murs += 1
        cout = float(ligne["cout_actif_%"]) \
            if pd.notna(ligne["cout_actif_%"]) else 0.0
        net = r["rendement_brut_%"] - cout
        cle = "retenues" if int(ligne["retenue"]) else \
            f"ecartees_{ligne['etape_fatale'] or 'autre'}"
        groupes.setdefault(cle, []).append({"net": net, "issue": r["issue"]})

    resultat = {
        "suivis": int(len(journal)), "murs": murs,
        "en_attente": int(len(journal)) - murs - invérifiables,
        "inverifiables": invérifiables,
        "par_groupe": {cle: _stats(g) for cle, g in sorted(groupes.items())},
    }

    if murs < MURS_MIN_POUR_CONCLURE:
        resultat["lecture"] = (
            f"Trop tôt pour juger le filtre : {murs} verdict(s) mûr(s) sur "
            f"{MURS_MIN_POUR_CONCLURE} requis. Les chiffres s'affichent, la "
            f"conclusion attend.")
        return resultat

    retenues = groupes.get("retenues") or []
    ecartees = [g for cle, gr in groupes.items()
                if cle != "retenues" for g in gr]
    if not retenues or not ecartees:
        resultat["lecture"] = ("Un seul des deux camps a des verdicts mûrs : "
                               "rien à comparer encore.")
        return resultat
    net_r = float(pd.Series([g["net"] for g in retenues]).mean())
    net_e = float(pd.Series([g["net"] for g in ecartees]).mean())
    if net_e < net_r:
        resultat["lecture"] = (
            f"Le filtre gagne sa vie : les idées écartées auraient rendu "
            f"{net_e:+.2f} % net en moyenne, contre {net_r:+.2f} % pour les "
            f"retenues ({len(ecartees)} écartées, {len(retenues)} retenues "
            f"mûres).")
    else:
        resultat["lecture"] = (
            f"ATTENTION : les idées écartées ({net_e:+.2f} % net en moyenne) "
            f"font MIEUX que les retenues ({net_r:+.2f} %) sur "
            f"{len(ecartees)} + {len(retenues)} verdicts mûrs. Si l'écart "
            f"persiste en grandissant, le filtre détruit de la valeur — c'est "
            f"exactement ce que ce journal existe pour détecter.")
    return resultat
