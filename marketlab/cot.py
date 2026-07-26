"""Rapports COT (Commitments of Traders) de la CFTC.

Chaque vendredi, la CFTC publie le positionnement du mardi sur les contrats à
terme américains : les **non-commerciaux** (spéculateurs — fonds, CTA) et les
**commerciaux** (professionnels du sous-jacent, qui se couvrent). Source :
API Socrata publique de la CFTC, sans clé.

Lecture retenue — le **COT index** : la position nette des spéculateurs,
normalisée entre son minimum et son maximum sur 3 ans (0 = jamais aussi
vendeurs, 100 = jamais aussi acheteurs). Deux usages complémentaires :

- **tendanciel** : des spéculateurs nettement acheteurs et qui renforcent
  accompagnent la tendance ;
- **contrarien aux extrêmes** : au-delà de ~85 (ou sous ~15), tout le monde
  est déjà du même côté — il ne reste plus grand monde pour pousser le prix,
  et le trade est dit « encombré ». C'est un signal de prudence, pas de
  retournement automatique.

Pour les paires cotées USD/XXX (JPY, CHF, CAD), le contrat CFTC porte sur la
devise étrangère : le positionnement est INVERSÉ pour raisonner dans le sens
de la paire.
"""

import json
import time

import pandas as pd
import requests

from marketlab import config

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
CACHE_TTL_H = 24
FENETRE_INDEX_SEMAINES = 156  # ~3 ans

# symbole MarketLab -> (code de marché CFTC, inverser le sens ?)
MARCHES = {
    "GC=F": ("088691", False),   # or, COMEX
    "SI=F": ("084691", False),   # argent
    "CL=F": ("067651", False),   # pétrole WTI, NYMEX
    "NG=F": ("023651", False),   # gaz naturel
    "HG=F": ("085692", False),   # cuivre
    "CC=F": ("073732", False),   # cacao, ICE US
    "CT=F": ("033661", False),   # coton n°2
    "KC=F": ("083731", False),   # café C
    "ZW=F": ("001602", False),   # blé SRW, CBOT
    "EURUSD=X": ("099741", False),
    "GBPUSD=X": ("096742", False),
    "AUDUSD=X": ("232741", False),
    "NZDUSD=X": ("112741", False),
    "USDJPY=X": ("097741", True),   # contrat = yen -> inverse de USD/JPY
    "USDCHF=X": ("092741", True),
    "USDCAD=X": ("090741", True),
    "BTCUSDT": ("133741", False),   # bitcoin CME
    "^GSPC": ("13874A", False),     # e-mini S&P 500
}
# NB : le Brent (BZ=F) se traite sur ICE Europe, hors périmètre CFTC.


def couvre(symbole: str) -> bool:
    return symbole in MARCHES


def _serie(code: str) -> pd.DataFrame:
    """Historique hebdomadaire du marché (cache 24 h — publication hebdo)."""
    cache = config.CACHE_DIR / f"cot_{code}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_H * 3600:
        brut = json.loads(cache.read_text(encoding="utf-8"))
    else:
        resp = requests.get(API, params={
            "cftc_contract_market_code": code,
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": FENETRE_INDEX_SEMAINES + 10,
        }, timeout=30)
        resp.raise_for_status()
        brut = resp.json()
        cache.write_text(json.dumps(brut), encoding="utf-8")
    if not brut:
        raise RuntimeError(f"Aucune donnée COT pour le code {code}")

    df = pd.DataFrame(brut)
    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    for col in ("noncomm_positions_long_all", "noncomm_positions_short_all",
                "comm_positions_long_all", "comm_positions_short_all",
                "open_interest_all"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["noncomm_positions_long_all"]).sort_values("date")
    df["net_specs"] = (df["noncomm_positions_long_all"]
                       - df["noncomm_positions_short_all"])
    df["net_commerciaux"] = (df["comm_positions_long_all"]
                             - df["comm_positions_short_all"])
    return df.reset_index(drop=True)


