"""Le spread MESURÉ depuis les barres, au lieu d'une table supposée.

CE QUE ÇA REMPLACE. `couts.py` porte une table de spreads codée en dur, des
« ordres de grandeur constatés ». Or l'estimateur de Roll (1984) permet de
MESURER le spread effectif depuis les barres 5 min qu'on archive déjà : quand
les transactions rebondissent entre le prix acheteur et le prix vendeur, deux
variations consécutives sont négativement corrélées, et l'ampleur de cette
anti-corrélation révèle l'écart. Sondé le 2026-08-01 avant d'écrire ce module :

    AAPL      mesuré 0,0395 %   table 0,040 %   — accord quasi parfait
    EURUSD=X  mesuré 0,0070 %   table 0,015 %   — la table SURESTIME ×2
    GC=F      mesuré 0,0396 %   table 0,070 %   — la table SURESTIME ×2
    MC.PA     covariance positive : indétectable — repli table obligatoire

L'accord sur AAPL crédibilise l'estimateur ; les écarts sur le forex et l'or
signifient que des idées sont aujourd'hui écartées À TORT par le filtre de
coût, sur la foi d'un chiffre inventé deux fois trop grand.

COMMENT ON LE REND ROBUSTE — l'estimateur brut est bruité, trois défenses :

1. par SÉANCE TERMINÉE uniquement, comme la volatilité réalisée : une séance
   partielle donnerait une covariance sur trois écarts ;
2. la valeur de production est la MÉDIANE des estimations quotidiennes, jamais
   une estimation isolée — une séance aberrante ne déplace pas une médiane ;
3. quand la covariance est positive (marché en tendance intrajournalière, ou
   cotations au milieu de fourchette), l'estimateur ne voit rien : on le DIT
   (`mesurable: False`) et on retombe sur la table. Un repli assumé vaut mieux
   qu'un chiffre extorqué à des données qui ne le contiennent pas.

DEUX LIMITES À CONNAÎTRE, constatées dans les deux sens à l'amorçage.

Vers le bas : si une source cote des milieux de fourchette lissés, le rebond
disparaît et l'estimation est un plancher (cas MC.PA, covariance positive).

Vers le haut : sur des barres de 5 minutes, l'anti-corrélation capte le rebond
acheteur-vendeur PLUS la réversion transitoire des prix après un ordre — la
trace de l'impact, pas seulement l'écart coté. Mesuré à l'amorçage : Samsung
×7,5 la table, TSLA ×4, KOSPI ×12,6, alors que leurs écarts cotés sont bien
plus serrés. Pour un modèle de COÛT c'est défendable — cette réversion est un
vrai risque d'exécution au marché — mais c'est un majorant, pas « le spread ».

D'où la borne dans les deux sens (¼× à 4× la table) : la mesure affine l'ordre
de grandeur, elle n'a pas le droit de le renverser silencieusement. Se tromper
vers le haut sur le coût est le sens prudent — le même choix que les
corrélations de stress du module de concentration.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from marketlab import config, intraday

RELEVE_PATH = config.DATA_DIR / "spreads_mesures.csv"
COLONNES = ["date", "symbole", "interval", "spread_pct", "n_ecarts"]

# Écarts de prix minimum dans une séance pour tenter l'estimation. Une
# covariance sur moins de 50 points décrit le hasard de la séance.
ECARTS_MIN_JOUR = 50

# Séances d'estimation minimum avant que la médiane ne PRIME sur la table.
JOURS_MIN_MEDIANE = 5

# Fenêtre de la médiane, en séances estimées les plus récentes.
FENETRE_MEDIANE = 30

_MEMO: dict = {}


# ---------------------------------------------------------------------------
# L'estimateur
# ---------------------------------------------------------------------------

def estimer_roll(closes: pd.Series) -> dict:
    """Spread effectif de Roll sur les clôtures d'UNE séance, en % du cours.

    spread = 2·√(−cov(Δp_t, Δp_{t+1})) — le rebond entre prix acheteur et
    vendeur rend deux variations consécutives négativement corrélées, et
    l'ampleur de cette anti-corrélation est exactement l'écart.
    """
    c = pd.to_numeric(closes, errors="coerce").dropna()
    dp = c.diff().dropna()
    if len(dp) < ECARTS_MIN_JOUR:
        return {"mesurable": False,
                "raison": f"{len(dp)} écart(s), {ECARTS_MIN_JOUR} requis"}
    cov = float(np.cov(dp.iloc[:-1], dp.iloc[1:])[0, 1])
    if cov >= 0:
        return {"mesurable": False, "raison": "covariance sérielle positive"}
    prix = float(c.median())
    if prix <= 0:
        return {"mesurable": False, "raison": "prix non exploitable"}
    return {"mesurable": True,
            "spread_pct": float(2 * np.sqrt(-cov) / prix * 100),
            "n_ecarts": int(len(dp))}


def releve_du_magasin(symbole: str,
                      interval: str = intraday.INTERVALLE_DEFAUT,
                      aujourdhui: dt.date | None = None) -> pd.DataFrame:
    """Estimation par séance TERMINÉE, depuis les barres déjà archivées.

    Aucun accès réseau : la capture des barres est le travail du relevé de
    volatilité, ce module ne fait que relire le magasin. La séance en cours
    est exclue pour la même raison qu'en volatilité — partielle, elle
    produirait une estimation sur trois écarts qui resterait à vie dans un
    relevé immuable.
    """
    barres = intraday.lire(symbole, interval)
    if barres.empty or "close" not in barres.columns:
        return pd.DataFrame(columns=COLONNES)
    limite = (aujourdhui or dt.datetime.now(dt.timezone.utc).date()).isoformat()

    lignes = []
    for jour, part in barres.groupby(pd.DatetimeIndex(barres.index).normalize()):
        date = pd.Timestamp(jour).date().isoformat()
        if date >= limite:
            continue
        e = estimer_roll(part["close"])
        if not e["mesurable"]:
            continue
        lignes.append({"date": date, "symbole": symbole, "interval": interval,
                       "spread_pct": round(e["spread_pct"], 6),
                       "n_ecarts": e["n_ecarts"]})
    if not lignes:
        return pd.DataFrame(columns=COLONNES)
    return pd.DataFrame(lignes)[COLONNES]


# ---------------------------------------------------------------------------
# Relevé persisté — même discipline que la volatilité réalisée
# ---------------------------------------------------------------------------

def charger_releve() -> pd.DataFrame:
    if not RELEVE_PATH.exists():
        return pd.DataFrame(columns=COLONNES)
    try:
        df = pd.read_csv(RELEVE_PATH)
    except Exception:
        return pd.DataFrame(columns=COLONNES)
    for col in COLONNES:
        if col not in df.columns:
            df[col] = np.nan
    return df[COLONNES]


def fusionner(ancien: pd.DataFrame, nouveau: pd.DataFrame) -> pd.DataFrame:
    """Union immuable : une séance déjà estimée n'est pas réécrite.

    L'historique reste stable et le fichier ne bouge que quand une vraie
    séance s'ajoute — pas un commit par passage du workflow.
    """
    cadres = [c for c in (ancien, nouveau) if c is not None and not c.empty]
    if not cadres:
        return pd.DataFrame(columns=COLONNES)
    fusion = pd.concat(cadres, ignore_index=True)[COLONNES]
    fusion = fusion.drop_duplicates(subset=["date", "symbole", "interval"],
                                    keep="first")
    return fusion.sort_values(["date", "symbole"]).reset_index(drop=True)


def mettre_a_jour_releve(symboles: list[str] | None = None,
                         interval: str = intraday.INTERVALLE_DEFAUT,
                         ecrire: bool = True) -> dict:
    """Estime les séances terminées de tous les titres et les conserve.

    Ne lève jamais : un titre sans barres est simplement absent du relevé.
    """
    symboles = list(symboles or config.SUIVIS)
    lignes = []
    for sym in symboles:
        try:
            r = releve_du_magasin(sym, interval)
        except Exception:
            continue
        if not r.empty:
            lignes.append(r)
    nouveau = (pd.concat(lignes, ignore_index=True) if lignes
               else pd.DataFrame(columns=COLONNES))
    ancien = charger_releve()
    fusion = fusionner(ancien, nouveau)
    if ecrire:
        RELEVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fusion.to_csv(RELEVE_PATH, index=False, float_format="%.10g")
        _MEMO.clear()          # la médiane doit voir les nouvelles séances
    return {"titres": int(nouveau["symbole"].nunique()) if not nouveau.empty else 0,
            "ajoutees": len(fusion) - len(ancien),
            "total": len(fusion)}


def spread_median(symbole: str,
                  interval: str = intraday.INTERVALLE_DEFAUT) -> dict | None:
    """Valeur de production : médiane des dernières séances estimées.

    None tant que moins de `JOURS_MIN_MEDIANE` séances sont estimées — le
    consommateur retombe alors sur sa table. Jamais une estimation isolée :
    une séance aberrante ne déplace pas une médiane, elle déplacerait une
    moyenne.
    """
    if "releve" not in _MEMO:
        _MEMO["releve"] = charger_releve()
    releve = _MEMO["releve"]
    if releve.empty:
        return None
    part = releve[(releve["symbole"] == symbole)
                  & (releve["interval"] == interval)].sort_values("date")
    part = part.tail(FENETRE_MEDIANE)
    if len(part) < JOURS_MIN_MEDIANE:
        return None
    valeurs = pd.to_numeric(part["spread_pct"], errors="coerce").dropna()
    if len(valeurs) < JOURS_MIN_MEDIANE:
        return None
    return {"spread_pct": float(valeurs.median()),
            "n_seances": int(len(valeurs)),
            "derniere": str(part["date"].iloc[-1])}


# ---------------------------------------------------------------------------
# Sauts contre diffusion — la part de volatilité qui traverse les stops
# ---------------------------------------------------------------------------
#
# Un stop protège contre la dérive CONTINUE : le cours passe par tous les prix
# intermédiaires, l'ordre s'exécute au niveau demandé. Un SAUT ne passe par
# rien — annonce, trou de liquidité — et l'exécution se fait de l'autre côté,
# plus loin que prévu. La variation bipower (Barndorff-Nielsen & Shephard,
# 2004) sépare les deux : robuste aux sauts, elle estime la seule composante
# continue, et l'écart RV − BV mesure ce que les sauts ont apporté.
#
#     part de saut = max(0, RV − BV) / RV
#
# Un actif à forte part de saut a des stops moins fiables que sa volatilité ne
# le laisse croire — c'est une caractéristique STRUCTURELLE, mesurable, sans
# aucune hypothèse sur l'avenir. Le dimensionnement la consomme.

SAUTS_PATH = config.DATA_DIR / "sauts_mesures.csv"
COLONNES_SAUTS = ["date", "symbole", "interval", "rv", "bv", "part_saut"]
SEANCES_MIN_SAUTS = 10        # séances estimées avant qu'une médiane ait un sens


def variation_bipower(closes: pd.Series) -> dict:
    """RV, BV et part de saut d'UNE séance."""
    c = pd.to_numeric(closes, errors="coerce").dropna()
    r = np.log(c[c > 0]).diff().dropna()
    if len(r) < ECARTS_MIN_JOUR:
        return {"mesurable": False}
    rv = float((r ** 2).sum())
    bv = float((np.pi / 2) * (r.abs() * r.abs().shift(1)).dropna().sum())
    if rv <= 0:
        return {"mesurable": False}
    return {"mesurable": True, "rv": rv, "bv": bv,
            "part_saut": max(0.0, rv - bv) / rv}


