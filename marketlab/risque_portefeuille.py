"""Risque d'ENSEMBLE d'un portefeuille : quatre positions font-elles un seul pari ?

LE DÉFAUT QUE CE MODULE CORRIGE. Le robot dimensionne chaque position
isolément — 5 % de l'équité, quatre lignes au maximum — sans jamais regarder si
ces lignes bougent ensemble. Or elles le peuvent : le 17/06/2026, les plus
fortes secousses relevées ont touché EURUSD, AUDUSD, GBPUSD, USDCHF ET l'or le
même jour. C'était un choc du dollar, un seul. Quatre positions parmi celles-là,
ce ne sont pas quatre paris à 5 %, c'est un pari à 20 % qui s'ignore.

POURQUOI LES CORRÉLATIONS DE STRESS, ET NON LA MOYENNE. `correlations.py` a
mesuré sur ce projet que la corrélation moyenne des actions passe de 0,25 en
marché calme à 0,56 quand il se tend. Dimensionner sur la moyenne revient donc
à se croire diversifié précisément les jours où on ne l'est plus — c'est-à-dire
les seuls jours où la question se pose. Ce module calcule les corrélations sur
le TIERS le plus agité des séances. Il sera trop prudent en marché calme, et
c'est le sens dans lequel on veut se tromper.

LE SENS COMPTE. Être acheteur d'EUR/USD et acheteur d'USD/CHF, ce sont deux
paris OPPOSÉS sur le dollar, alors que les deux cours sont corrélés
négativement. La corrélation est donc signée par le sens des positions avant
d'être agrégée : c'est le pari qui compte, pas le cours.

CE MODULE NE PRÉDIT RIEN. Il ne dit pas ce qui va monter ; il dit combien de
paris DIFFÉRENTS on a réellement en portefeuille. C'est une mesure de
concentration, vérifiable, sans hypothèse sur l'avenir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from marketlab import correlations

PERIODES_AN = 252

# Part des séances les plus agitées servant de référence. Un tiers : assez pour
# que la matrice soit estimable, assez peu pour décrire un vrai régime tendu.
PART_STRESS = 0.33

# Volatilité annualisée maximale visée pour l'ensemble du portefeuille, en %.
# Ce n'est pas une limite de perte : c'est l'amplitude ordinaire qu'on accepte
# de subir. Au-delà, une seule mauvaise séance efface plusieurs semaines.
PLAFOND_VOL_PORTEFEUILLE = 25.0

# Au-dessus de cette corrélation SIGNÉE, deux positions sont le même pari. Le
# candidat n'ajoute alors pas de diversification, il agrandit une ligne
# existante — et c'est ainsi qu'il faut le traiter.
SEUIL_MEME_PARI = 0.75

# Plancher de taille : en dessous, la position ne vaut pas ses frais.
FACTEUR_MIN = 0.25

# Co-chute de queue. La corrélation — même de stress — est une moyenne : deux
# actifs peuvent être modérément corrélés au quotidien et s'effondrer ENSEMBLE
# les mauvais jours, et c'est le seul cas qui compte pour un portefeuille.
# On mesure donc directement : quand le pari déjà détenu vit un jour de son
# pire décile, quelle est la probabilité que le candidat vive le sien ?
# Sous indépendance elle vaut 10 % ; au-dessus de ce seuil, les deux paris
# meurent ensemble quel que soit ce que dit la corrélation ordinaire.
SEUIL_CO_CHUTE = 0.60
CO_CHUTE_QUANTILE = 0.10
CO_CHUTE_OBS_MIN = 40          # jours de queue observés — ~400 séances


def co_chute(candidat_r: pd.Series, detenu_r: pd.Series) -> float | None:
    """P(le candidat vit son pire décile | le détenu vit le sien).

    Les séries sont les rendements DU PARI (déjà signées par le sens) : deux
    achats sur des actifs anti-corrélés ont des queues disjointes, un achat et
    une vente sur les mêmes en ont de communes — comme pour la corrélation
    signée, c'est le pari qui compte, pas le cours.
    """
    cadre = pd.concat([candidat_r.rename("c"), detenu_r.rename("d")],
                      axis=1).dropna()
    if len(cadre) < CO_CHUTE_OBS_MIN / CO_CHUTE_QUANTILE:
        return None
    seuil_c = cadre["c"].quantile(CO_CHUTE_QUANTILE)
    seuil_d = cadre["d"].quantile(CO_CHUTE_QUANTILE)
    queue_d = cadre[cadre["d"] <= seuil_d]
    if len(queue_d) < CO_CHUTE_OBS_MIN:
        return None
    return float((queue_d["c"] <= seuil_c).mean())


def _sens(valeur) -> int:
    return -1 if str(valeur).lower() in {"short", "vente", "-1"} else 1


def matrice_stress(symboles: list[str], jours: int = 750) -> pd.DataFrame | None:
    """Corrélations calculées sur le tiers le plus agité des séances.

    None si l'historique commun est trop mince — auquel cas l'appelant doit
    garder son comportement actuel plutôt que d'inventer une matrice.
    """
    symboles = list(dict.fromkeys(symboles))
    if len(symboles) < 2:
        return None
    try:
        r = correlations.rendements(symboles, jours)
    except Exception:
        return None
    if r is None or r.empty or r.shape[1] < 2:
        return None

    vol_panier = r.mean(axis=1).rolling(20).std().dropna()
    if len(vol_panier) < 60:
        return r.corr()          # pas de quoi distinguer les régimes
    seuil = vol_panier.quantile(1 - PART_STRESS)
    agite = r.loc[vol_panier[vol_panier >= seuil].index]
    if len(agite) < 30:
        return r.corr()
    return agite.corr()


def volatilites(symboles: list[str], jours: int = 750) -> dict[str, float]:
    """Volatilité annualisée par actif, en %, sur la même source que la matrice."""
    try:
        r = correlations.rendements(list(dict.fromkeys(symboles)), jours)
    except Exception:
        return {}
    if r is None or r.empty:
        return {}
    ecarts = r.std() * np.sqrt(PERIODES_AN) * 100
    return {s: float(v) for s, v in ecarts.items() if np.isfinite(v)}


def volatilite_ensemble(expositions: dict[str, float], corr: pd.DataFrame,
                        vols: dict[str, float]) -> float | None:
    """Volatilité annualisée de l'ensemble, en % de l'équité.

    `expositions` — notionnel SIGNÉ de chaque actif, en part de l'équité
    (0.10 = 10 % à l'achat, −0.10 = 10 % à la vente).
    """
    presents = [s for s in expositions if s in corr.index and s in vols]
    if not presents:
        return None
    w = np.array([expositions[s] for s in presents])
    v = np.array([vols[s] for s in presents])
    c = corr.loc[presents, presents].to_numpy()
    c = np.nan_to_num(c, nan=0.0)
    np.fill_diagonal(c, 1.0)
    variance = float((w * v) @ c @ (w * v))
    return float(np.sqrt(max(variance, 0.0)))


def paris_independants(expositions: dict[str, float],
                       corr: pd.DataFrame) -> float | None:
    """Nombre de paris RÉELLEMENT différents.

    Quatre positions parfaitement corrélées valent un seul pari ; quatre
    positions indépendantes en valent quatre. C'est le chiffre à regarder avant
    de se croire diversifié — le simple décompte des lignes ne dit rien.
    """
    presents = [s for s in expositions if s in corr.index]
    if not presents:
        return None
    w = np.abs(np.array([expositions[s] for s in presents]))
    if w.sum() <= 0:
        return None
    w = w / w.sum()
    c = np.nan_to_num(corr.loc[presents, presents].to_numpy(), nan=0.0)
    np.fill_diagonal(c, 1.0)
    concentration = float(w @ c @ w)
    return round(1 / concentration, 2) if concentration > 0 else None


def evaluer(positions: list[dict], equite: float,
            candidat: dict | None = None) -> dict:
    """Diagnostic de concentration, et taille recommandée pour un candidat.

    `positions` — celles déjà ouvertes : symbole, sens, marge, levier.
    `candidat`  — position envisagée, même forme, ou None pour un simple bilan.

    Ne lève JAMAIS : sans historique exploitable, renvoie `facteur = 1.0` et le
    dit. Un garde-fou qui tombe en panne ne doit pas empêcher le robot de
    travailler — il doit s'effacer.
    """
    bilan = {"facteur": 1.0, "raisons": [], "vol_avant_%": None,
             "vol_apres_%": None, "paris_independants": None,
             "correlation_max": None, "meme_pari_que": None,
             "mesurable": False}
    if equite <= 0:
        return bilan

    def _expo(p: dict) -> float:
        return (float(p.get("marge", 0)) * float(p.get("levier", 1) or 1)
                / equite * _sens(p.get("sens")))

    detenus = {p["symbole"]: _expo(p) for p in positions if p.get("symbole")}
    symboles = list(detenus)
    if candidat and candidat.get("symbole"):
        symboles = symboles + [candidat["symbole"]]

    corr = matrice_stress(symboles)
    vols = volatilites(symboles)
    if corr is None or not vols:
        bilan["raisons"].append("historique commun insuffisant : "
                                "dimensionnement inchangé")
        return bilan
    bilan["mesurable"] = True

    bilan["vol_avant_%"] = (round(volatilite_ensemble(detenus, corr, vols), 1)
                            if detenus else 0.0)
    bilan["paris_independants"] = paris_independants(detenus, corr)

    if not candidat or not candidat.get("symbole"):
        return bilan

    sym = candidat["symbole"]
    sens_c = _sens(candidat.get("sens"))
    expo_c = _expo(candidat)

    # Corrélation SIGNÉE par le sens : deux positions opposées sur des actifs
    # corrélés sont deux paris différents, et inversement.
    correlations_signees = {}
    for s, e in detenus.items():
        if s not in corr.index or sym not in corr.index or s == sym:
            continue
        rho = corr.loc[sym, s]
        if not np.isfinite(rho):
            continue
        correlations_signees[s] = float(rho) * sens_c * np.sign(e or 1)

    if correlations_signees:
        pire = max(correlations_signees.items(), key=lambda kv: kv[1])
        bilan["correlation_max"] = round(pire[1], 2)
        if pire[1] >= SEUIL_MEME_PARI:
            bilan["meme_pari_que"] = pire[0]
            bilan["raisons"].append(
                f"même pari que {pire[0]} (corrélation {pire[1]:+.2f} en "
                f"régime tendu) : ce n'est pas une ligne de plus, c'est la "
                f"même agrandie")

    # Second détecteur, pour ce que la corrélation ne voit pas : la co-chute
    # de queue. Ne se déclenche que si le premier n'a rien dit — deux raisons
    # pour le même fait n'en font pas deux.
    if bilan["meme_pari_que"] is None and detenus:
        try:
            rendements = correlations.rendements(symboles, 750)
        except Exception:
            rendements = None
        if rendements is not None and sym in rendements.columns:
            cand_r = rendements[sym] * sens_c
            pire_l, pire_avec = 0.0, None
            for s, e in detenus.items():
                if s == sym or s not in rendements.columns:
                    continue
                l = co_chute(cand_r, rendements[s] * np.sign(e or 1))
                if l is not None and l > pire_l:
                    pire_l, pire_avec = l, s
            if pire_avec is not None:
                bilan["co_chute_max"] = round(pire_l, 2)
                if pire_l >= SEUIL_CO_CHUTE:
                    bilan["meme_pari_que"] = pire_avec
                    bilan["raisons"].append(
                        f"co-chute de queue avec {pire_avec} : quand il vit "
                        f"son pire décile, le candidat vit le sien "
                        f"{pire_l * 100:.0f} % du temps (10 % si "
                        f"indépendants) — ils meurent ensemble, même si la "
                        f"corrélation ordinaire ne le montre pas")

    apres = dict(detenus)
    apres[sym] = apres.get(sym, 0.0) + expo_c
    vol_apres = volatilite_ensemble(apres, corr, vols)
    bilan["vol_apres_%"] = round(vol_apres, 1) if vol_apres else None

    facteur = 1.0
    if bilan["meme_pari_que"]:
        facteur = 0.5
    if vol_apres and vol_apres > PLAFOND_VOL_PORTEFEUILLE:
        # Réduire la taille jusqu'à rentrer dans le budget. La volatilité
        # n'étant pas linéaire en la taille, on cherche par dichotomie plutôt
        # que par une règle de trois qui se tromperait.
        bas, haut = 0.0, facteur
        for _ in range(24):
            milieu = (bas + haut) / 2
            essai = dict(detenus)
            essai[sym] = essai.get(sym, 0.0) + expo_c * milieu
            v = volatilite_ensemble(essai, corr, vols)
            if v is not None and v > PLAFOND_VOL_PORTEFEUILLE:
                haut = milieu
            else:
                bas = milieu
        facteur = bas
        bilan["raisons"].append(
            f"volatilité d'ensemble projetée {vol_apres:.0f} % au-dessus du "
            f"plafond de {PLAFOND_VOL_PORTEFEUILLE:.0f} % : taille ramenée à "
            f"{facteur * 100:.0f} %")

    # Le seuil couvre AUSSI le zéro exact : la dichotomie peut y tomber, et une
    # position écartée sans motif écrit est un refus qu'on ne peut pas relire.
    if facteur < FACTEUR_MIN:
        bilan["raisons"].append(
            f"taille résiduelle sous {FACTEUR_MIN * 100:.0f} % : position "
            f"écartée plutôt qu'ouverte pour rien" if facteur > 0
            else "aucune taille ne tient dans le budget de risque : "
                 "position écartée")
        facteur = 0.0

    bilan["facteur"] = round(facteur, 3)
    return bilan
