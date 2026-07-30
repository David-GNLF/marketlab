"""Lecture d'ENSEMBLE des verdicts : par classe d'actif, par devise, et par
composante — pour répondre à « sur quoi ce raisonnement est-il basé ? ».

TROIS QUESTIONS QUE LA LISTE DES VERDICTS NE SAIT PAS TRAITER.

1. « Où est le vent ? » Trente-six cartes triées ne disent pas si les actions
   sont globalement bien orientées et les matières mal. La synthèse par classe
   répond avant qu'on descende au titre.

2. « Quel est le VRAI pari ? » Trois paires favorables — EUR/USD, GBP/USD,
   AUD/USD — ne sont pas trois idées : c'est UNE idée, vendre le dollar,
   comptée trois fois. Décomposer chaque paire en ses deux devises fait
   apparaître le pari sous-jacent, que la liste par symbole masque
   complètement. C'est le pendant, côté analyse, de ce que
   `risque_portefeuille` corrige côté exécution.

3. « D'où sort ce chiffre ? » Une note de +42 ne dit rien de sa fabrication.
   La décomposition en contributions montre ce qui pousse, ce qui retient, et
   dans quelle proportion — y compris quand deux composantes se contredisent,
   ce que la note finale efface par construction.

CE MODULE N'AJOUTE AUCUN SIGNAL. Il réorganise ce que le moteur de décision a
déjà produit. Aucune pondération nouvelle, aucune prévision : seulement des
regroupements et une arithmétique explicite. C'est délibéré — sur ce projet,
chaque brique prédictive ajoutée a échoué à son test, alors que rendre lisible
l'existant n'a jamais rien coûté.
"""

from __future__ import annotations

import numpy as np

from marketlab import surprise

# Libellés lisibles des composantes, et surtout ce sur quoi CHACUNE se fonde.
# C'est la réponse à « sur quoi ce raisonnement est-il basé », et elle doit
# tenir en une phrase qu'un débutant comprend.
FONDEMENTS = {
    "technique": ("Analyse technique",
                  "La forme récente du cours : tendance, élan, position par "
                  "rapport aux moyennes. Décrit ce qui vient de se passer."),
    "prevision": ("Prévision simulée",
                  "Des milliers de futurs rejoués à partir des variations "
                  "passées. Donne une fourchette, jamais un prix."),
    "analogues": ("Configurations passées",
                  "Les moments où le cours ressemblait le plus à aujourd'hui, "
                  "et ce qui a suivi ces fois-là."),
    "fondamentaux": ("Fondamentaux",
                     "Les comptes de l'entreprise : valorisation, qualité, "
                     "croissance, solidité. Sans objet hors actions."),
    "moteurs": ("Moteurs économiques",
                "Ce qui fait bouger cette classe d'actif : écart de taux pour "
                "une devise, taux réels et dollar pour l'or, structure des "
                "contrats à terme pour une matière première."),
    "saisonnalite": ("Saisonnalité",
                     "Les régularités de calendrier qui survivent à un test "
                     "statistique correct — la plupart n'y survivent pas."),
    "sentiment": ("Sentiment",
                  "Le ton des actualités récentes. Indicatif : un lexique ne "
                  "comprend pas l'ironie ni le contexte."),
}

# Devises suivies, reprises de l'indice de surprise pour n'avoir qu'une liste.
DEVISES = surprise.DEVISES


# ---------------------------------------------------------------------------
# D'où sort la note
# ---------------------------------------------------------------------------

