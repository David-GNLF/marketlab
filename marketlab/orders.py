"""Exécution semi-automatisée : propositions d'ordres à valider une par une.

Le système PROPOSE, l'humain DÉCIDE : chaque proposition (générée depuis les
signaux du screener, dimensionnée par le risque) reste en attente jusqu'à
validation explicite. La validation exécute l'ordre dans le portefeuille
PAPIER et produit un ticket lisible ; pour un compte réel, le ticket se
recopie manuellement chez le courtier — MarketLab ne passe jamais d'ordre réel.

Dimensionnement : risque fixe par position (défaut 1 % du capital), stop
suggéré à 2×ATR(14) sous le prix ; montant plafonné à 20 % du capital.
"""

import json
import uuid

import pandas as pd

from marketlab import config, indicators, paper, screener
from marketlab.data import get_ohlcv

ORDRES_PATH = config.DATA_DIR / "ordres_proposes.json"
UNIVERS_DEFAUT = ["Actions US", "Actions EU", "Crypto"]


def _load() -> list[dict]:
    if ORDRES_PATH.exists():
        return json.loads(ORDRES_PATH.read_text(encoding="utf-8"))
    return []


def _save(ordres: list[dict]) -> None:
    ORDRES_PATH.write_text(json.dumps(ordres[-300:], ensure_ascii=False, indent=1),
                           encoding="utf-8")


def lister(statut: str | None = None) -> list[dict]:
    ordres = _load()
    return [o for o in ordres if statut is None or o["statut"] == statut]


def proposer(universes: list[str] | None = None, seuil_achat: float = 40.0,
             seuil_vente: float = -15.0, risque_pct: float = 1.0,
             max_nouvelles: int = 5) -> list[dict]:
    """Génère des propositions depuis les signaux. N'exécute RIEN."""
    universes = universes or UNIVERS_DEFAUT
    pf = paper.load()
    # capital approché : cash + positions au prix de revient (évite un aller
    # réseau par ligne ; le sizing n'a pas besoin d'une valorisation exacte)
    capital = pf["cash"] + sum(p["qty"] * p["prix_moyen"]
                               for p in pf["positions"].values())
    ordres = _load()
    en_attente = {o["symbole"] for o in ordres if o["statut"] == "proposee"}

    symbols = [s for u in universes for s in config.UNIVERS.get(u, [])
               if not s.endswith("=X")]
    table = screener.scan(symbols)
    scores = {r["symbole"]: r["score"] for _, r in table.iterrows()
              if r["score"] is not None}
    nouvelles: list[dict] = []

    def _proposition(sens: str, sym: str, motif: str, **extra) -> dict:
        return {"id": uuid.uuid4().hex[:8], "quand": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
                "sens": sens, "symbole": sym, "score": scores.get(sym),
                "motif": motif, "statut": "proposee", **extra}

    # Ventes : positions détenues dont le signal s'est retourné
    for sym, pos in pf["positions"].items():
        sc = scores.get(sym)
        if sc is not None and sc <= seuil_vente and sym not in en_attente:
            nouvelles.append(_proposition(
                "VENTE", sym, f"score {sc} ≤ seuil vente {seuil_vente}",
                qty=round(pos["qty"], 6), prix_ref=None, stop_suggere=None,
                montant_usd=None))

    # Achats : meilleurs scores non détenus, dimensionnés par le risque
    candidats = [(s, sc) for s, sc in sorted(scores.items(), key=lambda x: -x[1])
                 if sc >= seuil_achat and s not in pf["positions"]
                 and s not in en_attente]
    for sym, sc in candidats[:max_nouvelles]:
        try:
            df = indicators.enrich(get_ohlcv(sym))
            px_local = float(df["close"].iloc[-1])
            atr = float(df["atr14"].iloc[-1])
            px_usd = paper.prix_usd(sym)
            fx = px_usd / px_local  # conversion éventuelle EUR→USD
            atr_usd = atr * fx
            stop_usd = px_usd - 2 * atr_usd
            risque_usd = capital * risque_pct / 100
            qty = risque_usd / (2 * atr_usd) if atr_usd > 0 else 0.0
            montant = min(qty * px_usd, capital * 0.20)
            if montant < 50:
                continue
            nouvelles.append(_proposition(
                "ACHAT", sym,
                f"score {sc} ≥ seuil achat {seuil_achat} ; risque {risque_pct} % "
                f"du capital, stop à 2×ATR",
                prix_ref=round(px_usd, 4), stop_suggere=round(stop_usd, 4),
                montant_usd=round(montant, 2), qty=None))
        except Exception:
            continue  # un titre en échec ne bloque pas la génération

    ordres.extend(nouvelles)
    _save(ordres)
    return nouvelles


def _trouver(prop_id: str, ordres: list[dict]) -> dict:
    for o in ordres:
        if o["id"] == prop_id:
            return o
    raise RuntimeError(f"Proposition {prop_id} introuvable")


def valider(prop_id: str) -> dict:
    """Exécute la proposition dans le portefeuille PAPIER + ticket d'ordre."""
    ordres = _load()
    o = _trouver(prop_id, ordres)
    if o["statut"] != "proposee":
        raise RuntimeError(f"Proposition déjà {o['statut']}")
    if o["sens"] == "ACHAT":
        trade = paper.acheter(o["symbole"], o["montant_usd"])
    else:
        trade = paper.vendre(o["symbole"], o.get("qty"))
    o["statut"] = "executee"
    o["trade"] = trade
    o["ticket"] = (
        f"TICKET {o['sens']} {o['symbole']} — qty {trade['qty']} "
        f"@ ~{trade['prix_usd']} USD (montant {trade['montant_usd']} USD)"
        + (f" ; stop suggéré {o['stop_suggere']} USD" if o.get("stop_suggere") else "")
        + " — exécuté en PAPIER ; pour un compte réel, recopier chez le courtier."
    )
    _save(ordres)
    return o


def rejeter(prop_id: str) -> dict:
    ordres = _load()
    o = _trouver(prop_id, ordres)
    if o["statut"] != "proposee":
        raise RuntimeError(f"Proposition déjà {o['statut']}")
    o["statut"] = "rejetee"
    _save(ordres)
    return o
