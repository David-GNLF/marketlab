"""Moteurs fondamentaux par classe d'actif : ce qui fait VRAIMENT bouger
les devises et les matières premières.

Quatre outils, chacun adossé à une relation économique documentée :

- **Différentiels de taux (forex)** — les capitaux vont vers la devise qui
  rémunère le mieux : l'écart de taux directeurs entre les deux devises d'une
  paire, et son évolution, sont le moteur n°1 des changes. Sources : Fed,
  BCE, SONIA quotidiens ; autres banques centrales via l'OCDE (mensuel).
- **Taux réels + dollar (métaux précieux)** — l'or ne verse pas d'intérêts :
  son coût d'opportunité est le taux RÉEL. La relation inverse or/taux réels
  US (TIPS 10 ans) est la plus documentée des métaux précieux.
- **Structure à terme (matières)** — l'écart entre le contrat proche et un
  contrat éloigné : une backwardation (différé moins cher) signale une
  tension physique immédiate et un portage POSITIF pour l'acheteur ; un
  contango marqué, l'inverse.
- **Baromètres cross-asset** — cuivre/or (croissance contre peur) et AUD/JPY
  (appétit pour le risque) : des confirmations croisées, calculées depuis
  les données déjà disponibles.
"""

import datetime as dt

import pandas as pd

from marketlab import config
from marketlab.data import fred, get_ohlcv

# --- Taux directeurs par devise (chaînes de repli FRED) ----------------------

TAUX_DEVISES = {
    "USD": ["DFF"],                      # Fed funds effectif, quotidien
    "EUR": ["ECBDFR"],                   # BCE facilité de dépôt, quotidien
    "GBP": ["IUDSOIA"],                  # SONIA, quotidien
    "JPY": ["IRSTCI01JPM156N"],          # OCDE mensuel
    "CHF": ["IR3TIB01CHM156N", "IRSTCI01CHM156N"],
    "AUD": ["IRSTCI01AUM156N"],
    "CAD": ["IRSTCI01CAM156N"],
    "NZD": ["IRSTCI01NZM156N"],
}


def _taux(devise: str) -> dict:
    """Dernier taux connu d'une devise, avec sa fraîcheur."""
    for serie in TAUX_DEVISES.get(devise, []):
        try:
            s = fred.get_series(serie, lookback_years=3)
            if s.empty:
                continue
            date = s.index[-1]
            age_jours = (pd.Timestamp.now() - date).days
            il_y_a_6m = s[s.index <= date - pd.DateOffset(months=6)]
            return {
                "devise": devise, "serie": serie,
                "taux": round(float(s.iloc[-1]), 2),
                "date": date.strftime("%Y-%m-%d"),
                "taux_6m_avant": (round(float(il_y_a_6m.iloc[-1]), 2)
                                  if len(il_y_a_6m) else None),
                "date_ancienne": age_jours > 120,
            }
        except Exception:
            continue
    raise RuntimeError(f"aucune série de taux disponible pour {devise}")


def differentiel_taux(paire: str) -> dict:
    """Carry d'une paire forex : écart de taux base − cotée, et sa dynamique.

    Un différentiel positif rémunère le porteur de la paire ; un écart qui
    S'ÉLARGIT attire les capitaux vers la devise de base.
    """
    propre = paire.replace("=X", "")
    if len(propre) != 6:
        raise RuntimeError(f"{paire} n'est pas une paire forex")
    base, cotee = propre[:3], propre[3:]
    t_base, t_cotee = _taux(base), _taux(cotee)

    diff = round(t_base["taux"] - t_cotee["taux"], 2)
    diff_6m = (round(t_base["taux_6m_avant"] - t_cotee["taux_6m_avant"], 2)
               if t_base["taux_6m_avant"] is not None
               and t_cotee["taux_6m_avant"] is not None else None)
    dynamique = None if diff_6m is None else round(diff - diff_6m, 2)

    sens = "porteur pour la paire" if diff > 0.25 else \
        "contre la paire" if diff < -0.25 else "à peu près neutre"
    evolution = ("" if dynamique is None else
                 " et l'écart s'élargit en faveur de la devise de base"
                 if dynamique > 0.1 else
                 " mais l'écart se resserre" if dynamique < -0.1 else
                 ", écart stable sur 6 mois")
    avertissements = [f"taux {t['devise']} daté du {t['date']}"
                      for t in (t_base, t_cotee) if t["date_ancienne"]]
    return {
        "outil": "différentiel de taux (carry)",
        "paire": paire,
        "taux": {base: t_base["taux"], cotee: t_cotee["taux"]},
        "differentiel_pts": diff,
        "variation_6m_pts": dynamique,
        "lecture": (f"{base} rémunère {t_base['taux']} % contre "
                    f"{t_cotee['taux']} % pour {cotee} : carry "
                    f"{diff:+.2f} pt, {sens}{evolution}."),
        "avertissements": avertissements,
    }