def releve_sauts_du_magasin(symbole: str,
                            interval: str = intraday.INTERVALLE_DEFAUT,
                            aujourdhui: dt.date | None = None) -> pd.DataFrame:
    """Part de saut par séance TERMINÉE, depuis les barres archivées."""
    barres = intraday.lire(symbole, interval)
    if barres.empty or "close" not in barres.columns:
        return pd.DataFrame(columns=COLONNES_SAUTS)
    limite = (aujourdhui or dt.datetime.now(dt.timezone.utc).date()).isoformat()
    lignes = []
    for jour, part in barres.groupby(pd.DatetimeIndex(barres.index).normalize()):
        date = pd.Timestamp(jour).date().isoformat()
        if date >= limite:
            continue
        e = variation_bipower(part["close"])
        if not e["mesurable"]:
            continue
        lignes.append({"date": date, "symbole": symbole, "interval": interval,
                       "rv": e["rv"], "bv": e["bv"],
                       "part_saut": round(e["part_saut"], 4)})
    if not lignes:
        return pd.DataFrame(columns=COLONNES_SAUTS)
    return pd.DataFrame(lignes)[COLONNES_SAUTS]


def charger_sauts() -> pd.DataFrame:
    if not SAUTS_PATH.exists():
        return pd.DataFrame(columns=COLONNES_SAUTS)
    try:
        df = pd.read_csv(SAUTS_PATH)
    except Exception:
        return pd.DataFrame(columns=COLONNES_SAUTS)
    for col in COLONNES_SAUTS:
        if col not in df.columns:
            df[col] = np.nan
    return df[COLONNES_SAUTS]


