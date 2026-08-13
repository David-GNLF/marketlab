"""Surveillance des positions OUVERTES : la chaîne ne s'arrête plus à la porte.

LE TROU QUE CE MODULE COMBLE. La chaîne de dimensionnement juge une idée au
moment d'ENTRER : frais, régime, sauts, concentration, Kelly. Puis plus rien
ne regarde la position — or tout ce que la chaîne a vérifié BOUGE pendant la
vie du trade. Le régime peut basculer en tendu après l'entrée ; la part de
saut d'un titre peut monter ; deux positions détenues peuvent converger ; le
portage s'accumule chaque nuit et déplace le seuil de rentabilité en silence.
Un encadrement qui ne vaut qu'à l'entrée n'encadre que la moitié du trade.

CE QUE CE MODULE FAIT — ET NE FAIT PAS. Il SIGNALE, il n'agit jamais : fermer
une position est une décision qui appartient au titulaire du compte (ou aux
règles écrites du robot, qui existent déjà). Un garde-fou qui liquide de
lui-même est une source de pertes qu'on ne peut plus relire.

CONTRE LE BRUIT : chaque garde mémorise ce qu'il a déjà dit, DANS la position
elle-même (le compte est réécrit chaque nuit par la tenue, l'état voyage
avec). Une condition déjà signalée se tait tant qu'elle persiste, et se
réarme quand elle disparaît — une alerte répétée chaque nuit ne serait plus
lue au bout de trois jours, et c'est la quatrième qui compterait.

Les gardes s'EFFACENT en cas de panne (même contrat que le risque
d'ensemble) : une surveillance qui casse la tenue des comptes protégerait la
panne, pas le compte.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from marketlab import (correlations, implicite, microstructure, regimes,
                       risque_portefeuille)

# Au-dessus de cette part de variance venue des sauts, le stop ne protège
# plus vraiment : l'exécution se fait de l'autre côté. Plus haut que le seuil
# d'entrée (5 %) : à l'entrée on ajuste une taille, ici on dérange quelqu'un.
SEUIL_PART_SAUT = 0.20

# Paliers de portage, en % de la MISE déjà consommés. Chaque palier n'est
# signalé qu'une fois : c'est le franchissement qui informe, pas le niveau.
PALIERS_PORTAGE_PCT = (1.0, 2.0, 5.0)

HORIZON_DEFAUT = 20

# Pression VENDEUSE lue dans les options — ce que le marché price, pas ce que
# nous prédisons. Un skew qui se creuse = les puts s'enchérissent, le marché
# achète de la protection contre la baisse ; une IV qui bondit = il price un
# mouvement violent. Seuils depuis la RÉFÉRENCE prise à la première
# surveillance de la position : c'est le CHANGEMENT depuis votre entrée qui
# surprend, pas le niveau.
SEUIL_SKEW_PTS = 5.0
SEUIL_IV_RATIO = 1.5


def _etat(p: dict) -> dict:
    return p.setdefault("surveillance", {})


def _sens(p: dict) -> int:
    return -1 if str(p.get("sens", "long")).lower() in {"short", "vente"} else 1


def _seances_depuis(date_str) -> int | None:
    """Séances (jours ouvrés) écoulées depuis une date. None si illisible."""
    try:
        debut = pd.Timestamp(str(date_str)[:10])
        return max(0, len(pd.bdate_range(debut, pd.Timestamp.now())) - 1)
    except Exception:
        return None


# ------------------------------------------------------------ gardes unitaires

def _garde_regime(p: dict) -> list[str]:
    """Le marché est-il passé dans un régime où l'avis est suspendu ?

    On ne connaît pas le régime au moment de l'entrée (il n'était pas
    consigné) ; ce qu'on peut dire d'honnête est : AUJOURD'HUI, l'outil qui a
    motivé cette position est mesuré inversé. Signalé une fois par épisode —
    l'état se réarme quand le régime redevient fréquentable.
    """
    suspension = regimes.avis_suspendu()
    etat = _etat(p)
    if not suspension:
        etat.pop("regime_signale", None)
        return []
    courant = suspension.get("regime", "?")
    if etat.get("regime_signale") == courant:
        return []
    etat["regime_signale"] = courant
    return [f"surveillance {p['symbole']} : le marché est passé en "
            f"{regimes.ETIQUETTES.get(courant, courant)} — un régime où le "
            f"classement de l'outil est mesuré inversé. Le raisonnement qui a "
            f"ouvert cette position n'y vaut plus rien ; le stop est la seule "
            f"protection restante."]


def _garde_sauts(p: dict) -> list[str]:
    """La structure du titre a-t-elle changé : sa volatilité saute-t-elle ?"""
    part = microstructure.part_sauts(p.get("symbole", ""))
    etat = _etat(p)
    if not part or part["part_saut"] <= SEUIL_PART_SAUT:
        etat.pop("sauts_signale", None)
        return []
    if etat.get("sauts_signale"):
        return []
    etat["sauts_signale"] = True
    return [f"surveillance {p['symbole']} : {part['part_saut'] * 100:.0f} % "
            f"de la volatilité de ce titre vient de SAUTS (médiane sur "
            f"{part['n_seances']} séances) — un saut traverse le stop au lieu "
            f"de s'y arrêter, la perte réelle peut dépasser la perte prévue."]


def _garde_portage(p: dict) -> list[str]:
    """Le portage a-t-il franchi un palier ? Le levier se paie chaque nuit,
    et ce coût déplace le seuil de rentabilité sans jamais faire de bruit."""
    marge = float(p.get("marge") or 0)
    frais = float(p.get("frais_portage_cumules") or 0)
    if marge <= 0 or frais <= 0:
        return []
    pct = frais / marge * 100
    franchis = [s for s in PALIERS_PORTAGE_PCT if pct >= s]
    if not franchis:
        return []
    palier = max(franchis)
    etat = _etat(p)
    if float(etat.get("portage_signale_pct") or 0) >= palier:
        return []
    etat["portage_signale_pct"] = palier
    return [f"surveillance {p['symbole']} : le portage a déjà consommé "
            f"{pct:.1f} % de la mise ({frais:.2f} $) — chaque nuit de plus "
            f"élève le seuil de rentabilité de cette position."]


def _garde_horizon(p: dict, horizon: int) -> list[str]:
    """Le plan avait un horizon ; au-delà, il ne dit plus rien.

    Stop et objectif viennent de simulations sur `horizon` séances. Une
    position qui traîne au-delà vit sur un plan périmé — elle n'est pas
    forcément mauvaise, mais plus personne ne peut dire qu'elle est bonne.
    """
    n = _seances_depuis(p.get("ouvert_le"))
    etat = _etat(p)
    if n is None or n <= horizon:
        return []
    if etat.get("horizon_signale"):
        return []
    etat["horizon_signale"] = True
    return [f"surveillance {p['symbole']} : ouverte depuis {n} séances alors "
            f"que le plan portait sur {horizon} — stop et objectif reposent "
            f"sur des simulations qui ne couvrent plus cette période. "
            f"Reconduire est une décision, pas un défaut."]


def _implicite_du_titre(symbole: str) -> dict | None:
    """Dernier relevé d'options du titre (IV à la monnaie, skew), ou None.

    Seules les actions US ont des chaînes d'options relevées : pour les
    autres, ce garde s'efface sans bruit.
    """
    releve = implicite.charger_releve()
    if releve is None or releve.empty:
        return None
    du_titre = releve[releve["symbole"] == symbole]
    if du_titre.empty:
        return None
    derniere = du_titre.sort_values("date").iloc[-1]
    iv, skew = derniere.get("iv_atm_pct"), derniere.get("skew_pts")
    if pd.isna(iv) or pd.isna(skew):
        return None
    return {"iv": float(iv), "skew": float(skew)}


def _garde_pression_vendeuse(p: dict) -> list[str]:
    """Le marché des options s'est-il mis à pricer la baisse depuis l'entrée ?

    On ne prédit rien — le signal directionnel de l'outil a échoué à tous
    ses arbitrages. On lit ce que LE MARCHÉ paie : la référence (IV, skew)
    est prise à la première surveillance de la position, et c'est le
    creusement DEPUIS cette référence qui alerte. Une fois par franchissement,
    réarmé si la pression retombe.
    """
    mesure = _implicite_du_titre(p.get("symbole", ""))
    etat = _etat(p)
    if mesure is None:
        return []
    if "iv_ref" not in etat:
        etat["iv_ref"] = mesure["iv"]
        etat["skew_ref"] = mesure["skew"]
        return []
    alertes = []

    creusement = mesure["skew"] - float(etat["skew_ref"])
    if creusement >= SEUIL_SKEW_PTS and not etat.get("skew_signale"):
        etat["skew_signale"] = True
        alertes.append(
            f"surveillance {p['symbole']} : le skew des options s'est creusé "
            f"de {creusement:.1f} pts depuis votre entrée — le marché paie sa "
            f"protection contre la baisse nettement plus cher qu'avant. Ce "
            f"n'est pas une prédiction, c'est le prix de l'assurance qui "
            f"monte.")
    elif creusement < SEUIL_SKEW_PTS:
        etat.pop("skew_signale", None)

    ratio = mesure["iv"] / float(etat["iv_ref"]) if etat["iv_ref"] else None
    if ratio and ratio >= SEUIL_IV_RATIO and not etat.get("iv_signale"):
        etat["iv_signale"] = True
        alertes.append(
            f"surveillance {p['symbole']} : la volatilité implicite a bondi "
            f"de {(ratio - 1) * 100:.0f} % depuis votre entrée "
            f"({etat['iv_ref']:.0f} % → {mesure['iv']:.0f} %) — le marché "
            f"price un mouvement bien plus violent qu'à l'ouverture de la "
            f"position. Le stop protège de la dérive, pas d'un écart.")
    elif ratio and ratio < SEUIL_IV_RATIO:
        etat.pop("iv_signale", None)
    return alertes


# ------------------------------------------------------- garde entre détenues

def _garde_concentration(compte: dict) -> list[str]:
    """Deux positions DÉTENUES sont-elles devenues le même pari ?

    La chaîne d'entrée compare le candidat à l'existant ; personne ne
    recompare l'existant à lui-même au fil du temps. Mêmes détecteurs que
    l'entrée — corrélation de stress signée, puis co-chute de queue si la
    corrélation s'est tue — mêmes seuils, une seule raison par paire.
    """
    positions = [p for p in compte.get("positions", []) if p.get("symbole")]
    if len(positions) < 2:
        compte.pop("surveillance_paires", None)
        return []
    symboles = list(dict.fromkeys(p["symbole"] for p in positions))
    if len(symboles) < 2:
        return []

    corr = risque_portefeuille.matrice_stress(symboles)
    try:
        rendements = correlations.rendements(symboles, 750)
    except Exception:
        rendements = None

    sens_par_symbole = {p["symbole"]: _sens(p) for p in positions}
    flagrantes: dict[str, str] = {}
    for i, a in enumerate(symboles):
        for b in symboles[i + 1:]:
            raison = None
            rho_signe = None
            if corr is not None and a in corr.index and b in corr.index:
                rho = corr.loc[a, b]
                if np.isfinite(rho):
                    rho_signe = (float(rho) * sens_par_symbole[a]
                                 * sens_par_symbole[b])
            if rho_signe is not None \
                    and rho_signe >= risque_portefeuille.SEUIL_MEME_PARI:
                raison = (f"corrélation {rho_signe:+.2f} en régime tendu : "
                          f"deux stops touchés le même jour ne feraient "
                          f"qu'une seule perte, double")
            elif rendements is not None and a in rendements.columns \
                    and b in rendements.columns:
                l = risque_portefeuille.co_chute(
                    rendements[a] * sens_par_symbole[a],
                    rendements[b] * sens_par_symbole[b])
                if l is not None and l >= risque_portefeuille.SEUIL_CO_CHUTE:
                    raison = (f"co-chute de queue {l * 100:.0f} % (10 % si "
                              f"indépendants) : ils meurent ensemble même si "
                              f"la corrélation ordinaire ne le montre pas")
            if raison:
                flagrantes["|".join(sorted((a, b)))] = raison

    deja = compte.get("surveillance_paires") or {}
    alertes = []
    for cle, raison in flagrantes.items():
        if cle in deja:
            continue
        a, b = cle.split("|")
        alertes.append(f"surveillance : {a} et {b} détenues ensemble sont "
                       f"devenues le MÊME pari — {raison}.")
    # l'état ne garde que les paires encore flagrantes : une paire assainie
    # (position fermée, corrélation retombée) se réarme d'elle-même
    if flagrantes:
        compte["surveillance_paires"] = {k: pd.Timestamp.now().strftime("%Y-%m-%d")
                                         for k in flagrantes}
    else:
        compte.pop("surveillance_paires", None)
    return alertes


# ---------------------------------------------------------------- point d'entrée

def examiner(compte: dict) -> list[str]:
    """Rejoue les gardes de la chaîne sur les positions ouvertes d'un compte.

    Modifie le compte EN PLACE (l'état anti-répétition vit dans les positions,
    réécrites chaque nuit par la tenue) et renvoie les alertes NOUVELLES.
    Ne lève jamais : chaque garde en panne s'efface, seul, en silence — la
    tenue des comptes passe avant la surveillance.
    """
    alertes: list[str] = []
    horizon = int(compte.get("horizon") or HORIZON_DEFAUT)
    for p in compte.get("positions", []):
        for garde in (_garde_regime, _garde_sauts, _garde_portage,
                      _garde_pression_vendeuse):
            try:
                alertes += garde(p)
            except Exception:
                continue
        try:
            alertes += _garde_horizon(p, horizon)
        except Exception:
            continue
    try:
        alertes += _garde_concentration(compte)
    except Exception:
        pass
    return alertes