# --- Taux réels et dollar (métaux précieux) ----------------------------------

def moteur_metaux() -> dict:
    """Le couple taux réels US / dollar, coût d'opportunité de l'or."""
    reels = fred.get_series("DFII10", lookback_years=2)
    dollar = fred.get_series("DTWEXBGS", lookback_years=2)
    r_now = float(reels.iloc[-1])
    r_3m = float(reels[reels.index <= reels.index[-1]
                       - pd.DateOffset(months=3)].iloc[-1])
    d_now = float(dollar.iloc[-1])
    d_3m = float(dollar[dollar.index <= dollar.index[-1]
                        - pd.DateOffset(months=3)].iloc[-1])
    var_reels = round(r_now - r_3m, 2)
    var_dollar = round((d_now / d_3m - 1) * 100, 1)

    # corrélation observée or / taux réels (variations hebdomadaires, 2 ans)
    try:
        or_ = get_ohlcv("GC=F", lookback_days=730)["close"].resample("W").last()
        reels_h = reels.resample("W").last().reindex(or_.index, method="ffill")
        correlation = round(float(or_.pct_change().corr(reels_h.diff())), 2)
    except Exception:
        correlation = None

    vents = []
    if var_reels < -0.15:
        vents.append("taux réels en BAISSE (porteur pour l'or)")
    elif var_reels > 0.15:
        vents.append("taux réels en HAUSSE (vent contraire pour l'or)")
    if var_dollar < -1.5:
        vents.append("dollar en repli (porteur)")
    elif var_dollar > 1.5:
        vents.append("dollar en hausse (contraire)")
    return {
        "outil": "taux réels + dollar",
        "taux_reel_10a_%": round(r_now, 2),
        "variation_3m_pts": var_reels,
        "dollar_variation_3m_%": var_dollar,
        "correlation_or_taux_reels_2ans": correlation,
        "lecture": (f"taux réel US 10 ans {r_now:.2f} % ({var_reels:+.2f} pt "
                    f"sur 3 mois), dollar {var_dollar:+.1f} % sur 3 mois"
                    + (" — " + " ; ".join(vents) if vents else
                       " — pas de vent dominant pour les métaux précieux")
                    + (f". Corrélation or/taux réels observée : {correlation}."
                       if correlation is not None else ".")),
    }


# --- Structure à terme (matières) --------------------------------------------

# lettres de mois cotées par produit (racine, place)
_CONTRATS = {
    "CL=F": ("CL", "NYM", "FGHJKMNQUVXZ"),
    "NG=F": ("NG", "NYM", "FGHJKMNQUVXZ"),
    "GC=F": ("GC", "CMX", "GJMQVZ"),
    "SI=F": ("SI", "CMX", "HKNUZ"),
    "HG=F": ("HG", "CMX", "FGHJKMNQUVXZ"),
    "CC=F": ("CC", "NYB", "HKNUZ"),
    "CT=F": ("CT", "NYB", "HKNZ"),
    "KC=F": ("KC", "NYB", "HKNUZ"),
    "ZW=F": ("ZW", "CBT", "HKNUZ"),
}
_LETTRE_MOIS = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
                "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def _contrat_differe(symbole: str, mois_min: int = 4,
                     mois_max: int = 10) -> tuple[str, int] | None:
    """Premier contrat différé disponible entre mois_min et mois_max devant."""
    if symbole not in _CONTRATS:
        return None
    racine, place, lettres = _CONTRATS[symbole]
    aujourdhui = dt.date.today()
    candidats = []
    for delta in range(mois_min, mois_max + 1):
        annee = aujourdhui.year + (aujourdhui.month + delta - 1) // 12
        mois = (aujourdhui.month + delta - 1) % 12 + 1
        lettre = next((l for l, m in _LETTRE_MOIS.items() if m == mois), None)
        if lettre and lettre in lettres:
            candidats.append((f"{racine}{lettre}{str(annee)[2:]}.{place}", delta))
    for sym, delta in candidats:
        try:
            df = get_ohlcv(sym, lookback_days=60)
            if len(df) >= 10:
                return sym, delta
        except Exception:
            continue
    return None


