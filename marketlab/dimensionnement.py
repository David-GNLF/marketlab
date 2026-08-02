"""Combien mettre sur une idée — une seule réponse, et toute la chaîne visible.

LE PRINCIPE, ET IL EST CONTRE-INTUITIF POUR BEAUCOUP. On ne dimensionne pas
une position en pourcentage du CAPITAL, mais en pourcentage du RISQUE. Mettre
5 % de son compte sur l'euro-dollar (qui bouge de 5 % par an) et 5 % sur le
bitcoin (qui bouge de 30 %) revient à prendre six fois plus de risque sur le
second tout en croyant faire la même chose. La bonne question n'est pas
« combien j'engage » mais « combien je perds si le stop est touché ».

C'est exactement ce que `levels.plan()` calculait déjà quand on lui passe un
capital — et que personne ne lisait : le robot dimensionne à 5 % de l'équité,
quel que soit l'actif. Ce module ne réinvente donc pas ce calcul, il le remet
au centre et lui ajoute ce qui manquait autour.

CE QUE LA TAILLE NE DOIT PAS SUIVRE : la conviction. La note de cet outil n'a
pas démontré de pouvoir de classement (IC mesuré ≈ 0, négatif en marché tendu).
Doser selon la note reviendrait à doser selon du bruit. La note sert donc de
PORTE — on entre ou on n'entre pas — jamais d'amplificateur. C'est la
différence entre un outil qui encadre et un outil qui flatte.

LA CHAÎNE, dans l'ordre où elle s'applique. Chaque étape peut réduire, aucune
ne peut augmenter :

  1. l'idée survit-elle à ses frais ?              sinon rien
  2. le régime autorise-t-il un avis directionnel ? sinon rien
  3. taille par le RISQUE (distance au stop)
  4. plafond de concentration (corrélation)
  5. plafond absolu par position

Chaque réduction est nommée. Une taille sans explication est un chiffre qu'on
suit ou qu'on ignore ; une taille expliquée est une décision.
"""

from __future__ import annotations

from marketlab import couts

# Part de l'équité RISQUÉE par idée — pas engagée, risquée : c'est ce qu'on
# perd si le stop est touché. 1 % est la valeur d'usage : elle laisse encaisser
# une longue série de pertes sans que le compte devienne inexploitable.
RISQUE_PAR_IDEE_PCT = 1.0

# Plafond absolu de mise, en part de l'équité. Il n'a rien à voir avec le
# risque : il protège du cas où un stop très proche justifierait, par le calcul,
# d'engager la moitié du compte sur une seule ligne. Un stop serré peut être
# franchi par un simple écart de cotation.
PLAFOND_MISE_PCT = 10.0

# En dessous, la position ne vaut pas ses frais ni l'attention qu'elle demande.
MISE_MIN = 10.0


def _kelly(plan: dict) -> float | None:
    """Fraction de Kelly du plan, en part de l'équité exposée (notionnel).

    Trois issues par construction du plan : objectif touché (+g, probabilité
    p), stop touché (−s, probabilité q), ni l'un ni l'autre (≈ 0). Maximiser
    la croissance logarithmique donne, en fermé :

        f* = (p·g − q·s) / (g·s·(p + q))

    None si le plan ne porte pas ses probabilités — on ne fabrique pas un
    plafond avec des chiffres absents.
    """
    try:
        p = float(plan.get("proba_toucher_objectif_%", 0)) / 100
        q = float(plan.get("proba_toucher_stop_%", 0)) / 100
        g = float(plan.get("gain_potentiel_%", 0)) / 100
        s = float(plan.get("risque_%", 0)) / 100
    except (TypeError, ValueError):
        return None
    if min(p, q) <= 0 or min(g, s) <= 0 or p + q > 1.001:
        return None
    return (p * g - q * s) / (g * s * (p + q))


