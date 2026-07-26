"""API REST MarketLab (FastAPI) — expose toutes les briques d'analyse.

Lancer :  python -m uvicorn main:app --app-dir api --port 8600 --reload
Docs interactives : http://localhost:8600/docs
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles

from marketlab import (config, correlations, eco_calendar, events, forecast,
                       fundamentals, indicators, levels, macro, metalabel, ml,
                       news, orders, paper, score_history, screener, seasonality,
                       signals)
from marketlab.data import get_ohlcv, premium

app = FastAPI(
    title="MarketLab API",
    version="0.4.0",
    description="Outils d'aide à la décision — analyses statistiques, pas des "
                "prédictions. Aucun contenu ne constitue un conseil en investissement.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5180", "http://localhost:5173",
                   "http://127.0.0.1:5180"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe records (dates ISO, NaN -> None)."""
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index(names="date")
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    # passage en object AVANT le where : un float64 ne peut pas contenir None,
    # le NaN survivrait et casserait la sérialisation JSON
    out = out.astype(object).where(out.notna(), None)
    return out.to_dict(orient="records")


# --- Référentiel ------------------------------------------------------------

@app.get("/api/univers")
def univers():
    return config.UNIVERS


# --- Données & signaux ------------------------------------------------------

@app.get("/api/ohlcv/{symbol}")
def ohlcv(symbol: str, lookback_days: int = 365):
    try:
        df = indicators.enrich(get_ohlcv(symbol.upper(), lookback_days=lookback_days))
    except Exception as exc:
        raise HTTPException(404, f"Données indisponibles pour {symbol} : {exc}")
    cols = ["open", "high", "low", "close", "volume", "sma50", "sma200",
            "rsi14", "hist", "bb_upper", "bb_lower"]
    return _records(df[cols].round(6))


@app.get("/api/signaux/{symbol}")
def signaux(symbol: str):
    try:
        df = indicators.enrich(get_ohlcv(symbol.upper()))
    except Exception as exc:
        raise HTTPException(404, f"Données indisponibles pour {symbol} : {exc}")
    sig = signals.compute_signals(df)
    sig["avis"] = signals.label(sig["score"])
    return sig


@app.get("/api/screener")
def scan(univers: list[str] = Query(default=["Actions US"])):
    symbols = [s for u in univers for s in config.UNIVERS.get(u, [])]
    if not symbols:
        raise HTTPException(400, f"Univers inconnus : {univers}")
    return _records(screener.scan(symbols))


# --- Contexte ---------------------------------------------------------------

@app.get("/api/macro")
def macro_view():
    return {"regime": macro.regime(), "indicateurs": _records(macro.snapshot())}


@app.get("/api/calendrier")
def calendrier(impacts: list[str] = Query(default=["High", "Medium"])):
    try:
        df = eco_calendar.get_events(impacts=impacts)
    except Exception as exc:
        raise HTTPException(502, f"Calendrier indisponible : {exc}")
    df = df.copy()
    df["quand"] = df["quand"].dt.strftime("%Y-%m-%d %H:%M")
    return _records(df)


@app.get("/api/news/{symbol}")
def news_view(symbol: str):
    try:
        return {"sentiment": news.sentiment(symbol.upper()),
                "titres": _records(news.headlines(symbol.upper()))}
    except Exception as exc:
        raise HTTPException(502, f"Actualités indisponibles : {exc}")


# --- ML & score -------------------------------------------------------------

@app.get("/api/score-predictif/{symbol}")
def score_predictif(symbol: str, horizon: int = 10):
    try:
        return score_history.predictive_power(symbol.upper(), horizon=horizon)
    except Exception as exc:
        raise HTTPException(400, str(exc))


class MlParams(BaseModel):
    horizon: int = 5
    threshold: float = 0.55
    lookback_days: int = 1825
    include_macro: bool = True


@app.post("/api/ml/{symbol}")
def ml_walk_forward(symbol: str, params: MlParams):
    try:
        df = indicators.enrich(get_ohlcv(symbol.upper(),
                                         lookback_days=params.lookback_days))
        res = ml.walk_forward(df, horizon=params.horizon,
                              threshold=params.threshold)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    equity = res["equity"].round(4)
    bh = res["bh_equity"].round(4)
    return {
        "metrics": res["metrics"],
        "folds": res["folds"].to_dict(orient="records"),
        "equity": [{"date": d.strftime("%Y-%m-%d"), "strategie": float(v),
                    "buyhold": float(bh.loc[d])} for d, v in equity.items()],
    }


