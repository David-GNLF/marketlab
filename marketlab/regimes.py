"""L'outil se trompe-t-il PARTOUT, ou seulement dans certains marchés ?

LA QUESTION, ET POURQUOI ELLE N'AVAIT PAS ÉTÉ POSÉE. Le bilan mesure un IC
unique, moyenné sur deux ans : −0,087 sur le rendement relatif. Un chiffre
unique suppose que la capacité de l'outil est CONSTANTE. Or la composante
technique a un IC négatif et des quintiles monotones décroissants — signature
d'un retour à la moyenne. Ce genre d'effet s'inverse typiquement entre marché
calme et marché tendu : acheter ce qui vient de monter fonctionne dans une
tendance installée et se retourne quand la volatilité éclate.

Si c'est le cas ici, la conclusion n'est pas « l'outil ne marche pas » mais
« l'outil marche à l'envers dans un régime » — ce qui est exploitable, alors
qu'une moyenne nulle ne l'est pas.

CE MODULE NE PROMET RIEN. Il peut parfaitement conclure « rien de mesurable
dans aucun régime », et ce serait une information : cela fermerait une piste
au lieu de la laisser ouverte indéfiniment.

AUCUNE DONNÉE NOUVELLE. Le journal des décisions et les cours de marché
suffisent : tout est déjà enregistré, il manquait seulement de croiser les deux.

LE PIÈGE CENTRAL — LE CLASSEMENT NE DOIT RIEN SAVOIR DU FUTUR. Classer les
séances en « calme / normal / tendu » avec les quantiles calculés sur TOUT
l'historique reviendrait à utiliser, pour juger une décision de juin 2024, la
volatilité observée en 2026. Le régime serait alors une information que
personne ne possédait au moment du verdict, et la mesure serait truquée sans
que rien ne le signale. Les quantiles sont donc calculés en fenêtre
EXPANSIVE : à chaque date, uniquement sur ce qui précède.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from marketlab import config, decision, validation

# Référence de nervosité du marché. Le VIX d'abord : c'est la mesure que les
# salles regardent. À défaut, la volatilité réalisée de l'indice large — moins
# fine, mais toujours disponible.
REFERENCE = "^VIX"
REFERENCE_SECOURS = "^GSPC"

# Observations minimales avant de classer quoi que ce soit. En dessous, les
# quantiles expansifs décriraient le hasard des premiers jours.
HISTORIQUE_MIN = 250

SEUIL_BAS, SEUIL_HAUT = 0.33, 0.67

ETIQUETTES = {"calme": "marché calme", "normal": "marché ordinaire",
              "tendu": "marché tendu"}


def serie_reference(jours: int = 1500) -> pd.Series | None:
    """Nervosité du marché, une valeur par séance. None si indisponible."""
    from marketlab.data import get_ohlcv
    try:
        return get_ohlcv(REFERENCE, lookback_days=jours)["close"].dropna()
    except Exception:
        pass
    try:
        cours = get_ohlcv(REFERENCE_SECOURS, lookback_days=jours)["close"].dropna()
    except Exception:
        return None
    # volatilité réalisée sur 20 séances, annualisée — même rôle que le VIX
    r = np.log(cours / cours.shift(1))
    return (r.rolling(20).std() * np.sqrt(252) * 100).dropna()


def classer(reference: pd.Series | None = None) -> pd.Series:
    """Régime de chaque séance, sans jamais regarder au-delà.

    Les bornes viennent de quantiles EXPANSIFS : à la date t, on ne connaît
    que les séances jusqu'à t. C'est ce qui rend la mesure honnête — et c'est
    aussi pourquoi les premiers mois restent « indéterminé ».
    """
    reference = serie_reference() if reference is None else reference
    if reference is None or reference.empty:
        return pd.Series(dtype=object)

    ref = reference.sort_index()
    bas = ref.expanding(min_periods=HISTORIQUE_MIN).quantile(SEUIL_BAS)
    haut = ref.expanding(min_periods=HISTORIQUE_MIN).quantile(SEUIL_HAUT)

    regime = pd.Series("indetermine", index=ref.index, dtype=object)
    regime[ref <= bas] = "calme"
    regime[(ref > bas) & (ref < haut)] = "normal"
    regime[ref >= haut] = "tendu"
    regime[bas.isna()] = "indetermine"
    return regime


def attacher(df: pd.DataFrame, regime: pd.Series | None = None) -> pd.DataFrame:
    """Ajoute la colonne `regime` à un journal évalué, par date.

    Report en arrière (`ffill` par date antérieure) : un verdict rendu un jour
    férié ou un week-end se rattache au dernier régime CONNU, jamais au
    suivant — qui n'existait pas encore.
    """
    if df is None or df.empty:
        return df
    regime = classer() if regime is None else regime
    sortie = df.copy()
    if regime is None or regime.empty:
        sortie["regime"] = "indetermine"
        return sortie
    reg = regime.sort_index()
    reg.index = pd.to_datetime(reg.index)
    dates = pd.to_datetime(sortie["date"])
    positions = reg.index.searchsorted(dates, side="right") - 1
    sortie["regime"] = [reg.iloc[p] if p >= 0 else "indetermine"
                        for p in positions]
    return sortie


def _ic(df: pd.DataFrame, colonne_note: str, horizon: int) -> dict:
    """IC transversal d'une colonne, via la mesure existante.

    On renomme la colonne en `note` plutôt que de recopier le calcul :
    `decision._mesurer_competence` porte le Fama-MacBeth ET l'erreur-type de
    Newey-West qui tient compte du recouvrement des fenêtres. Deux
    implémentations du même test finiraient par diverger, et c'est précisément
    sur ce genre de mesure qu'on ne peut pas se le permettre.
    """
    if colonne_note not in df.columns:
        return {"statut": "colonne absente"}
    sous = df.dropna(subset=[colonne_note]).copy()
    sous["note"] = sous[colonne_note]
    return decision._mesurer_competence(sous, "rendement_relatif_%",
                                        horizon_force=horizon)


def analyser(horizon: int | None = None) -> dict:
    """L'outil classe-t-il mieux les actifs dans certains marchés ?

    Renvoie, par régime, l'IC de la note globale et de chaque composante.
    Ne lève jamais : sans journal exploitable, le dit et s'arrête.
    """
    horizon = horizon or 20
    try:
        df = decision._evaluer_journal(horizon)
    except Exception as exc:
        return {"mesurable": False, "raison": f"journal illisible : {exc}"}
    if df is None or df.empty:
        return {"mesurable": False, "raison": "aucun verdict arrivé à échéance"}

    df = attacher(df)
    if "regime" not in df.columns:
        return {"mesurable": False, "raison": "régimes non calculables"}

    composantes = sorted(c for c in df.columns if c.startswith("c_"))
    par_regime = {}
    for nom, groupe in df.groupby("regime"):
        if nom == "indetermine":
            continue
        entree = {
            "n_verdicts": int(len(groupe)),
            "n_dates": int(groupe["date"].nunique()),
            "rendement_moyen_%": round(
                float(groupe["rendement_reel_%"].mean()), 2),
            "note": _ic(groupe, "note", horizon),
            # Seconde mesure, par un chemin opposé : au lieu de corriger
            # l'écart-type pour le recouvrement, on l'ÉLIMINE en n'échantillonnant
            # que des dates disjointes. Quand les deux divergent, c'est presque
            # toujours que la correction a été trop généreuse.
            "purge": validation.ic_purge(groupe, horizon=horizon),
            "composantes": {c[2:]: _ic(groupe, c, horizon) for c in composantes},
        }
        par_regime[nom] = entree

    return {
        "mesurable": bool(par_regime),
        "horizon": horizon,
        "reference": REFERENCE,
        "n_total": int(len(df)),
        "n_indetermine": int((df["regime"] == "indetermine").sum()),
        "par_regime": par_regime,
        "lecture": _lecture(par_regime),
    }


def _lecture(par_regime: dict) -> str:
    """Ce qu'il faut retenir, en une phrase — y compris quand il n'y a rien."""
    # DEUX MESURES COHABITENT et l'on retient la plus STRICTE. L'écart-type
    # corrigé (Newey-West) et l'échantillonnage sans recouvrement répondent à
    # la même question par des chemins opposés ; mesuré sur ce projet, le
    # second contredit le premier sur l'ensemble des verdicts — 21,7 % des
    # découpages concluent là où le t corrigé annonçait « négatif ». Publier
    # la conclusion la plus généreuse quand on dispose de la plus exigeante
    # reviendrait à choisir le chiffre qui arrange.
    def _conclut(e: dict) -> bool:
        purge = e.get("purge") or {}
        if purge.get("mesurable"):
            return str(purge.get("verdict", "")).endswith("confirmé")
        return (isinstance(e.get("note"), dict)
                and e["note"].get("sens") in {"positif", "negatif", "négatif"})

    prouves = {nom: e for nom, e in par_regime.items()
               if _conclut(e)}
    if not prouves:
        return ("Aucun régime ne montre de capacité démontrée à classer les "
                "actifs, ni dans un sens ni dans l'autre. La moyenne nulle du "
                "bilan ne cachait donc pas deux comportements opposés : elle "
                "décrit bien l'absence de signal partout.")
    morceaux = []
    for nom, e in prouves.items():
        purge = e.get("purge") or {}
        if purge.get("mesurable"):
            inverse = purge["ic_moyen"] < 0
            morceaux.append(
                f"{ETIQUETTES.get(nom, nom)} : classement "
                f"{'INVERSÉ' if inverse else 'correct'}, confirmé par "
                f"{purge['part_concluante_%']:.0f} % des découpages sans "
                f"recouvrement (IC {purge['ic_moyen']:+.3f}, "
                f"{purge['obs_par_echantillonnage']} observations "
                f"indépendantes par découpage)")
        else:
            sens = e["note"]["sens"]
            morceaux.append(
                f"{ETIQUETTES.get(nom, nom)} : classement "
                f"{'correct' if sens == 'positif' else 'INVERSÉ'} "
                f"({e['n_dates']} dates — mesure corrigée seulement, "
                f"non reconfirmée sans recouvrement)")

    fin = ""
    if len(prouves) < len(par_regime):
        fin = (" Un effet présent dans un seul régime est exactement ce qu'une "
               "moyenne unique efface : le bilan d'ensemble ne le montrait pas.")
    # Le nombre d'épisodes indépendants est le seul chiffre qui dise si l'on
    # peut y croire. Une dizaine, c'est peu : le t affiché repose sur des
    # fenêtres qui se recouvrent largement, et une seule période agitée peut
    # porter tout le résultat.
    maigres = [(e.get("purge") or {}).get("obs_par_echantillonnage")
               or e["note"].get("episodes_independants", 0)
               for e in prouves.values()]
    if maigres and min(maigres) < 15:
        fin += (" À manier avec prudence : le résultat ne repose que sur une "
                "poignée d'épisodes de marché distincts, et une seule période "
                "agitée peut le porter à elle seule. À reconfirmer quand "
                "l'historique aura grandi.")
    return " ; ".join(morceaux) + "." + fin


# ---------------------------------------------------------------------------
# Verdict persisté : ce que le moteur de décision doit en faire
# ---------------------------------------------------------------------------

VERDICT_PATH = config.DATA_DIR / "regimes_verdict.json"

# Régime courant : la dernière séance classée. Relu souvent (une fois par
# titre à la génération), donc mémorisé pour la durée du processus.
_MEMO: dict = {}


def regime_courant() -> str:
    """Régime de la séance la plus récente, ou « indetermine »."""
    if "courant" in _MEMO:
        return _MEMO["courant"]
    try:
        serie = classer()
        valeur = str(serie.iloc[-1]) if len(serie) else "indetermine"
    except Exception:
        valeur = "indetermine"
    _MEMO["courant"] = valeur
    return valeur


# REPLI SUR LA MESURE GLOBALE — désarmé, et voici l'arbitrage.
#
# Un régime trop peu peuplé ne peut pas être mesuré séparément. Deux lectures
# s'affrontent :
#
#   ARMÉ   — la mesure tous régimes confondus porte aussi sur ces séances, et
#            elle est défavorable ; conseiller là où l'on ne mesure rien tout
#            en s'abstenant là où l'on mesure mal reviendrait à ne parler que
#            là où l'on est le plus ignorant. Cohérent, mais mesuré au
#            2026-07-31 : l'avis serait suspendu 100 % des séances, c'est-à-dire
#            que la plateforme cesserait d'émettre le moindre avis directionnel.
#
#   DÉSARMÉ — on ne s'abstient que là où la mesure PENCHE défavorablement.
#            Mesuré au 2026-07-31 : 88 % des séances (« ordinaire » et
#            « tendu »), l'avis ne subsistant qu'en marché calme.
#
# Ce n'est pas un réglage technique : c'est le choix entre un outil qui se
# tait presque toujours et un outil qui se tait toujours. Il revient au
# lecteur, pas au code — d'où une constante nommée plutôt qu'un choix enfoui.
REPLI_SUR_MESURE_GLOBALE = False

# SEUIL DE MATÉRIALITÉ de la suspension prudentielle — décision utilisateur du
# 2026-08-27, au jalon de reconfirmation. La prudence suspendait dès que l'IC
# passait SOUS ZÉRO ; or en marché ordinaire — le régime le plus fréquent —
# l'IC est retombé à −0,006 sur 18 observations indépendantes : un zéro de
# bruit. Suspendre là-dessus ne protégeait de rien et coûtait TOUTES les
# occasions. La prudence exige désormais une inclinaison MATÉRIELLE : sous une
# vingtaine d'observations indépendantes, un |IC| inférieur à 0,05 est
# indiscernable de zéro. Le chemin DÉMONTRÉ (inversion confirmée par la purge)
# reste inchangé, quelle que soit l'amplitude — une preuve n'a pas de seuil.
# Auto-réversible : si l'ordinaire replonge sous ce seuil, il se re-suspend
# tout seul à l'arbitrage suivant.
SEUIL_PRUDENTIEL_IC = -0.05


def calibrer(horizon: int = 20, ecrire: bool = True) -> dict:
    """Décide, régime par régime, si le verdict directionnel doit être suspendu.

    DEUX MOTIFS DE SUSPENDRE, ET ILS NE SE VALENT PAS.

    1. DÉMONTRÉ — la mesure purgée établit que le classement est inversé dans
       ce régime. C'est une preuve, au sens statistique du terme.

    2. PRUDENTIEL — l'IC mesuré penche MATÉRIELLEMENT du mauvais côté
       (sous `SEUIL_PRUDENTIEL_IC`) sans que la preuve soit faite. On suspend
       quand même, et c'est un choix de POSTURE, pas une conclusion de
       mesure : s'abstenir ne coûte qu'une occasion manquée, suivre un avis
       dont rien n'établit la valeur coûte le spread, le portage et le
       risque. Mais un IC dans la bande de bruit autour de zéro ne penche
       pas : il ne dit rien, et « rien » ne justifie plus une suspension
       (décision du 2026-08-27, voir le seuil).

    Les deux motifs sont écrits dans la sortie et distingués à l'affichage :
    présenter une prudence comme une démonstration serait exactement le genre
    d'affirmation non vérifiée que ce projet s'interdit.

    ON NE RETOURNE JAMAIS L'AVIS. Retourner reviendrait à parier que l'effet
    persiste — or il repose sur une dizaine d'observations réellement
    indépendantes, et une seule période agitée peut le porter. Suspendre ne
    coûte qu'une occasion manquée ; retourner à tort coûte de l'argent, et sur
    la foi d'un résultat que ce projet lui-même juge à confirmer.

    L'inversion n'est pas abandonnée pour autant : la note retournée est
    journalisée comme CANDIDATE. Si elle fait ses preuves sur des données
    fraîches, elle gagnera sa place — comme toute brique ici.

    UN RÉGIME NON MESURABLE : voir `REPLI_SUR_MESURE_GLOBALE` ci-dessus. Le
    réglage est désarmé par défaut, et ce n'est pas une hésitation technique
    mais une décision de produit qui appartient au lecteur.
    """
    analyse = analyser(horizon=horizon)
    verdict = {"horizon": horizon, "suspendus": [], "detail": {},
               "mesurable": bool(analyse.get("mesurable"))}

    # Ce que dit la mesure TOUS RÉGIMES CONFONDUS, obtenue par la MÊME purge
    # que les mesures par régime : elle sert de repli pour les régimes trop peu
    # peuplés pour être jugés séparément.
    ic_global = None
    try:
        globale = validation.ic_purge(decision._evaluer_journal(horizon=horizon),
                                      horizon=horizon)
        ic_global = globale.get("ic_moyen")
    except Exception:
        ic_global = None
    globale_defavorable = ic_global is not None and float(ic_global) < 0
    verdict["ic_global"] = ic_global

    for nom, entree in (analyse.get("par_regime") or {}).items():
        purge = entree.get("purge") or {}
        ic = purge.get("ic_moyen")
        mesurable = ic is not None
        confirme = str(purge.get("verdict", "")).endswith("confirmé")
        demontre = confirme and mesurable and float(ic) < 0
        # Prudence : la mesure penche MATÉRIELLEMENT du mauvais côté sans
        # trancher. On s'abstient, sans prétendre avoir démontré quoi que ce
        # soit — et un IC dans la bande de bruit ne suspend plus rien.
        prudentiel = mesurable and not demontre \
            and float(ic) <= SEUIL_PRUDENTIEL_IC
        if REPLI_SUR_MESURE_GLOBALE and not mesurable and globale_defavorable:
            prudentiel = True
        verdict["detail"][nom] = {
            "confirme": confirme,
            "mesurable": mesurable,
            "inverse": demontre,
            "motif": ("démontré" if demontre
                      else "prudentiel" if prudentiel else None),
            "ic_purge": ic,
            "part_concluante_%": purge.get("part_concluante_%"),
            "obs_independantes": purge.get("obs_par_echantillonnage"),
        }
        if demontre or prudentiel:
            verdict["suspendus"].append(nom)

    demontres = [r for r, d in verdict["detail"].items()
                 if d["motif"] == "démontré"]
    prudents = [r for r, d in verdict["detail"].items()
                if d["motif"] == "prudentiel"]
    morceaux = []
    if demontres:
        morceaux.append(
            "Dans " + ", ".join(ETIQUETTES.get(r, r) for r in demontres)
            + ", le classement a été mesuré INVERSÉ sans recouvrement : l'avis "
              "directionnel y est suspendu. Il n'est pas retourné — l'effet "
              "repose sur trop peu d'épisodes distincts pour qu'on parie "
              "dessus.")
    if prudents:
        morceaux.append(
            "Dans " + ", ".join(ETIQUETTES.get(r, r) for r in prudents)
            + ", la mesure penche du mauvais côté sans le démontrer. L'avis y "
              "est suspendu par PRUDENCE, pas par démonstration : s'abstenir "
              "ne coûte qu'une occasion manquée, tandis que suivre un avis "
              "dont rien n'établit la valeur coûte le spread, le portage et "
              "le risque.")
    verdict["lecture"] = (" ".join(morceaux) if morceaux else
                          "Aucun régime ne justifie de suspendre l'avis "
                          "directionnel.")

    if ecrire:
        VERDICT_PATH.parent.mkdir(parents=True, exist_ok=True)
        VERDICT_PATH.write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
    return verdict


def charger_verdict() -> dict:
    if "verdict" in _MEMO:
        return _MEMO["verdict"]
    try:
        _MEMO["verdict"] = json.loads(VERDICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        _MEMO["verdict"] = {"suspendus": []}
    return _MEMO["verdict"]


def avis_suspendu() -> dict | None:
    """Le régime courant impose-t-il de suspendre l'avis ? None sinon."""
    courant = regime_courant()
    if courant in (charger_verdict().get("suspendus") or []):
        detail = (charger_verdict().get("detail") or {}).get(courant, {})
        return {"regime": courant, **detail}
    return None