def contributions(dossier: dict) -> dict:
    """Décompose la note globale en apports de chaque composante.

    La note publiée est une moyenne pondérée. La contribution d'une composante
    vaut donc `note × poids / somme des poids` : elle s'exprime dans la même
    unité que la note finale, et la somme des contributions redonne exactement
    cette note. C'est vérifiable, pas illustratif.

    `part_%` mesure le POIDS RÉEL dans l'explication, en valeur absolue : une
    composante à +30 et une à −30 pèsent autant l'une que l'autre, même si
    elles s'annulent dans le total.
    """
    comps = dossier.get("composantes") or []
    total_poids = sum(float(c.get("poids", 0)) for c in comps)
    if total_poids <= 0:
        return {"lignes": [], "note_reconstituee": 0.0, "desaccord": False}

    lignes = []
    for c in comps:
        nom = c.get("nom", "?")
        poids = float(c.get("poids", 0))
        note = float(c.get("note", 0))
        libelle, fondement = FONDEMENTS.get(nom, (nom.capitalize(), ""))
        lignes.append({
            "nom": nom, "libelle": libelle, "fondement": fondement,
            "note": round(note, 1),
            "poids_%": round(poids / total_poids * 100, 1),
            "contribution": round(note * poids / total_poids, 1),
            "sens": "hausse" if note > 0 else "baisse" if note < 0 else "neutre",
            "raisons": c.get("raisons") or [],
        })

    somme_abs = sum(abs(l["contribution"]) for l in lignes) or 1.0
    for l in lignes:
        l["part_%"] = round(abs(l["contribution"]) / somme_abs * 100, 1)
    lignes.sort(key=lambda l: -abs(l["contribution"]))

    positives = [l for l in lignes if l["contribution"] > 0]
    negatives = [l for l in lignes if l["contribution"] < 0]
    return {
        "lignes": lignes,
        "note_reconstituee": round(sum(l["contribution"] for l in lignes), 1),
        # Un désaccord n'est pas un défaut : c'est une information que la note
        # finale, seule, fait disparaître.
        "desaccord": bool(positives and negatives),
        # Un moteur POUSSE, un frein RETIENT. Prendre le plus gros contributeur
        # en valeur absolue faisait désigner la même composante comme moteur et
        # comme frein quand elle était négative — constaté sur MSFT.
        "moteur_principal": (max(positives, key=lambda l: l["contribution"])["libelle"]
                             if positives else None),
        "frein_principal": (min(negatives, key=lambda l: l["contribution"])["libelle"]
                            if negatives else None),
    }


# ---------------------------------------------------------------------------
# Par classe d'actif
# ---------------------------------------------------------------------------

def par_categorie(dossiers: list[dict]) -> list[dict]:
    """Orientation d'ensemble de chaque classe d'actif."""
    groupes: dict[str, list[dict]] = {}
    for d in dossiers:
        groupes.setdefault(d.get("classe") or "Autres", []).append(d)

    sortie = []
    for classe, liste in groupes.items():
        notes = [float(d.get("note_globale", 0)) for d in liste]
        avis = {}
        for d in liste:
            a = d.get("avis", "?")
            avis[a] = avis.get(a, 0) + 1
        moyenne = float(np.mean(notes)) if notes else 0.0
        meilleur = max(liste, key=lambda d: d.get("note_globale", 0))
        pire = min(liste, key=lambda d: d.get("note_globale", 0))
        sortie.append({
            "classe": classe,
            "n": len(liste),
            "note_moyenne": round(moyenne, 1),
            "dispersion": round(float(np.std(notes)), 1) if len(notes) > 1 else 0.0,
            "avis": avis,
            "meilleur": {"symbole": meilleur.get("symbole"),
                         "note": meilleur.get("note_globale")},
            "pire": {"symbole": pire.get("symbole"),
                     "note": pire.get("note_globale")},
            "lecture": _lecture_categorie(classe, moyenne, notes),
        })
    sortie.sort(key=lambda x: -x["note_moyenne"])
    return sortie


def _lecture_categorie(classe: str, moyenne: float, notes: list[float]) -> str:
    dispersion = float(np.std(notes)) if len(notes) > 1 else 0.0
    if dispersion > 35:
        return (f"Pas d'orientation commune : les {classe.lower()} se "
                f"distinguent beaucoup les unes des autres (écart type "
                f"{dispersion:.0f}). C'est un cas où le choix du titre compte "
                f"plus que la classe.")
    if moyenne >= 25:
        return f"Orientation favorable partagée par l'ensemble des {classe.lower()}."
    if moyenne <= -25:
        return f"Orientation défavorable partagée par l'ensemble des {classe.lower()}."
    return (f"Pas de direction marquée sur les {classe.lower()} : les avis se "
            f"compensent.")