def mettre_a_jour_sauts(symboles: list[str] | None = None,
                        interval: str = intraday.INTERVALLE_DEFAUT,
                        ecrire: bool = True) -> dict:
    """Accumule la part de saut, union immuable — même contrat que le spread."""
    symboles = list(symboles or config.SUIVIS)
    lignes = []
    for sym in symboles:
        try:
            r = releve_sauts_du_magasin(sym, interval)
        except Exception:
            continue
        if not r.empty:
            lignes.append(r)
    nouveau = (pd.concat(lignes, ignore_index=True) if lignes
               else pd.DataFrame(columns=COLONNES_SAUTS))
    ancien = charger_sauts()
    cadres = [c for c in (ancien, nouveau) if not c.empty]
    fusion = (pd.concat(cadres, ignore_index=True)[COLONNES_SAUTS]
              .drop_duplicates(subset=["date", "symbole", "interval"],
                               keep="first")
              .sort_values(["date", "symbole"]).reset_index(drop=True)
              if cadres else pd.DataFrame(columns=COLONNES_SAUTS))
    if ecrire:
        SAUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fusion.to_csv(SAUTS_PATH, index=False, float_format="%.10g")
        _MEMO.pop("sauts", None)
    return {"titres": int(nouveau["symbole"].nunique()) if not nouveau.empty else 0,
            "ajoutees": len(fusion) - len(ancien), "total": len(fusion)}


def part_sauts(symbole: str,
               interval: str = intraday.INTERVALLE_DEFAUT) -> dict | None:
    """Part de saut MÉDIANE d'un actif. None sous le seuil de séances.

    La médiane, pour la même raison que le spread : une séance d'annonce ne
    doit pas définir la structure d'un actif — mais si la MOITIÉ des séances
    sont sauteuses, c'est bien la structure.
    """
    if "sauts" not in _MEMO:
        _MEMO["sauts"] = charger_sauts()
    releve = _MEMO["sauts"]
    if releve.empty:
        return None
    part = releve[(releve["symbole"] == symbole)
                  & (releve["interval"] == interval)]
    valeurs = pd.to_numeric(part["part_saut"], errors="coerce").dropna()
    if len(valeurs) < SEANCES_MIN_SAUTS:
        return None
    return {"part_saut": float(valeurs.median()),
            "n_seances": int(len(valeurs))}