# --- Paper trading ----------------------------------------------------------

class InitParams(BaseModel):
    capital: float = 10_000.0


class OrdreAchat(BaseModel):
    symbole: str
    montant: float


class OrdreVente(BaseModel):
    symbole: str
    qty: float | None = None


class AutoParams(BaseModel):
    dry_run: bool = True
    univers: list[str] | None = None


@app.get("/api/paper")
def paper_etat():
    try:
        e = paper.etat()
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))
    e["positions"] = _records(e["positions"]) if len(e["positions"]) else []
    return e


@app.get("/api/paper/historique")
def paper_historique():
    try:
        return paper.load()["transactions"]
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/paper/init")
def paper_init(params: InitParams):
    paper.init(params.capital)
    return {"ok": True, "capital": params.capital}


@app.post("/api/paper/acheter")
def paper_acheter(ordre: OrdreAchat):
    try:
        return paper.acheter(ordre.symbole.upper(), ordre.montant)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/paper/vendre")
def paper_vendre(ordre: OrdreVente):
    try:
        return paper.vendre(ordre.symbole.upper(), ordre.qty)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/paper/auto")
def paper_auto(params: AutoParams):
    try:
        return {"journal": paper.auto(universes=params.univers,
                                      dry_run=params.dry_run)}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


# --- Ordres semi-automatisés (proposition → validation manuelle) ------------

class GenererParams(BaseModel):
    univers: list[str] | None = None
    seuil_achat: float = 40.0
    seuil_vente: float = -15.0
    risque_pct: float = 1.0


@app.get("/api/ordres")
def ordres_lister(statut: str | None = None):
    return orders.lister(statut)


@app.post("/api/ordres/generer")
def ordres_generer(params: GenererParams):
    try:
        return {"nouvelles": orders.proposer(
            universes=params.univers, seuil_achat=params.seuil_achat,
            seuil_vente=params.seuil_vente, risque_pct=params.risque_pct)}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/ordres/{prop_id}/valider")
