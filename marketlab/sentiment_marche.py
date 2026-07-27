"""Indice Peur & Avidité maison : sept mesures objectives, zéro sondage.

Les sources classiques de sentiment (sondage AAII, ratio put/call CBOE) sont
devenues payantes. Plutôt qu'un sondage déclaratif hebdomadaire, cet indice
agrège SEPT mesures de comportement réel des marchés, toutes gratuites et
quotidiennes :

1. **Niveau du VIX** (percentile 2 ans, inversé) — volatilité payée pour se
   couvrir : basse = sérénité, haute = peur.
2. **Structure par terme du VIX** (VIX / VIX3M) — en contango (court < long),
   le marché est détendu ; en backwardation (>1), la peur est immédiate.
   C'est l'un des marqueurs de stress les plus fiables.
3. **Spread high yield** (FRED BAMLH0A0HYM2, percentile 2 ans inversé) — la
   prime exigée pour prêter aux entreprises fragiles : serrée = appétit pour
   le risque, large = aversion.
4. **Largeur de marché** — part des actions suivies (US, EU, Asie) au-dessus
   de leur moyenne 50 séances : une hausse portée par tout le monde n'a pas
   la même santé qu'une hausse portée par cinq titres.
5. **Refuge or vs actions** — performance relative 20 séances du S&P 500 face
   à l'or : quand l'or bat nettement les actions, l'argent cherche un abri.
6. **VVIX** (volatilité du VIX, percentile 2 ans inversé) — la nervosité de
   ceux qui se couvrent : quand même les couvertures deviennent chères à
   assurer, la peur est profonde.
7. **SKEW** (percentile 2 ans inversé) — le surcoût des puts très
   hors-la-monnaie : ce que le marché paie pour s'assurer contre un krach.
   Indicatif : son pouvoir prédictif est débattu dans la littérature, d'où
   un rôle de simple composante parmi sept.

Chaque composante est notée de 0 (peur extrême) à 100 (avidité extrême), la
moyenne fait l'indice. Usage CONTRARIEN aux extrêmes : sous ~20, la peur est
souvent excessive (terrain d'achat) ; au-delà de ~80, l'euphorie invite à la
prudence. Entre les deux : simple contexte.
"""

import numpy as np
import pandas as pd

from marketlab import config, indicators
from marketlab.data import fred, get_ohlcv


def _percentile(serie: pd.Series, valeur: float) -> float:
    """Rang percentile de `valeur` dans `serie` (0-100)."""
    s = serie.dropna()
    if len(s) < 30:
        return 50.0
    return float((s < valeur).mean() * 100)


