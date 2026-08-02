"""Ce que le marché price : volatilité implicite, prime de variance, skew.

POURQUOI C'EST LA SEULE DONNÉE VRAIMENT NOUVELLE. Tout le reste de MarketLab
mesure le passé — cours, volatilité réalisée, spreads. Une chaîne d'options
contient autre chose : la volatilité que le marché PAIE pour se couvrir sur
l'avenir. C'est la prévision d'un adversaire qui met de l'argent derrière la
sienne. Sondé le 2026-08-01 avant d'écrire ce module : 20 échéances sur AAPL,
volatilité implicite renseignée sur 100 % des contrats — la donnée est là,
gratuite, et l'outil ne la regardait pas.

TROIS USAGES, DU PLUS IMMÉDIAT AU PLUS PATIENT.

1. LA PRIME DE VARIANCE DU MARCHÉ (VIX), mesurable AUJOURD'HUI sur six ans :
   le VIX est la volatilité implicite à 30 jours du S&P 500. La comparer à la
   volatilité ensuite RÉALISÉE mesure ce que les vendeurs d'assurance
   encaissent — la prime la mieux documentée de la littérature, persistante
   parce que c'est une prime d'assurance, pas une anomalie qui s'arbitre.

2. LE PORTRAIT PAR TITRE : IV à ~30 jours contre notre prévision (EWMA) et
   contre le réalisé récent, plus le skew — l'écart de prix entre la
   protection à la baisse et le pari à la hausse. Descriptif, sans promesse.

3. LE BANC D'ESSAI DIFFÉRÉ : nos prévisions de volatilité contre celle du
   marché, jugées au QLIKE sur le réalisé — mais seulement quand chaque
   instantané aura VÉCU ses 21 séances. D'où l'accumulation quotidienne :
   les chaînes d'options sont éphémères (Yahoo ne sert que l'instant), même
   contrainte que le calendrier économique. Sans relevé conservé, ce
   benchmark ne pourra jamais exister.

UNITÉS, LE PIÈGE À NE PAS ENTERRER. La volatilité réalisée du magasin
intrajournalier EXCLUT les sauts de nuit (choix documenté dans intraday.py).
L'implicite, elle, couvre tout — nuits et week-ends compris. Les comparer
directement biaiserait la prime vers le haut. Toutes les comparaisons de ce
module utilisent donc le réalisé CLÔTURE-À-CLÔTURE, qui couvre le même temps
que l'option.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from marketlab import config

RELEVE_PATH = config.DATA_DIR / "vol_implicite.csv"
COLONNES = ["date", "symbole", "jours_echeance", "iv_atm_pct", "skew_pts",
            "n_contrats"]

# Échéance visée : ~30 jours calendaires (l'horizon du VIX, la convention de
# place). En dessous de 7 jours, les options de très court terme portent un
# bruit de fin de vie qui n'a rien à voir avec une prévision.
JOURS_CIBLE = 30
JOURS_MIN = 7

# Une IV hors de cette plage est une donnée malade (cotation figée, division
# par un prix nul), pas une information.
IV_MIN_PCT, IV_MAX_PCT = 1.0, 300.0

SEANCES_REALISE = 21          # ~30 jours calendaires en séances
PERIODES_AN = 252

_MEMO: dict = {}


# ---------------------------------------------------------------------------
# Extraction — fonctions pures, testables sans réseau
# ---------------------------------------------------------------------------

def choisir_echeance(expirations: list[str],
                     aujourdhui: dt.date | None = None) -> str | None:
    """L'échéance la plus proche de 30 jours, jamais sous 7."""
    aujourdhui = aujourdhui or dt.date.today()
    candidates = []
    for e in expirations:
        try:
            jours = (dt.date.fromisoformat(str(e)) - aujourdhui).days
        except ValueError:
            continue
        if jours >= JOURS_MIN:
            candidates.append((abs(jours - JOURS_CIBLE), e))
    return min(candidates)[1] if candidates else None