def analyse(symbole: str) -> dict:
    """Positionnement spéculatif et COT index pour un actif couvert."""
    if symbole not in MARCHES:
        raise RuntimeError(f"{symbole} hors périmètre COT (contrats US uniquement)")
    code, inverser = MARCHES[symbole]
    df = _serie(code)
    if len(df) < 30:
        raise RuntimeError(f"Historique COT trop court ({len(df)} semaines)")

    net = df["net_specs"] * (-1 if inverser else 1)
    net_com = df["net_commerciaux"] * (-1 if inverser else 1)
    actuel = float(net.iloc[-1])
    precedent = float(net.iloc[-2]) if len(net) > 1 else actuel
    borne_basse, borne_haute = float(net.min()), float(net.max())
    index = (100 * (actuel - borne_basse) / (borne_haute - borne_basse)
             if borne_haute > borne_basse else 50.0)

    oi = float(df["open_interest_all"].iloc[-1])
    extreme = index >= 85 or index <= 15
    sens_specs = "acheteurs" if actuel > 0 else "vendeurs"
    lecture = (
        f"spéculateurs {sens_specs} nets ({actuel:+,.0f} contrats, "
        f"{'renforcent' if abs(actuel) > abs(precedent) else 'allègent'}), "
        f"COT index {index:.0f}/100 sur 3 ans"
        + (f" — POSITIONNEMENT EXTRÊME : le trade est encombré, il reste peu "
           f"d'acheteurs potentiels" if index >= 85 else
           " — POSITIONNEMENT EXTRÊME côté vendeur : terrain de rebond "
           "contrarien" if index <= 15 else "")
    )
    return {
        "symbole": symbole,
        "marche_cftc": df.iloc[-1].get("market_and_exchange_names", code),
        "date_rapport": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "inverse_pour_la_paire": inverser,
        "net_speculateurs": int(actuel),
        "variation_1_semaine": int(actuel - precedent),
        "net_commerciaux": int(net_com.iloc[-1]),
        "cot_index_3ans": round(index, 1),
        "extreme": bool(extreme),
        "open_interest": int(oi),
        "lecture": lecture,
    }


def controle(symbole: str, sens: str) -> dict:
    """Verdict COT pour un sens de trade donné (utilisé par les renforts).

    - favorable : les spéculateurs accompagnent SANS être à l'extrême ;
    - défavorable : positionnement extrême dans le sens du trade (encombré),
      ou spéculateurs massivement contre.
    """
    try:
        a = analyse(symbole)
    except RuntimeError as exc:
        return {"favorable": None, "raison": str(exc)[:100]}

    idx = a["cot_index_3ans"]
    if sens == "achat":
        if idx >= 85:
            favorable = False
            raison = (f"COT index {idx}/100 : positionnement acheteur EXTRÊME "
                      "— trade encombré, prudence sur les achats.")
        elif idx <= 15:
            favorable = None
            raison = (f"COT index {idx}/100 : les spéculateurs sont au plus "
                      "bas — configuration contrarienne, sans confirmation.")
        else:
            favorable = a["net_speculateurs"] > 0 or a["variation_1_semaine"] > 0
            raison = a["lecture"]
    elif sens == "vente":
        if idx <= 15:
            favorable = False
            raison = (f"COT index {idx}/100 : positionnement vendeur EXTRÊME "
                      "— trade encombré, prudence sur les ventes.")
        else:
            favorable = a["net_speculateurs"] < 0 or a["variation_1_semaine"] < 0
            raison = a["lecture"]
    else:
        favorable, raison = None, a["lecture"]

    return {"favorable": favorable, "raison": raison, **{
        k: a[k] for k in ("cot_index_3ans", "net_speculateurs",
                          "variation_1_semaine", "date_rapport", "extreme")}}


def panorama(symboles: list[str] | None = None) -> pd.DataFrame:
    """Tableau du positionnement spéculatif pour tous les actifs couverts."""
    symboles = symboles or list(MARCHES)
    lignes = []
    for s in symboles:
        if s not in MARCHES:
            continue
        try:
            a = analyse(s)
            lignes.append({
                "symbole": s,
                "nom": config.NOMS_ACTIFS.get(s, s),
                "net_speculateurs": a["net_speculateurs"],
                "variation_1_sem": a["variation_1_semaine"],
                "cot_index_3ans": a["cot_index_3ans"],
                "extreme": a["extreme"],
                "date_rapport": a["date_rapport"],
            })
        except Exception as exc:
            lignes.append({"symbole": s, "nom": config.NOMS_ACTIFS.get(s, s),
                           "erreur": str(exc)[:60]})
    return pd.DataFrame(lignes)