def dimensionner(symbole: str, plan: dict, equite: float,
                 positions: list[dict] | None = None,
                 horizon: int = 20, levier: float | None = None,
                 risque_pct: float = RISQUE_PAR_IDEE_PCT,
                 avis_suspendu: dict | None = None) -> dict:
    """Mise recommandée, et la raison de chaque réduction.

    `plan` — sortie de `levels.plan()` : il porte l'entrée, le stop, et donc la
    distance qui détermine la taille.

    Ne lève jamais : une brique manquante réduit la précision, pas la
    disponibilité. Mais elle est dite.
    """
    etapes = []
    resultat = {
        "symbole": symbole, "mise": 0.0, "mise_%_equite": 0.0,
        "notionnel": 0.0, "levier": None, "perte_si_stop": 0.0,
        "risque_%_equite": 0.0, "etapes": etapes, "retenue": False,
    }
    if equite <= 0 or not plan:
        etapes.append("aucune équité ou aucun plan : rien à dimensionner")
        resultat["lecture"] = "Pas de quoi dimensionner."
        return resultat

    levier = float(levier if levier is not None else couts.levier_defaut(symbole))

    # --- 1. l'idée survit-elle à ses frais ? ------------------------------
    frais = plan.get("couts") or {}
    if not frais:
        try:
            frais = couts.net(float(plan.get("esperance_%", 0)), symbole,
                              horizon=horizon, levier=levier)
        except Exception:
            frais = {}
    if frais and frais.get("survit_aux_frais") is False:
        etapes.append(
            f"ÉCARTÉE — l'espérance ne survit pas aux frais : "
            f"{frais.get('esperance_brute_%')} % attendus pour "
            f"{frais.get('seuil_actif_%')} % de coût")
        resultat["lecture"] = (
            "Aucune mise : cette idée est perdante une fois le spread et le "
            "portage comptés. Le dimensionnement ne rattrape pas une espérance "
            "négative, il ne fait que choisir combien perdre.")
        return resultat
    if frais:
        etapes.append(
            f"survit aux frais : {frais.get('esperance_nette_%')} % nets "
            f"(coût {frais.get('seuil_actif_%')} % sur l'actif)")

    # --- 2. le régime autorise-t-il un avis directionnel ? ----------------
    if avis_suspendu:
        etapes.append(
            f"ÉCARTÉE — avis directionnel suspendu en "
            f"{avis_suspendu.get('regime', 'ce régime')} : le classement de "
            f"l'outil y a été mesuré inversé")
        resultat["lecture"] = (
            "Aucune mise : dans ce régime de marché, la capacité de l'outil à "
            "trier les actifs a été mesurée INVERSÉE. Prendre une position sur "
            "un avis dont on sait qu'il se trompe n'est pas du risque, c'est "
            "une erreur connue.")
        return resultat

    # --- 3. taille par le RISQUE ------------------------------------------
    risque_actif_pct = float(plan.get("risque_%") or 0)
    if risque_actif_pct <= 0:
        etapes.append("distance au stop inconnue : dimensionnement impossible")
        resultat["lecture"] = ("Pas de stop exploitable : sans distance connue, "
                               "aucune taille ne peut être justifiée.")
        return resultat

    # Perte si stop = notionnel × distance au stop. On veut que cette perte
    # vaille `risque_pct` de l'équité, d'où le notionnel cible.
    perte_visee = equite * risque_pct / 100
    notionnel = perte_visee / (risque_actif_pct / 100)
    mise = notionnel / levier
    etapes.append(
        f"risque visé {risque_pct:.1f} % de l'équité ({perte_visee:.2f} $) ; "
        f"stop à {risque_actif_pct:.2f} % ⇒ notionnel {notionnel:.0f} $, "
        f"mise {mise:.2f} $ à effet {levier:.0f}")

    # --- 3 bis. la part de saut : ce que le stop ne protège pas -----------
    # La taille ci-dessus suppose que la perte s'arrête AU stop. C'est vrai
    # pour la dérive continue ; un saut traverse le stop et l'exécution se
    # fait plus loin. La variation bipower mesure, par actif, la part de la
    # variance qui vient des sauts — et la mise est réduite d'autant :
    # 1/(1 + part), soit ×0,77 pour un actif dont 30 % de la variance saute.
    # Ordre de grandeur assumé, pas un modèle d'exécution : le but est que
    # deux actifs de même volatilité mais de structures différentes ne portent
    # pas la même mise.
    try:
        from marketlab import microstructure
        sauts = microstructure.part_sauts(symbole)
    except Exception:
        sauts = None
    if sauts and sauts["part_saut"] > 0.05:
        facteur_sauts = 1 / (1 + sauts["part_saut"])
        mise *= facteur_sauts
        notionnel = mise * levier
        etapes.append(
            f"sauts : {sauts['part_saut'] * 100:.0f} % de la variance de cet "
            f"actif TRAVERSE les stops au lieu de passer par tous les prix "
            f"(médiane sur {sauts['n_seances']} séances) — mise réduite à "
            f"{facteur_sauts * 100:.0f} %")

    # --- 4. plafond de concentration --------------------------------------
    if positions:
        try:
            from marketlab import risque_portefeuille
            r = risque_portefeuille.evaluer(
                positions, equite,
                {"symbole": symbole, "sens": "long", "marge": mise,
                 "levier": levier})
            if r["facteur"] < 1:
                mise *= r["facteur"]
                notionnel = mise * levier
                etapes.append(
                    f"concentration : taille ramenée à {r['facteur'] * 100:.0f} % — "
                    + " ; ".join(r["raisons"]))
            elif r.get("mesurable"):
                etapes.append("concentration : rien à corriger, l'idée "
                              "n'ajoute pas au pari déjà en portefeuille")
        except Exception as exc:
            etapes.append(f"concentration non mesurable ({str(exc)[:50]}) : "
                          "taille inchangée")

    # --- 5. plafond absolu -------------------------------------------------
    plafond = equite * PLAFOND_MISE_PCT / 100
    if mise > plafond:
        etapes.append(
            f"plafond par position : mise ramenée de {mise:.2f} $ à "
            f"{plafond:.2f} $ ({PLAFOND_MISE_PCT:.0f} % de l'équité). Un stop "
            f"très proche justifierait par le calcul d'engager bien davantage, "
            f"mais un écart de cotation suffirait à le franchir")
        mise = plafond
        notionnel = mise * levier

    # --- 6. le plafond de Kelly -------------------------------------------
    # La fraction de Kelly maximise la croissance composée SI les probabilités
    # du plan sont justes — hypothèse trop forte pour la viser, assez bonne
    # pour servir de PLAFOND : une taille au-dessus de Kelly perd de l'argent
    # en croissance composée même quand les probabilités sont exactes. Le
    # dimensionnement par le risque vit très en dessous ; le jour où un stop
    # étroit et un gros budget la dépasseraient, c'est Kelly qui a raison.
    kelly = _kelly(plan)
    if kelly is not None:
        resultat["kelly"] = {
            "fraction": round(kelly, 4),
            "plafond_notionnel": round(kelly * equite, 2),
            "part_utilisee_%": (round(notionnel / (kelly * equite) * 100, 1)
                                if kelly > 0 else None),
        }
        if kelly > 0 and notionnel > kelly * equite:
            notionnel = kelly * equite
            mise = notionnel / levier
            etapes.append(
                f"plafond de Kelly : exposition ramenée à "
                f"{notionnel:.0f} $ — au-delà, même des probabilités EXACTES "
                f"perdent de l'argent en croissance composée")

    if mise < MISE_MIN:
        etapes.append(f"mise résiduelle sous {MISE_MIN:.0f} $ : position écartée "
                      f"plutôt qu'ouverte pour rien")
        resultat["lecture"] = ("Aucune mise : ce qui resterait après les "
                               "réductions ne vaut pas ses frais.")
        return resultat

    perte = notionnel * risque_actif_pct / 100
    resultat.update({
        "mise": round(mise, 2),
        "mise_%_equite": round(mise / equite * 100, 2),
        "notionnel": round(notionnel, 2),
        "levier": round(levier, 2),
        "perte_si_stop": round(perte, 2),
        "risque_%_equite": round(perte / equite * 100, 2),
        "retenue": True,
    })
    resultat["lecture"] = (
        f"Mise recommandée {mise:.2f} $ ({resultat['mise_%_equite']:.1f} % de "
        f"l'équité), soit {notionnel:.0f} $ d'exposition à effet {levier:.0f}. "
        f"Si le stop est touché, la perte est de {perte:.2f} $ — "
        f"{resultat['risque_%_equite']:.1f} % du compte. C'est ce chiffre-là "
        f"qui a déterminé la taille, pas la note.")
    return resultat


def comparer_a_taille_fixe(resultat: dict, equite: float,
                           part_fixe_pct: float = 5.0) -> dict:
    """Ce qu'une taille fixe en % du capital aurait donné, et l'écart.

    Sert à montrer ce que le dimensionnement par le risque change réellement :
    sur un actif calme il autorise DAVANTAGE, sur un actif agité beaucoup
    moins. C'est le même compte qui est protégé dans les deux cas.
    """
    fixe = equite * part_fixe_pct / 100
    par_risque = float(resultat.get("mise") or 0)
    if fixe <= 0:
        return {"fixe": 0.0, "par_risque": par_risque, "rapport": None}
    return {
        "fixe": round(fixe, 2),
        "par_risque": round(par_risque, 2),
        "rapport": round(par_risque / fixe, 2),
        "lecture": (
            f"Une taille fixe de {part_fixe_pct:.0f} % aurait engagé "
            f"{fixe:.2f} $ ; le dimensionnement par le risque en met "
            f"{par_risque:.2f} $, soit {par_risque / fixe:.2f} fois. "
            + ("L'actif est calme : à risque égal, on peut engager davantage."
               if par_risque > fixe else
               "L'actif est agité : la même mise aurait fait courir un risque "
               "bien supérieur.")),
    }