def ordres_valider(prop_id: str):
    try:
        return orders.valider(prop_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/ordres/{prop_id}/rejeter")
def ordres_rejeter(prop_id: str):
    try:
        return orders.rejeter(prop_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


# --- Prévision probabiliste -------------------------------------------------

@app.get("/api/prevision/{symbol}")
def prevision(symbol: str, horizon: int = 20):
    """Régime, volatilité, cône de projection et analogues historiques."""
    try:
        return forecast.analyser(symbol.upper(), horizon=horizon)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/calibration/{symbol}")
def calibration(symbol: str, horizon: int = 20, n_tests: int = 80):
    """Contrôle qualité : les intervalles annoncés tiennent-ils leurs promesses ?"""
    try:
        df = indicators.enrich(get_ohlcv(symbol.upper(), lookback_days=1825))
        return forecast.calibration(df, horizon=horizon, n_tests=n_tests)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/plan/{symbol}")
def plan_position(symbol: str, sens: str = "achat", horizon: int = 20,
                  capital: float | None = None, risque_pct: float = 1.0):
    """Plan chiffré : entrée, stop, objectif, probabilités, taille."""
    try:
        return levels.plan(symbol.upper(), sens=sens, horizon=horizon,
                           capital=capital, risque_pct=risque_pct)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/niveaux/{symbol}")
def niveaux(symbol: str):
    """Supports, résistances et points pivots."""
    try:
        df = indicators.enrich(get_ohlcv(symbol.upper(), lookback_days=1825))
        return {"zones": levels.zones_proches(df), "pivots": levels.pivots(df),
                "regime": forecast.regime(df)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


# --- Fondamentaux -----------------------------------------------------------

@app.get("/api/fondamentaux/{symbol}")
def fondamentaux(symbol: str):
    """Notation fondamentale d'une action (valorisation/qualité/croissance/solidité)."""
    try:
        return fundamentals.noter(symbol.upper())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/fondamentaux")
def fondamentaux_comparer(univers: list[str] = Query(default=["Actions US"])):
    symboles = [s for u in univers for s in config.UNIVERS.get(u, [])]
    if not symboles:
        raise HTTPException(400, f"Univers inconnus : {univers}")
    return _records(fundamentals.comparer(symboles))


# --- Corrélations & risque de portefeuille ----------------------------------

@app.get("/api/correlations")
def correlations_matrice(univers: list[str] = Query(default=["Actions US"]),
                         jours: int = 500):
    symboles = [s for u in univers for s in config.UNIVERS.get(u, [])]
    try:
        m = correlations.matrice(symboles, jours=jours)
        return {
            "symboles": list(m.columns),
            "matrice": m.to_dict(),
            "extremes": correlations.paires_extremes(symboles, jours=jours),
            "par_regime": correlations.correlation_par_regime(symboles),
        }
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/beta/{symbol}")
def beta_marche(symbol: str, reference: str = "^GSPC"):
    try:
        return correlations.beta(symbol.upper(), reference=reference)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/portefeuille/risque")
def portefeuille_risque():
    """Risque, diversification et concentration du portefeuille papier."""
    try:
        return correlations.analyser_paper()
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/portefeuille/diversifier")
def portefeuille_diversifier(n: int = 5):
    try:
        detenus = list(paper.load()["positions"])
        return {"suggestions": correlations.suggerer_diversification(detenus, n=n)}
    except Exception as exc:
        raise HTTPException(400, str(exc))


# --- Résultats trimestriels -------------------------------------------------

@app.get("/api/resultats/{symbol}")
def resultats_etude(symbol: str, avant: int = 10, apres: int = 20):
    """Étude d'événements : comportement du cours autour des publications."""
    try:
        return events.etude(symbol.upper(), avant=avant, apres=apres)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/resultats/{symbol}/risque")
def resultats_risque(symbol: str, horizon: int = 20):
    """Une publication tombe-t-elle dans l'horizon de position ?"""
    try:
        return events.risque_evenement(symbol.upper(), horizon=horizon)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/calendrier-resultats")
def calendrier_resultats(univers: list[str] = Query(default=["Actions US"]),
                         jours: int = 45):
    symboles = [s for u in univers for s in config.UNIVERS.get(u, [])]
    if not symboles:
        raise HTTPException(400, f"Univers inconnus : {univers}")
    return _records(events.prochaines_publications(symboles, jours=jours))


# --- Méta-labeling ----------------------------------------------------------

@app.get("/api/metalabel/{symbol}")
def meta_labeling(symbol: str, horizon: int = 20, seuil_meta: float = 0.55):
    """Le signal primaire mérite-t-il d'être suivi ? (triple barrière + méta)."""
    try:
        return metalabel.analyser(symbol.upper(), horizon=horizon,
                                  seuil_meta=seuil_meta)
    except Exception as exc:
        raise HTTPException(400, str(exc))


# --- Saisonnalité -----------------------------------------------------------

@app.get("/api/saisonnalite/{symbol}")
def saisonnalite(symbol: str):
    """Effets de calendrier, testés (Student + Bonferroni + stabilité)."""
    try:
        return seasonality.analyser(symbol.upper())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/saisonnalite/{symbol}/courbe")
def saisonnalite_courbe(symbol: str):
    """Trajectoire moyenne du rendement cumulé au fil de l'année civile."""
    try:
        return seasonality.courbe_annuelle(symbol.upper())
    except Exception as exc:
        raise HTTPException(400, str(exc))


# --- Statut fournisseurs ----------------------------------------------------

@app.get("/api/fournisseurs")
def fournisseurs():
    return {
        "premium_twelvedata": premium.api_key() is not None,
        "note": "sans clé, tout fonctionne sur les sources gratuites ; "
                "voir data_local/providers.json pour activer Twelve Data",
    }


# --- Front de production (front/dist, généré par `npm run build`) -----------
# Monté en DERNIER : les routes /api/* déclarées ci-dessus restent prioritaires.
_DIST = Path(__file__).resolve().parent.parent / "front" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="front")
