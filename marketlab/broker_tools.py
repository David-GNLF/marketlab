"""Les outils des salles de marché : six indicateurs de référence des brokers.

Sélection fondée sur l'usage réel des plateformes professionnelles
(MetaTrader, TradingView, terminaux institutionnels) :

- **ADX / DMI** (Wilder) — LE filtre de régime des stratégies quantitatives
  institutionnelles : au-dessus de 25, la tendance est exploitable ; sous 20,
  le marché est en range et les outils de tendance mentent.
- **Supertrend** (10, 3) — direction limpide + stop dynamique intégré ;
  l'indicateur de suivi le plus utilisé des plateformes de détail.
- **Ichimoku Kinkō Hyō** — le système complet japonais : tendance, momentum
  et supports/résistances (nuage) en une lecture.
- **Retracements de Fibonacci** — les niveaux les plus surveillés du marché ;
  leur pouvoir vient précisément du nombre d'yeux posés dessus.
- **Stochastique** (14, 3, 3) — timing de surachat/survente, dans le top 4
  des indicateurs les plus employés.
- **OBV** — la confirmation par les volumes : un mouvement sans volume est
  un mouvement suspect (non applicable au forex, sans volume centralisé).

Chaque outil renvoie {signal, valeurs, lecture} — signal ∈ {haussier,
baissier, neutre} — et `consensus()` les agrège en un score lisible.
Ces outils DÉCRIVENT ; le verdict et ses vetos restent seuls décideurs.
"""

import numpy as np
import pandas as pd

from marketlab import indicators


# --- ADX / DMI ----------------------------------------------------------------

def adx_dmi(df: pd.DataFrame, periode: int = 14) -> dict:
    h, b, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - b, (h - c.shift()).abs(),
                    (b - c.shift()).abs()], axis=1).max(axis=1)
    up, down = h.diff(), -b.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr = tr.ewm(alpha=1 / periode, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / periode, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / periode, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / periode, adjust=False).mean()

    a, p, m = float(adx.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])
    if a < 20:
        signal, lecture = "neutre", (
            f"ADX {a:.0f} : PAS de tendance exploitable — marché en range, "
            "se méfier des signaux de tendance")
    elif p > m:
        signal = "haussier"
        force = "forte" if a >= 25 else "naissante"
        lecture = f"ADX {a:.0f}, +DI {p:.0f} > −DI {m:.0f} : tendance haussière {force}"
    else:
        signal = "baissier"
        force = "forte" if a >= 25 else "naissante"
        lecture = f"ADX {a:.0f}, −DI {m:.0f} > +DI {p:.0f} : tendance baissière {force}"
    return {"outil": "ADX/DMI", "signal": signal, "adx": round(a, 1),
            "plus_di": round(p, 1), "minus_di": round(m, 1), "lecture": lecture}


# --- Supertrend ---------------------------------------------------------------

def supertrend(df: pd.DataFrame, periode: int = 10, mult: float = 3.0) -> dict:
    if "atr14" not in df.columns:
        df = indicators.enrich(df)
    h, b, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    tr = np.maximum(h[1:] - b[1:], np.maximum(abs(h[1:] - c[:-1]),
                                              abs(b[1:] - c[:-1])))
    atr = pd.Series(tr).ewm(alpha=1 / periode, adjust=False).mean().to_numpy()
    atr = np.concatenate([[atr[0]], atr])
    milieu = (h + b) / 2
    haut_base, bas_base = milieu + mult * atr, milieu - mult * atr

    haut = haut_base.copy()
    bas = bas_base.copy()
    tendance = np.ones(len(c), dtype=int)
    for i in range(1, len(c)):
        haut[i] = haut_base[i] if haut_base[i] < haut[i - 1] or c[i - 1] > haut[i - 1] \
            else haut[i - 1]
        bas[i] = bas_base[i] if bas_base[i] > bas[i - 1] or c[i - 1] < bas[i - 1] \
            else bas[i - 1]
        if tendance[i - 1] == 1:
            tendance[i] = -1 if c[i] < bas[i] else 1
        else:
            tendance[i] = 1 if c[i] > haut[i] else -1

    ligne = bas[-1] if tendance[-1] == 1 else haut[-1]
    depuis = 1
    while depuis < len(tendance) and tendance[-1 - depuis] == tendance[-1]:
        depuis += 1
    signal = "haussier" if tendance[-1] == 1 else "baissier"
    return {
        "outil": "Supertrend", "signal": signal,
        "ligne": round(float(ligne), 4), "depuis_seances": depuis,
        "lecture": (f"Supertrend {signal} depuis {depuis} séances — la ligne "
                    f"{ligne:,.2f} sert de stop dynamique "
                    f"({'sous' if signal == 'haussier' else 'au-dessus du'} prix)"),
    }