def _clip(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


# --- Composantes (0 = peur extrême, 100 = avidité extrême) -------------------

def composante_vix() -> dict:
    df = get_ohlcv("^VIX", lookback_days=730)
    vix = float(df["close"].iloc[-1])
    note = _clip(100 - _percentile(df["close"], vix))
    return {"nom": "niveau du VIX", "note": round(note, 0),
            "detail": f"VIX {vix:.1f} (percentile 2 ans : {100 - note:.0f})"}


def composante_structure_vix() -> dict:
    vix = float(get_ohlcv("^VIX", lookback_days=90)["close"].iloc[-1])
    vix3m = float(get_ohlcv("^VIX3M", lookback_days=90)["close"].iloc[-1])
    ratio = vix / vix3m if vix3m > 0 else 1.0
    # 0,80 (contango profond, sérénité) -> 100 ; 1,10 (backwardation) -> 0
    note = _clip((1.10 - ratio) / 0.30 * 100)
    etat = "contango (détendu)" if ratio < 0.97 else \
        "backwardation (STRESS immédiat)" if ratio > 1.0 else "plat"
    return {"nom": "structure du VIX", "note": round(note, 0),
            "detail": f"VIX/VIX3M {ratio:.2f} : {etat}"}


def composante_credit() -> dict:
    s = fred.get_series("BAMLH0A0HYM2", lookback_years=2)
    spread = float(s.iloc[-1])
    note = _clip(100 - _percentile(s, spread))
    return {"nom": "spread high yield", "note": round(note, 0),
            "detail": f"prime crédit fragile {spread:.2f} pt "
                      f"(percentile 2 ans : {100 - note:.0f})"}


def composante_largeur() -> dict:
    symboles = (config.ACTIONS_US + config.ACTIONS_EU + config.ACTIONS_ASIE)
    dessus, total = 0, 0
    for sym in symboles:
        try:
            df = indicators.enrich(get_ohlcv(sym, lookback_days=400))
            sma50 = df["sma50"].iloc[-1]
            if pd.notna(sma50):
                total += 1
                dessus += int(float(df["close"].iloc[-1]) > float(sma50))
        except Exception:
            continue
    if total == 0:
        return {"nom": "largeur de marché", "note": 50.0,
                "detail": "indisponible"}
    note = _clip(dessus / total * 100)
    return {"nom": "largeur de marché", "note": round(note, 0),
            "detail": f"{dessus}/{total} actions au-dessus de leur SMA50"}


def composante_refuge() -> dict:
    spx = get_ohlcv("^GSPC", lookback_days=200)["close"]
    or_ = get_ohlcv("GC=F", lookback_days=200)["close"]
    perf_spx = float(spx.iloc[-1] / spx.iloc[-20] - 1) * 100
    perf_or = float(or_.iloc[-1] / or_.iloc[-20] - 1) * 100
    ecart = perf_spx - perf_or          # actions battent l'or = appétit
    note = _clip(50 + ecart / 8 * 50)   # ±8 pts d'écart = extrêmes
    return {"nom": "refuge or vs actions", "note": round(note, 0),
            "detail": f"S&P {perf_spx:+.1f} % vs or {perf_or:+.1f} % sur 20 séances"}


def composante_vvix() -> dict:
    df = get_ohlcv("^VVIX", lookback_days=730)
    vvix = float(df["close"].iloc[-1])
    note = _clip(100 - _percentile(df["close"], vvix))
    return {"nom": "VVIX (vol du VIX)", "note": round(note, 0),
            "detail": f"VVIX {vvix:.1f} (percentile 2 ans : {100 - note:.0f}) — "
                      "le coût d'assurer les couvertures elles-mêmes"}


def composante_skew() -> dict:
    df = get_ohlcv("^SKEW", lookback_days=730)
    skew = float(df["close"].iloc[-1])
    note = _clip(100 - _percentile(df["close"], skew))
    return {"nom": "SKEW (risque de queue)", "note": round(note, 0),
            "detail": f"SKEW {skew:.1f} (percentile 2 ans : {100 - note:.0f}) — "
                      "le surcoût de l'assurance anti-krach ; indicatif"}


# --- Indice ------------------------------------------------------------------

def indice() -> dict:
    """L'indice agrégé, chaque composante restant lisible et critiquable."""
    composantes = []
    for calc in (composante_vix, composante_structure_vix, composante_credit,
                 composante_largeur, composante_refuge, composante_vvix,
                 composante_skew):
        try:
            composantes.append(calc())
        except Exception as exc:
            composantes.append({"nom": calc.__name__, "note": None,
                                "detail": f"indisponible : {str(exc)[:60]}"})
    notes = [c["note"] for c in composantes if c["note"] is not None]
    if not notes:
        raise RuntimeError("Aucune composante de sentiment disponible")
    valeur = round(float(np.mean(notes)), 0)

    if valeur <= 20:
        zone, lecture = "peur extrême", (
            "La peur est à un extrême : historiquement un terrain d'achat "
            "contrarien — à condition d'avoir un plan et un stop, pas un pari.")
    elif valeur <= 40:
        zone, lecture = "peur", "Climat craintif : les bonnes affaires naissent ici."
    elif valeur < 60:
        zone, lecture = "neutre", "Ni peur ni euphorie : le sentiment ne dicte rien."
    elif valeur < 80:
        zone, lecture = "avidité", ("Appétit marqué pour le risque : rester "
                                    "sélectif, resserrer les stops.")
    else:
        zone, lecture = "avidité extrême", (
            "Euphorie : quand tout le monde est déjà acheteur, il ne reste "
            "plus grand monde pour pousser les prix. Prudence maximale sur "
            "les nouveaux achats.")
    return {
        "date": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M"),
        "valeur": valeur,
        "zone": zone,
        "lecture": lecture,
        "composantes": composantes,
        "methode": "Moyenne de 7 mesures de comportement réel (0 = peur "
                   "extrême, 100 = avidité extrême) — pas un sondage.",
    }
