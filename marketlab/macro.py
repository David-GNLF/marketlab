"""Tableau de bord macro US : niveaux, tendances et lecture de régime.

Les événements macro (décisions Fed, CPI, NFP) font bouger tous les marchés,
forex et crypto compris. Ce module donne le contexte ; le calendrier des
événements à venir arrive en Phase 2.
"""

import pandas as pd

from marketlab import config
from marketlab.data import fred


def snapshot() -> pd.DataFrame:
    """Dernière valeur + variation sur ~3 mois et ~1 an de chaque série clé."""
    rows = []
    for series_id, label in config.FRED_SERIES.items():
        try:
            s = fred.get_series(series_id)
            last = float(s.iloc[-1])
            m3 = s[s.index <= s.index[-1] - pd.DateOffset(months=3)]
            y1 = s[s.index <= s.index[-1] - pd.DateOffset(years=1)]
            rows.append({
                "indicateur": label,
                "serie": series_id,
                "valeur": round(last, 2),
                "date": s.index[-1].date().isoformat(),
                "delta_3m": round(last - float(m3.iloc[-1]), 2) if len(m3) else None,
                "delta_1a": round(last - float(y1.iloc[-1]), 2) if len(y1) else None,
            })
        except Exception as exc:
            rows.append({"indicateur": label, "serie": series_id, "valeur": None,
                         "date": None, "delta_3m": None, "delta_1a": None,
                         "erreur": str(exc)[:60]})
    return pd.DataFrame(rows)


def inflation_yoy() -> pd.Series:
    """Inflation US en glissement annuel (%) à partir de l'indice CPI."""
    cpi = fred.get_series("CPIAUCSL")
    return (cpi.pct_change(12) * 100).dropna()


def regime() -> dict:
    """Lecture heuristique du régime macro. Indicative, pas prédictive."""
    notes = []
    score = 0
    try:
        infl = float(inflation_yoy().iloc[-1])
        if infl > 4:
            score -= 2; notes.append(f"Inflation élevée ({infl:.1f} %) : vent contraire")
        elif infl > 2.5:
            score -= 1; notes.append(f"Inflation au-dessus de la cible ({infl:.1f} %)")
        else:
            score += 1; notes.append(f"Inflation maîtrisée ({infl:.1f} %)")
    except Exception:
        notes.append("Inflation indisponible")
    try:
        curve = float(fred.get_series("T10Y2Y").iloc[-1])
        if curve < 0:
            score -= 2; notes.append(f"Courbe des taux inversée ({curve:.2f}) : signal récession historique")
        else:
            score += 1; notes.append(f"Courbe des taux normale ({curve:.2f})")
    except Exception:
        notes.append("Courbe des taux indisponible")
    try:
        vix = float(fred.get_series("VIXCLS").iloc[-1])
        if vix > 30:
            score -= 2; notes.append(f"VIX en zone de stress ({vix:.0f})")
        elif vix > 20:
            score -= 1; notes.append(f"VIX nerveux ({vix:.0f})")
        else:
            score += 1; notes.append(f"VIX calme ({vix:.0f})")
    except Exception:
        notes.append("VIX indisponible")

    lecture = "favorable au risque" if score >= 2 else \
              "prudence" if score >= 0 else "défavorable au risque"
    return {"score": score, "lecture": lecture, "notes": notes}