# --- Ichimoku -----------------------------------------------------------------

def ichimoku(df: pd.DataFrame) -> dict:
    h, b, c = df["high"], df["low"], df["close"]
    tenkan = (h.rolling(9).max() + b.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + b.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((h.rolling(52).max() + b.rolling(52).min()) / 2).shift(26)

    prix = float(c.iloc[-1])
    na, nb = float(senkou_a.iloc[-1]), float(senkou_b.iloc[-1])
    haut_nuage, bas_nuage = max(na, nb), min(na, nb)
    tk = float(tenkan.iloc[-1]) - float(kijun.iloc[-1])

    if prix > haut_nuage:
        position = "au-dessus du nuage"
        signal = "haussier" if tk >= 0 else "neutre"
    elif prix < bas_nuage:
        position = "sous le nuage"
        signal = "baissier" if tk <= 0 else "neutre"
    else:
        position, signal = "DANS le nuage", "neutre"
    croisement = ("Tenkan > Kijun (élan haussier)" if tk > 0 else
                  "Tenkan < Kijun (élan baissier)" if tk < 0 else "Tenkan = Kijun")
    return {
        "outil": "Ichimoku", "signal": signal,
        "nuage": [round(bas_nuage, 4), round(haut_nuage, 4)],
        "lecture": (f"prix {position} ({bas_nuage:,.2f}–{haut_nuage:,.2f}), "
                    f"{croisement} — "
                    + {"haussier": "configuration haussière complète",
                       "baissier": "configuration baissière complète",
                       "neutre": "signaux mixtes, le nuage fait obstacle"}[signal]),
    }


# --- Fibonacci ----------------------------------------------------------------

def fibonacci(df: pd.DataFrame, fenetre: int = 120) -> dict:
    recent = df.tail(fenetre)
    prix = float(df["close"].iloc[-1])
    i_haut = recent["high"].idxmax()
    i_bas = recent["low"].idxmin()
    haut, bas = float(recent["high"].max()), float(recent["low"].min())
    montant = i_bas < i_haut  # le creux précède le sommet : jambe haussière

    niveaux = {}
    for r in (0.236, 0.382, 0.5, 0.618, 0.786):
        niveaux[f"{r * 100:.1f}"] = round(
            haut - (haut - bas) * r if montant else bas + (haut - bas) * r, 4)

    sous = {k: v for k, v in niveaux.items() if v < prix}
    dessus = {k: v for k, v in niveaux.items() if v > prix}
    support = max(sous.items(), key=lambda kv: kv[1]) if sous else None
    resistance = min(dessus.items(), key=lambda kv: kv[1]) if dessus else None

    jambe = "haussière" if montant else "baissière"
    morceaux = [f"jambe {jambe} {bas:,.2f} → {haut:,.2f}" if montant
                else f"jambe {jambe} {haut:,.2f} → {bas:,.2f}"]
    if support:
        morceaux.append(f"support fib {support[0]} % à {support[1]:,.2f}")
    if resistance:
        morceaux.append(f"résistance fib {resistance[0]} % à {resistance[1]:,.2f}")
    retenu = float(support[1]) if support else bas
    profondeur = (haut - prix) / (haut - bas) if haut > bas else 0
    signal = ("haussier" if montant and profondeur < 0.382 else
              "baissier" if not montant and profondeur > 0.618 else "neutre")
    return {"outil": "Fibonacci", "signal": signal, "niveaux": niveaux,
            "support": support[1] if support else None,
            "resistance": resistance[1] if resistance else None,
            "lecture": " ; ".join(morceaux)
                       + " — niveaux surveillés par tout le marché"}


# --- Stochastique -------------------------------------------------------------

def stochastique(df: pd.DataFrame, periode: int = 14, lissage: int = 3) -> dict:
    bas_n = df["low"].rolling(periode).min()
    haut_n = df["high"].rolling(periode).max()
    k_brut = 100 * (df["close"] - bas_n) / (haut_n - bas_n)
    k = k_brut.rolling(lissage).mean()
    d = k.rolling(lissage).mean()
    vk, vd = float(k.iloc[-1]), float(d.iloc[-1])

    if vk > 80:
        signal = "baissier" if vk < vd else "neutre"
        etat = "SURACHAT" + (" avec croisement baissier" if vk < vd else "")
    elif vk < 20:
        signal = "haussier" if vk > vd else "neutre"
        etat = "SURVENTE" + (" avec croisement haussier" if vk > vd else "")
    else:
        signal = "haussier" if vk > vd else "baissier"
        etat = f"zone médiane, %K {'>' if vk > vd else '<'} %D"
    return {"outil": "Stochastique", "signal": signal,
            "k": round(vk, 1), "d": round(vd, 1),
            "lecture": f"%K {vk:.0f} / %D {vd:.0f} : {etat}"}


# --- OBV ----------------------------------------------------------------------

def obv(df: pd.DataFrame) -> dict | None:
    if float(df["volume"].tail(60).sum()) <= 0:
        return None  # forex : pas de volume centralisé
    flux = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    moyenne = flux.rolling(20).mean()
    au_dessus = float(flux.iloc[-1]) > float(moyenne.iloc[-1])
    pente = float(flux.iloc[-1] - flux.iloc[-10])
    signal = ("haussier" if au_dessus and pente > 0 else
              "baissier" if not au_dessus and pente < 0 else "neutre")
    return {"outil": "OBV", "signal": signal,
            "lecture": ("les volumes accompagnent la hausse (accumulation)"
                        if signal == "haussier" else
                        "les volumes accompagnent la baisse (distribution)"
                        if signal == "baissier" else
                        "volumes sans direction nette")}


# --- Consensus ----------------------------------------------------------------

def analyse(df: pd.DataFrame) -> dict:
    """Les six outils + consensus. ADX < 20 est signalé en avertissement de
    régime : en range, les signaux de tendance perdent leur sens."""
    if "sma200" not in df.columns:
        df = indicators.enrich(df)
    outils = []
    for calc in (adx_dmi, supertrend, ichimoku, fibonacci, stochastique, obv):
        try:
            r = calc(df)
            if r is not None:
                outils.append(r)
        except Exception as exc:
            outils.append({"outil": calc.__name__, "signal": "neutre",
                           "lecture": f"indisponible : {str(exc)[:60]}"})

    haussiers = sum(1 for o in outils if o["signal"] == "haussier")
    baissiers = sum(1 for o in outils if o["signal"] == "baissier")
    total = len(outils)
    adx_val = next((o.get("adx") for o in outils if o["outil"] == "ADX/DMI"), None)
    avertissement = (f"ADX {adx_val:.0f} : marché en RANGE — consensus à lire "
                     "avec prudence" if adx_val is not None and adx_val < 20
                     else None)
    if haussiers >= baissiers + 2:
        tendance = "haussier"
    elif baissiers >= haussiers + 2:
        tendance = "baissier"
    else:
        tendance = "partagé"
    return {
        "outils": outils,
        "consensus": {"haussiers": haussiers, "baissiers": baissiers,
                      "total": total, "tendance": tendance,
                      "texte": f"{haussiers} haussier(s) / {baissiers} "
                               f"baissier(s) sur {total} — {tendance}"},
        "avertissement_regime": avertissement,
    }


def consensus(df: pd.DataFrame) -> dict:
    """Version légère pour la liste des verdicts."""
    a = analyse(df)
    return {**a["consensus"],
            "avertissement": a["avertissement_regime"]}
