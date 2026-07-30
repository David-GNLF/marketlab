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

import numpy as np
import pandas as pd

from marketlab import decision

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
    prouves = {nom: e for nom, e in par_regime.items()
               if isinstance(e.get("note"), dict)
               and e["note"].get("sens") in {"positif", "negatif", "négatif"}}
    if not prouves:
        return ("Aucun régime ne montre de capacité démontrée à classer les "
                "actifs, ni dans un sens ni dans l'autre. La moyenne nulle du "
                "bilan ne cachait donc pas deux comportements opposés : elle "
                "décrit bien l'absence de signal partout.")
    morceaux = []
    for nom, e in prouves.items():
        sens = e["note"]["sens"]
        episodes = e["note"].get("episodes_independants")
        morceaux.append(
            f"{ETIQUETTES.get(nom, nom)} : classement "
            f"{'correct' if sens == 'positif' else 'INVERSÉ'} "
            f"({e['n_dates']} dates, soit environ {episodes} épisode(s) de "
            f"marché réellement indépendant(s))")

    fin = ""
    if len(prouves) < len(par_regime):
        fin = (" Un effet présent dans un seul régime est exactement ce qu'une "
               "moyenne unique efface : le bilan d'ensemble ne le montrait pas.")
    # Le nombre d'épisodes indépendants est le seul chiffre qui dise si l'on
    # peut y croire. Une dizaine, c'est peu : le t affiché repose sur des
    # fenêtres qui se recouvrent largement, et une seule période agitée peut
    # porter tout le résultat.
    maigres = [e["note"].get("episodes_independants", 0) for e in prouves.values()]
    if maigres and min(maigres) < 15:
        fin += (" À manier avec prudence : le résultat ne repose que sur une "
                "poignée d'épisodes de marché distincts, et une seule période "
                "agitée peut le porter à elle seule. À reconfirmer quand "
                "l'historique aura grandi.")
    return " ; ".join(morceaux) + "." + fin