# ---------------------------------------------------------------------------
# Par devise — le vrai pari derrière les paires
# ---------------------------------------------------------------------------

def par_devise(dossiers: list[dict]) -> dict:
    """Force de chaque devise, déduite des verdicts sur les paires.

    Une paire oppose deux devises : un avis favorable sur EUR/USD est un avis
    favorable sur l'euro ET défavorable sur le dollar, à parts égales. En
    reportant chaque note sur ses deux devises, on voit apparaître ce que la
    liste par symbole cache — que trois idées n'en font parfois qu'une.
    """
    scores: dict[str, list[float]] = {d: [] for d in DEVISES}
    paires = []
    for d in dossiers:
        couple = surprise.devises_de(d.get("symbole", ""))
        if couple is None:
            continue
        base, contre = couple
        note = float(d.get("note_globale", 0))
        scores[base].append(note)
        scores[contre].append(-note)
        paires.append({"symbole": d.get("symbole"), "note": round(note, 1),
                       "base": base, "contre": contre})

    forces = []
    for devise, notes in scores.items():
        if not notes:
            continue
        forces.append({
            "devise": devise,
            "force": round(float(np.mean(notes)), 1),
            "n_paires": len(notes),
            # Un avis répété sur plusieurs paires est plus solide qu'un avis
            # isolé : le second peut tenir à la seconde devise de la paire.
            "accord": round(float(np.mean(np.sign(notes))), 2) if notes else 0.0,
        })
    forces.sort(key=lambda x: -x["force"])

    return {"forces": forces, "paires": paires,
            "lecture": _lecture_devises(forces)}


def _lecture_devises(forces: list[dict]) -> str:
    """Lecture honnête : seule une devise VUE SUR PLUSIEURS PAIRES vaut d'être
    commentée.

    Dans un univers où toutes les paires suivies contiennent le dollar, chaque
    autre devise n'apparaît qu'une fois : sa « force » n'est alors que le
    miroir d'un seul verdict, pas une opinion recoupée. La première version
    classait tout le monde ensemble et désignait la mieux placée — souvent une
    devise à une seule paire, donc un chiffre sans robustesse.
    """
    if not forces:
        return "Aucune paire de devises suivie."
    recoupees = [f for f in forces if f["n_paires"] >= 2]
    if not recoupees:
        return ("Chaque devise n'apparaît que dans une paire : leurs forces ne "
                "sont que le reflet d'un verdict isolé, pas une opinion "
                "recoupée. Rien à en conclure au niveau des devises.")

    dominante = max(recoupees, key=lambda f: abs(f["force"]))
    sens = "HAUSSE" if dominante["force"] > 0 else "BAISSE"
    part = abs(dominante["accord"])
    if abs(dominante["force"]) < 15:
        return (f"Le {dominante['devise']}, seule devise recoupée sur "
                f"{dominante['n_paires']} paires, ne ressort pas nettement : "
                f"pas de pari commun identifiable.")
    return (f"Le pari commun est la {sens} du {dominante['devise']} : il est "
            f"impliqué dans {dominante['n_paires']} paires suivies, et "
            f"{part * 100:.0f} % d'entre elles vont dans le même sens. "
            f"Prendre plusieurs de ces paires revient à répéter cette idée, "
            f"pas à se diversifier — c'est ce que le contrôle de "
            f"concentration corrige à l'exécution.")


def bloc(dossiers: list[dict]) -> dict:
    """Synthèse complète, prête à publier."""
    dossiers = dossiers or []
    return {
        "par_categorie": par_categorie(dossiers),
        "par_devise": par_devise(dossiers),
        "fondements": {n: {"libelle": l, "explication": e}
                       for n, (l, e) in FONDEMENTS.items()},
        "n_dossiers": len(dossiers),
    }