def structure_terme(symbole: str) -> dict:
    """Contango ou backwardation : le portage d'une position matière."""
    trouve = _contrat_differe(symbole)
    if trouve is None:
        raise RuntimeError(f"pas de contrat différé exploitable pour {symbole}")
    sym_differe, ecart_mois = trouve
    # lookback profond : déjà en cache partout ailleurs, zéro requête en plus
    front = float(get_ohlcv(symbole, lookback_days=1825)["close"].iloc[-1])
    differe = float(get_ohlcv(sym_differe, lookback_days=60)["close"].iloc[-1])
    base_pct = (differe / front - 1) * 100
    base_annualisee = base_pct * 12 / ecart_mois

    if base_annualisee < -3:
        etat = "BACKWARDATION"
        lecture = ("le différé cote sous le comptant : tension physique "
                   "immédiate, portage POSITIF pour l'acheteur — structure "
                   "haussière")
    elif base_annualisee > 6:
        etat = "CONTANGO marqué"
        lecture = ("le différé cote nettement au-dessus : marché bien "
                   "approvisionné, le portage COÛTE à l'acheteur — vent "
                   "contraire pour une position longue durable")
    else:
        etat = "structure plate"
        lecture = "ni tension ni excédent notable : portage à peu près neutre"
    return {
        "outil": "structure à terme",
        "symbole": symbole,
        "contrat_differe": sym_differe,
        "ecart_mois": ecart_mois,
        "prix_front": round(front, 2),
        "prix_differe": round(differe, 2),
        "base_annualisee_%": round(base_annualisee, 1),
        "etat": etat,
        "lecture": (f"{etat} : {config.NOMS_ACTIFS.get(symbole, symbole)} "
                    f"comptant {front:,.2f} vs {sym_differe} {differe:,.2f} "
                    f"({base_annualisee:+.1f} %/an) — {lecture}."),
    }


# --- Baromètres cross-asset --------------------------------------------------

def barometres() -> dict:
    """Cuivre/or et AUD/JPY : deux jauges croisées de l'appétit pour le risque."""
    resultats, lectures = {}, []

    cuivre = get_ohlcv("HG=F", lookback_days=730)["close"]
    or_ = get_ohlcv("GC=F", lookback_days=730)["close"]
    ratio = (cuivre / or_).dropna()
    var_3m = float(ratio.iloc[-1] / ratio.iloc[-63] - 1) * 100
    pct = float((ratio < ratio.iloc[-1]).mean() * 100)
    resultats["cuivre_or"] = {"variation_3m_%": round(var_3m, 1),
                              "percentile_2ans": round(pct, 0)}
    lectures.append(
        f"cuivre/or {var_3m:+.1f} % sur 3 mois (percentile {pct:.0f}) : "
        + ("la croissance l'emporte sur la peur" if var_3m > 2 else
           "la peur l'emporte sur la croissance" if var_3m < -2 else
           "équilibre croissance/peur"))

    aud = get_ohlcv("AUDUSD=X", lookback_days=730)["close"]
    jpy = get_ohlcv("USDJPY=X", lookback_days=730)["close"]
    audjpy = (aud * jpy).dropna()
    var_aj = float(audjpy.iloc[-1] / audjpy.iloc[-63] - 1) * 100
    resultats["aud_jpy"] = {"niveau": round(float(audjpy.iloc[-1]), 2),
                            "variation_3m_%": round(var_aj, 1)}
    lectures.append(
        f"AUD/JPY {var_aj:+.1f} % sur 3 mois : "
        + ("les capitaux vont vers le risque" if var_aj > 2 else
           "fuite vers la sécurité" if var_aj < -2 else "flux neutres"))

    concordant = (var_3m > 2 and var_aj > 2) or (var_3m < -2 and var_aj < -2)
    return {
        "outil": "baromètres cross-asset",
        **resultats,
        "lecture": " ; ".join(lectures)
                   + (" — les deux baromètres CONCORDENT." if concordant
                      else " — signaux mitigés, pas de conclusion cross-asset."),
    }


# --- Répartiteur -------------------------------------------------------------

METAUX_PRECIEUX = {"GC=F", "SI=F"}


def moteurs(symbole: str) -> list[dict]:
    """Les moteurs fondamentaux pertinents pour UN actif donné."""
    sortie = []
    try:
        if symbole.endswith("=X"):
            sortie.append(differentiel_taux(symbole))
        elif symbole in METAUX_PRECIEUX:
            sortie.append(moteur_metaux())
            sortie.append(structure_terme(symbole))
        elif symbole.endswith("=F") and symbole in _CONTRATS:
            sortie.append(structure_terme(symbole))
    except RuntimeError as exc:
        sortie.append({"outil": "moteurs fondamentaux",
                       "lecture": f"indisponible : {str(exc)[:80]}"})
    return sortie
