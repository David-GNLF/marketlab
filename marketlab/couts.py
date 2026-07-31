"""Ce qu'une idée coûte AVANT de rapporter quoi que ce soit.

LE TROU QUE CE MODULE BOUCHE. Le moteur de décision annonçait des espérances
BRUTES : ni `levels.py`, ni `position.py`, ni `decision.py`, ni `edge.py` ne
contenaient la moindre mention de coût. Le seul endroit du projet où un spread
existait était la simulation du robot. Autrement dit, l'outil affichait
« espérance +2,3 % » à un utilisateur qui, lui, paie le spread deux fois et le
financement chaque nuit.

CE QUE PERSONNE NE MONTRE, ET POURQUOI ÇA COMPTE. Sur le marché des outils
d'aide à la décision, les espérances affichées sont presque toujours brutes.
C'est confortable : le coût transforme une bonne partie des idées « favorables »
en évidences négatives. Or c'est précisément le chiffre qui décide — une
stratégie dont le pouvoir prédictif est nul ne perd pas zéro, elle perd
exactement ses frais.

LE POINT QUE LE LEVIER REND CONTRE-INTUITIF. Le levier multiplie le gain, la
perte ET LE COÛT. À effet 5, une position engage cinq fois la mise, donc paie
cinq fois le spread rapporté à cette mise, et emprunte quatre cinquièmes du
notionnel. Le seuil de rentabilité exprimé EN POURCENTAGE DE LA MISE grimpe
donc avec le levier, alors que beaucoup le croient neutre. C'est la raison pour
laquelle ce module renvoie les deux lectures : ce que l'ACTIF doit parcourir,
et ce que ça représente sur la MISE.

Les valeurs sont des ordres de grandeur de courtage particulier, pas les tarifs
d'un courtier précis : elles servent à savoir si une idée a une chance, pas à
facturer.
"""

from __future__ import annotations

# Spread aller simple, en pourcentage du cours. Un aller-retour coûte le
# double. Ordres de grandeur constatés chez les courtiers particuliers.
SPREAD_PCT = {
    "Forex": 0.015,      # ~1,5 pip sur une paire majeure
    "Indices": 0.020,
    "Actions": 0.040,
    "Matières": 0.070,
    "Crypto": 0.075,
}
SPREAD_DEFAUT = 0.05

# Taux annuel sur la part EMPRUNTÉE. Aligné sur les frais de portage déjà
# appliqués par le robot : deux chiffres différents pour la même réalité
# finiraient par se contredire.
TAUX_FINANCEMENT_AN = 6.0

# Une séance dure plus qu'un jour civil : 5 séances par semaine en couvrent 7.
# Le financement se paie tous les jours, y compris le week-end.
JOURS_PAR_SEANCE = 7 / 5

# Leviers de référence, repris du robot pour la même raison.
LEVIERS = {"Forex": 5, "Matières": 3, "Actions": 2, "Crypto": 2, "Indices": 2}


def classe_actif(symbole: str) -> str:
    """Classe d'un symbole, selon la même convention que le reste du projet."""
    if symbole.endswith("=X"):
        return "Forex"
    if symbole.endswith("=F"):
        return "Matières"
    if symbole.endswith("USDT"):
        return "Crypto"
    if symbole.startswith("^"):
        return "Indices"
    return "Actions"


def levier_defaut(symbole: str) -> int:
    return LEVIERS.get(classe_actif(symbole), 2)


def couts(symbole: str, horizon: int = 20, levier: float | None = None,
          spread_pct: float | None = None) -> dict:
    """Coût complet d'un aller-retour, et seuil de rentabilité qui en découle.

    Renvoie deux lectures du MÊME coût :

    * `seuil_actif_%`  — ce que le cours doit parcourir pour rentrer dans ses
      frais. C'est ce qu'on compare à un objectif ou à une amplitude prévue.
    * `seuil_mise_%`   — ce que ces frais représentent sur la somme engagée.
      C'est ce qu'on ressent sur son compte.

    Le second vaut le premier multiplié par le levier : c'est exactement ce que
    le levier fait au coût, et ce qu'on oublie en le regardant comme un simple
    multiplicateur de gain.
    """
    classe = classe_actif(symbole)
    levier = float(levier if levier is not None else levier_defaut(symbole))
    levier = max(levier, 1.0)
    spread = float(spread_pct if spread_pct is not None
                   else SPREAD_PCT.get(classe, SPREAD_DEFAUT))

    # Aller-retour : on paie le spread à l'entrée ET à la sortie.
    cout_spread_actif = 2 * spread

    # Financement de la part empruntée, rapportée au NOTIONNEL : sur un levier
    # L, la part empruntée vaut (L−1)/L du notionnel.
    jours = max(horizon, 0) * JOURS_PAR_SEANCE
    part_empruntee = (levier - 1) / levier
    cout_financement_actif = (TAUX_FINANCEMENT_AN * jours / 365) * part_empruntee

    seuil_actif = cout_spread_actif + cout_financement_actif
    return {
        "classe": classe,
        "levier": round(levier, 2),
        "horizon": horizon,
        "spread_aller_%": round(spread, 4),
        "cout_spread_%": round(cout_spread_actif, 4),
        "cout_financement_%": round(cout_financement_actif, 4),
        "seuil_actif_%": round(seuil_actif, 4),
        "seuil_mise_%": round(seuil_actif * levier, 3),
        "part_financement_%": (round(cout_financement_actif / seuil_actif * 100, 1)
                               if seuil_actif > 0 else 0.0),
    }


def net(esperance_brute_pct: float, symbole: str, horizon: int = 20,
        levier: float | None = None) -> dict:
    """Espérance NETTE de frais, et ce que le coût en a mangé.

    `esperance_brute_pct` s'exprime en pourcentage du COURS, comme
    `levels.plan()["esperance_%"]`.
    """
    c = couts(symbole, horizon=horizon, levier=levier)
    nette = esperance_brute_pct - c["seuil_actif_%"]
    return {
        **c,
        "esperance_brute_%": round(float(esperance_brute_pct), 4),
        "esperance_nette_%": round(nette, 4),
        "survit_aux_frais": bool(nette > 0),
        "lecture": _lecture(esperance_brute_pct, nette, c),
    }


def _lecture(brute: float, nette: float, c: dict) -> str:
    seuil = c["seuil_actif_%"]
    if brute <= 0:
        return (f"L'idée est déjà négative avant frais. Les {seuil:.2f} % "
                f"d'aller-retour et de portage ne font que creuser l'écart.")
    if nette <= 0:
        return (f"Espérance ANNULÉE par les frais : {brute:.2f} % attendus "
                f"pour {seuil:.2f} % de coût. À effet {c['levier']:.0f}, cela "
                f"représente {c['seuil_mise_%']:.2f} % de la mise à récupérer "
                f"avant le premier centime de gain.")
    part = seuil / brute * 100
    if part > 50:
        return (f"Les frais absorbent {part:.0f} % de l'espérance : il reste "
                f"{nette:.2f} % nets sur {brute:.2f} % attendus. Une marge "
                f"aussi mince ne survit pas à une exécution un peu moins bonne "
                f"que prévu.")
    return (f"L'idée survit aux frais : {nette:.2f} % nets sur {brute:.2f} % "
            f"attendus, {seuil:.2f} % de coût ({c['part_financement_%']:.0f} % "
            f"venant du portage sur {c['horizon']} séances).")