def extraire_iv(calls: pd.DataFrame, puts: pd.DataFrame, spot: float) -> dict:
    """IV à la monnaie et skew, depuis une chaîne déjà téléchargée.

    ATM : médiane des 3 grèves les plus proches du cours, calls ET puts —
    la médiane parce qu'une cotation figée sur un contrat ne doit pas
    déplacer la mesure.

    Skew : IV du put à ~95 % du cours moins IV du call à ~105 %. Positif =
    la protection à la baisse se paie plus cher que le pari à la hausse —
    l'asymétrie de la peur, contrat par contrat.
    """
    if spot <= 0:
        return {"mesurable": False, "raison": "cours non exploitable"}

    def _propres(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "impliedVolatility" not in df.columns:
            return pd.DataFrame(columns=["strike", "iv_pct"])
        out = pd.DataFrame({
            "strike": pd.to_numeric(df["strike"], errors="coerce"),
            "iv_pct": pd.to_numeric(df["impliedVolatility"],
                                    errors="coerce") * 100,
        }).dropna()
        return out[(out["iv_pct"] >= IV_MIN_PCT) & (out["iv_pct"] <= IV_MAX_PCT)]

    c, p = _propres(calls), _propres(puts)
    if len(c) < 3 or len(p) < 3:
        return {"mesurable": False,
                "raison": f"trop peu de contrats sains ({len(c)} calls, "
                          f"{len(p)} puts)"}

    atm = pd.concat([
        c.assign(ecart=(c["strike"] - spot).abs()).nsmallest(3, "ecart"),
        p.assign(ecart=(p["strike"] - spot).abs()).nsmallest(3, "ecart"),
    ])["iv_pct"].median()

    put_bas = p.assign(ecart=(p["strike"] - spot * 0.95).abs()) \
               .nsmallest(1, "ecart")["iv_pct"]
    call_haut = c.assign(ecart=(c["strike"] - spot * 1.05).abs()) \
                 .nsmallest(1, "ecart")["iv_pct"]
    skew = float(put_bas.iloc[0] - call_haut.iloc[0]) \
        if len(put_bas) and len(call_haut) else None

    return {"mesurable": True, "iv_atm_pct": round(float(atm), 2),
            "skew_pts": round(skew, 2) if skew is not None else None,
            "n_contrats": int(len(c) + len(p))}


# ---------------------------------------------------------------------------
# Instantané quotidien accumulé — les chaînes sont éphémères
# ---------------------------------------------------------------------------

def instantane(symbole: str) -> dict | None:
    """Photographie du jour pour un titre. None si la chaîne est inexploitable.

    Réseau ICI et seulement ici : tout le reste du module relit le relevé.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(symbole)
        echeance = choisir_echeance(list(t.options))
        if echeance is None:
            return None
        chaine = t.option_chain(echeance)
        from marketlab.data import get_ohlcv
        spot = float(get_ohlcv(symbole, lookback_days=10)["close"].iloc[-1])
        e = extraire_iv(chaine.calls, chaine.puts, spot)
        if not e["mesurable"]:
            return None
        jours = (dt.date.fromisoformat(echeance) - dt.date.today()).days
        return {"date": dt.date.today().isoformat(), "symbole": symbole,
                "jours_echeance": jours, "iv_atm_pct": e["iv_atm_pct"],
                "skew_pts": e["skew_pts"], "n_contrats": e["n_contrats"]}
    except Exception:
        return None


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
    """Union immuable, le premier instantané fait foi — comme partout ici."""
    cadres = [c for c in (ancien, nouveau) if c is not None and not c.empty]
    if not cadres:
        return pd.DataFrame(columns=COLONNES)
    fusion = pd.concat(cadres, ignore_index=True)[COLONNES]
    fusion = fusion.drop_duplicates(subset=["date", "symbole"], keep="first")
    return fusion.sort_values(["date", "symbole"]).reset_index(drop=True)


def mettre_a_jour_releve(symboles: list[str] | None = None,
                         ecrire: bool = True) -> dict:
    """Photographie du jour pour les actions US. Ne lève jamais.

    Périmètre : les seules places où la sonde a prouvé la donnée. Étendre aux
    indices passerait par des trackers — plus tard, si le besoin se confirme.
    """
    symboles = list(symboles if symboles is not None else config.ACTIONS_US)
    lignes = [i for s in symboles if (i := instantane(s)) is not None]
    nouveau = (pd.DataFrame(lignes)[COLONNES] if lignes
               else pd.DataFrame(columns=COLONNES))
    ancien = charger_releve()
    fusion = fusionner(ancien, nouveau)
    if ecrire:
        RELEVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fusion.to_csv(RELEVE_PATH, index=False, float_format="%.10g")
        _MEMO.clear()
    return {"vus": len(lignes), "ajoutees": len(fusion) - len(ancien),
            "total": len(fusion)}


# ---------------------------------------------------------------------------
# Le réalisé qui couvre le même temps que l'option
# ---------------------------------------------------------------------------

def vol_cloture(closes: pd.Series, seances: int = SEANCES_REALISE) -> pd.Series:
    """Volatilité clôture-à-clôture annualisée en %, réalisée sur les
    `seances` séances SUIVANT chaque date — nuits et week-ends inclus, comme
    l'option la subit."""
    r = np.log(pd.to_numeric(closes, errors="coerce")).diff()
    avenir = r.shift(-1).iloc[::-1].rolling(seances).std().iloc[::-1]
    return avenir * np.sqrt(PERIODES_AN) * 100


# ---------------------------------------------------------------------------
# 1. La prime de variance du marché — mesurable aujourd'hui
# ---------------------------------------------------------------------------

def prime_variance_vix(jours: int = 1500) -> dict:
    """Le VIX contre la volatilité que le S&P 500 a ENSUITE réalisée.

    Chaque jour, le marché price 30 jours de volatilité future ; 21 séances
    plus tard, on sait ce qu'elle a vraiment été. L'écart est la prime que
    les acheteurs d'assurance paient — et sa persistance est la raison pour
    laquelle vendre de la volatilité rapporte longtemps puis ruine d'un coup.

    Les fenêtres se recouvrent : la moyenne naïve surestime la certitude,
    exactement le piège du bilan des verdicts. La médiane est donc AUSSI
    donnée sur des dates espacées de 21 séances (validation.dates_espacees),
    réellement indépendantes.
    """
    from marketlab import validation
    from marketlab.data import get_ohlcv
    try:
        vix = get_ohlcv("^VIX", lookback_days=jours)["close"]
        spx = get_ohlcv("^GSPC", lookback_days=jours)["close"]
    except Exception as exc:
        return {"mesurable": False, "raison": f"données absentes : {exc}"}

    realise = vol_cloture(spx)
    cadre = pd.DataFrame({"vix": vix, "realise": realise}).dropna()
    if len(cadre) < 100:
        return {"mesurable": False, "raison": f"{len(cadre)} jours exploitables"}
    cadre["prime_pts"] = cadre["vix"] - cadre["realise"]

    espacees = validation.dates_espacees(list(cadre.index),
                                         ecart=SEANCES_REALISE + 2)
    indep = cadre.loc[espacees, "prime_pts"]

    return {
        "mesurable": True,
        "n_jours": int(len(cadre)),
        "prime_mediane_pts": round(float(cadre["prime_pts"].median()), 2),
        "part_jours_positive_%": round(
            float((cadre["prime_pts"] > 0).mean()) * 100, 1),
        "n_independants": int(len(indep)),
        "prime_mediane_independante_pts": round(float(indep.median()), 2),
        "pire_episode_pts": round(float(cadre["prime_pts"].min()), 1),
        "lecture": (
            f"Sur {len(cadre)} séances, le VIX a coté en médiane "
            f"{cadre['prime_pts'].median():+.1f} points au-dessus de la "
            f"volatilité ensuite réalisée, et il était au-dessus "
            f"{(cadre['prime_pts'] > 0).mean() * 100:.0f} % du temps — c'est "
            f"la prime d'assurance, confirmée sur {len(indep)} fenêtres "
            f"réellement indépendantes ({indep.median():+.1f} pts). Le revers "
            f"est le pire épisode : {cadre['prime_pts'].min():+.0f} pts — "
            f"quand l'assurance sert, elle sert d'un coup. Vendre cette prime "
            f"sans en avoir les moyens est le métier le plus dangereux du "
            f"marché."),
    }


# ---------------------------------------------------------------------------
# 2. Le portrait par titre — pour la fiche
# ---------------------------------------------------------------------------

def synthese_titre(symbole: str, df: pd.DataFrame | None = None) -> dict | None:
    """IV du marché, notre prévision, le réalisé récent. None hors relevé.

    Aucun réseau : le relevé accumulé fournit l'IV, `df` (déjà chargé par la
    fiche) fournit les clôtures.
    """
    if "releve" not in _MEMO:
        _MEMO["releve"] = charger_releve()
    releve = _MEMO["releve"]
    part = releve[releve["symbole"] == symbole].sort_values("date")
    if part.empty:
        return None
    dernier = part.iloc[-1]

    sortie = {
        "date": str(dernier["date"]),
        "jours_echeance": int(dernier["jours_echeance"]),
        "iv_atm_pct": float(dernier["iv_atm_pct"]),
        "skew_pts": (float(dernier["skew_pts"])
                     if pd.notna(dernier["skew_pts"]) else None),
    }
    if df is not None and "close" in df.columns and len(df) > 60:
        closes = pd.to_numeric(df["close"], errors="coerce").dropna()
        r = np.log(closes).diff().dropna()
        realise = float(r.tail(SEANCES_REALISE).std() * np.sqrt(PERIODES_AN) * 100)
        from marketlab import forecast
        ewma = float(forecast.volatilite_ewma(r) * np.sqrt(PERIODES_AN) * 100)
        sortie["realise_21s_pct"] = round(realise, 1)
        sortie["notre_prevision_pct"] = round(ewma, 1)
        iv = sortie["iv_atm_pct"]
        sortie["lecture"] = (
            f"Le marché price {iv:.0f} % de volatilité annualisée à "
            f"{sortie['jours_echeance']} jours ; notre modèle (EWMA) prévoit "
            f"{ewma:.0f} %, et les 21 dernières séances ont réalisé "
            f"{realise:.0f} %. "
            + (f"Le skew de {sortie['skew_pts']:+.1f} pts dit que la "
               f"protection à la baisse se paie "
               f"{'plus' if sortie['skew_pts'] >= 0 else 'moins'} cher que le "
               f"pari à la hausse. " if sortie["skew_pts"] is not None else "")
            + "L'écart implicite−réalisé est en général une prime d'assurance, "
              "pas une prévision de tempête.")
    return sortie


# ---------------------------------------------------------------------------
# 3. Le banc d'essai différé — nos prévisions contre celles du marché
# ---------------------------------------------------------------------------

def comparer_previsionnistes(seances_min: int = 15) -> dict:
    """IV du marché contre notre EWMA, jugées au QLIKE sur le réalisé.

    Chaque instantané accumulé devient un duel… 21 séances plus tard, quand
    on sait ce que la volatilité a vraiment fait. Tant que moins de
    `seances_min` duels sont arrivés à maturité, le verdict est « pas encore
    mesurable » — le même contrat que HAR contre EWMA : aucune conclusion
    avant les preuves, mais l'infrastructure tourne et la conclusion viendra
    seule.
    """
    from marketlab import forecast, har
    from marketlab.data import get_ohlcv

    releve = charger_releve()
    if releve.empty:
        return {"mesurable": False, "raison": "aucun instantané accumulé"}

    duels = []
    for symbole, part in releve.groupby("symbole"):
        try:
            closes = get_ohlcv(symbole, lookback_days=400)["close"]
        except Exception:
            continue
        closes = pd.to_numeric(closes, errors="coerce").dropna()
        realise = vol_cloture(closes)
        r = np.log(closes).diff()
        for _, ligne in part.iterrows():
            date = pd.Timestamp(ligne["date"])
            passe = r[r.index <= date].dropna()
            if date not in realise.index or pd.isna(realise.loc[date]) \
                    or len(passe) < 60:
                continue          # fenêtre pas encore mûre, ou trop jeune
            ewma = float(forecast.volatilite_ewma(passe)
                         * np.sqrt(PERIODES_AN) * 100)
            duels.append({"symbole": symbole, "date": str(ligne["date"]),
                          "iv": float(ligne["iv_atm_pct"]), "ewma": ewma,
                          "realise": float(realise.loc[date])})
    if len(duels) < seances_min:
        return {"mesurable": False,
                "raison": f"{len(duels)} duel(s) arrivé(s) à maturité, "
                          f"{seances_min} requis — la conclusion viendra avec "
                          f"l'accumulation",
                "n_duels": len(duels)}

    d = pd.DataFrame(duels)
    vrai = (d["realise"] / 100) ** 2
    q_iv = har._qlike(vrai.to_numpy(), ((d["iv"] / 100) ** 2).to_numpy())
    q_ewma = har._qlike(vrai.to_numpy(), ((d["ewma"] / 100) ** 2).to_numpy())
    gagnant = "marché (IV)" if q_iv < q_ewma else "notre EWMA"
    return {"mesurable": True, "n_duels": int(len(d)),
            "qlike_iv": round(float(q_iv), 5),
            "qlike_ewma": round(float(q_ewma), 5),
            "gagnant": gagnant,
            "lecture": (f"Sur {len(d)} fenêtres arrivées à maturité, "
                        f"{gagnant} prévoit mieux la volatilité au sens du "
                        f"QLIKE. Si le marché gagne durablement, la voie "
                        f"honnête n'est pas de raffiner notre modèle : c'est "
                        f"d'utiliser SA prévision comme entrée.")}
