"""Calendrier économique — flux JSON public ForexFactory (sans clé).

Couvre la semaine en cours (le flux bascule sur la semaine suivante chaque
dimanche ; l'URL nextweek est tentée mais renvoie 404 actuellement) :
décisions de banques centrales, CPI, NFP, PIB… avec niveau d'impact,
prévision et valeur précédente.
Heures converties en heure du Bénin (UTC+1, sans heure d'été).
"""

import json
import time

import pandas as pd
import requests

from marketlab import config

URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
CACHE = config.CACHE_DIR / "eco_calendar.json"
CACHE_TTL_S = 4 * 3600
LOCAL_UTC_OFFSET_H = 1  # Bénin = UTC+1 toute l'année

IMPACT_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Holiday": 3}
IMPACT_FR = {"High": "Fort", "Medium": "Moyen", "Low": "Faible", "Holiday": "Férié"}


def _fetch_raw() -> list[dict]:
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_S:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    events: list[dict] = []
    errors: list[str] = []
    for url in URLS:
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "MarketLab/0.1"})
            resp.raise_for_status()
            events.extend(resp.json())
        except Exception as exc:  # nextweek peut manquer sans invalider thisweek
            errors.append(f"{url}: {exc}")
    if not events:
        raise RuntimeError("Calendrier économique inaccessible — " + " | ".join(errors))
    CACHE.write_text(json.dumps(events), encoding="utf-8")
    return events


def get_events(currencies: list[str] | None = None,
               impacts: list[str] | None = None) -> pd.DataFrame:
    """Événements normalisés, triés par date, filtrables par devise et impact.

    Colonnes : quand (datetime UTC+1), devise, evenement, impact, impact_fr,
    prevision, precedent.
    """
    rows = []
    for ev in _fetch_raw():
        try:
            when = pd.to_datetime(ev["date"], utc=True).tz_localize(None) \
                   + pd.Timedelta(hours=LOCAL_UTC_OFFSET_H)
        except Exception:
            continue
        rows.append({
            "quand": when,
            "devise": ev.get("country", ""),
            "evenement": ev.get("title", ""),
            "impact": ev.get("impact", ""),
            "impact_fr": IMPACT_FR.get(ev.get("impact", ""), ev.get("impact", "")),
            "prevision": ev.get("forecast", "") or "",
            "precedent": ev.get("previous", "") or "",
        })
    df = pd.DataFrame(rows).drop_duplicates(subset=["quand", "devise", "evenement"])
    if currencies:
        df = df[df["devise"].isin(currencies)]
    if impacts:
        df = df[df["impact"].isin(impacts)]
    return df.sort_values("quand").reset_index(drop=True)


def upcoming(hours: int = 24, impacts: list[str] | None = None) -> pd.DataFrame:
    """Événements dans les `hours` prochaines heures (défaut : fort impact)."""
    impacts = impacts or ["High"]
    df = get_events(impacts=impacts)
    now = pd.Timestamp.now()
    return df[(df["quand"] >= now) & (df["quand"] <= now + pd.Timedelta(hours=hours))] \
        .reset_index(drop=True)


def event_key(row: pd.Series) -> str:
    """Identifiant stable d'un événement (pour l'anti-doublon des alertes)."""
    return f"{row['quand'].isoformat()}|{row['devise']}|{row['evenement']}"
