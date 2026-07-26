"""Paper trading : portefeuille virtuel pour éprouver les signaux sans risque.

Persistance : data_local/paper_portfolio.json. Valorisation en USD :
actions EU converties via EUR/USD du jour, USDT assimilé à l'USD.
Les paires forex sont exclues (levier et lots hors périmètre).
Exécution au dernier cours de clôture connu (approximation assumée : pas de
spread ni de slippage — les performances papier sont donc optimistes).
"""

import json

import pandas as pd

from marketlab import config, screener, signals
from marketlab.data import get_ohlcv

PORTFOLIO_PATH = config.DATA_DIR / "paper_portfolio.json"
EU_SUFFIXES = (".PA", ".DE", ".AS")


def _now() -> str:
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")


def load() -> dict:
    if not PORTFOLIO_PATH.exists():
        raise RuntimeError("Aucun portefeuille papier. Lancer : python scripts/paper.py init")
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def save(pf: dict) -> None:
    PORTFOLIO_PATH.write_text(json.dumps(pf, ensure_ascii=False, indent=1),
                              encoding="utf-8")


def init(capital: float = 10_000.0) -> dict:
    pf = {"devise": "USD", "capital_initial": capital, "cash": capital,
          "positions": {}, "transactions": [], "equity_history": []}
    save(pf)
    return pf


def prix_usd(symbol: str) -> float:
    """Dernier cours de clôture converti en USD."""
    if symbol.endswith("=X"):
        raise RuntimeError("Forex exclu du paper trading (levier/lots non modélisés)")
    last = float(get_ohlcv(symbol)["close"].iloc[-1])
    if symbol.endswith(EU_SUFFIXES):
        eurusd = float(get_ohlcv("EURUSD=X")["close"].iloc[-1])
        return last * eurusd
    return last  # USD et USDT≈USD


def acheter(symbol: str, montant: float) -> dict:
    """Achète pour `montant` USD (quantité fractionnaire autorisée)."""
    pf = load()
    if montant <= 0 or montant > pf["cash"]:
        raise RuntimeError(f"Montant invalide ({montant}) — cash disponible : "
                           f"{pf['cash']:.2f} USD")
    px = prix_usd(symbol)
    qty = montant / px
    pos = pf["positions"].get(symbol, {"qty": 0.0, "prix_moyen": 0.0})
    total_qty = pos["qty"] + qty
    pos["prix_moyen"] = (pos["qty"] * pos["prix_moyen"] + qty * px) / total_qty
    pos["qty"] = total_qty
    pf["positions"][symbol] = pos
    pf["cash"] -= montant
    trade = {"quand": _now(), "sens": "ACHAT", "symbole": symbol,
             "qty": round(qty, 6), "prix_usd": round(px, 4),
             "montant_usd": round(montant, 2)}
    pf["transactions"].append(trade)
    save(pf)
    return trade


def vendre(symbol: str, qty: float | None = None) -> dict:
    """Vend `qty` unités (None = toute la position)."""
    pf = load()
    pos = pf["positions"].get(symbol)
    if not pos or pos["qty"] <= 0:
        raise RuntimeError(f"Pas de position sur {symbol}")
    qty = pos["qty"] if qty is None else min(qty, pos["qty"])
    px = prix_usd(symbol)
    montant = qty * px
    pnl = (px - pos["prix_moyen"]) * qty
    pos["qty"] -= qty
    if pos["qty"] <= 1e-9:
        del pf["positions"][symbol]
    pf["cash"] += montant
    trade = {"quand": _now(), "sens": "VENTE", "symbole": symbol,
             "qty": round(qty, 6), "prix_usd": round(px, 4),
             "montant_usd": round(montant, 2), "pnl_usd": round(pnl, 2)}
    pf["transactions"].append(trade)
    save(pf)
    return trade


def etat() -> dict:
    """Valorise le portefeuille au dernier cours et journalise l'équité."""
    pf = load()
    lignes, valeur_positions = [], 0.0
    for sym, pos in pf["positions"].items():
        try:
            px = prix_usd(sym)
            valeur = pos["qty"] * px
            pnl = (px - pos["prix_moyen"]) * pos["qty"]
            pnl_pct = (px / pos["prix_moyen"] - 1) * 100 if pos["prix_moyen"] else 0.0
            lignes.append({"symbole": sym, "qty": round(pos["qty"], 6),
                           "prix_moyen": round(pos["prix_moyen"], 4),
                           "cours": round(px, 4), "valeur_usd": round(valeur, 2),
                           "pnl_usd": round(pnl, 2), "pnl_%": round(pnl_pct, 2)})
            valeur_positions += valeur
        except Exception as exc:
            lignes.append({"symbole": sym, "qty": pos["qty"], "erreur": str(exc)[:60]})

    total = pf["cash"] + valeur_positions
    pf["equity_history"].append({"quand": _now(), "valeur_usd": round(total, 2)})
    pf["equity_history"] = pf["equity_history"][-2000:]
    save(pf)
    return {
        "cash_usd": round(pf["cash"], 2),
        "valeur_positions_usd": round(valeur_positions, 2),
        "valeur_totale_usd": round(total, 2),
        "perf_totale_%": round((total / pf["capital_initial"] - 1) * 100, 2),
        "positions": pd.DataFrame(lignes),
        "nb_transactions": len(pf["transactions"]),
    }


def auto(universes: list[str] | None = None, seuil_achat: float = 40.0,
         seuil_vente: float = -15.0, max_positions: int = 8,
         dry_run: bool = False) -> list[str]:
    """Exécute les signaux du screener en papier.

    Vend toute position dont le score passe sous `seuil_vente` ; achète (parts
    égales du cash) les meilleurs scores >= `seuil_achat` non détenus, dans la
    limite de `max_positions` lignes. Journal des décisions renvoyé.
    """
    universes = universes or ["Actions US", "Actions EU", "Crypto"]
    pf = load()
    symbols = [s for u in universes for s in config.UNIVERS.get(u, [])
               if not s.endswith("=X")]
    table = screener.scan(symbols)
    scores = {r["symbole"]: r["score"] for _, r in table.iterrows()
              if r["score"] is not None}
    journal: list[str] = []

    for sym in list(pf["positions"]):
        sc = scores.get(sym)
        if sc is not None and sc <= seuil_vente:
            if dry_run:
                journal.append(f"VENTE (simulée) {sym} — score {sc}")
            else:
                t = vendre(sym)
                journal.append(f"VENTE {sym} — score {sc}, PnL {t['pnl_usd']} USD")

    pf = load()
    candidats = [(s, sc) for s, sc in sorted(scores.items(), key=lambda x: -x[1])
                 if sc >= seuil_achat and s not in pf["positions"]]
    slots = max_positions - len(pf["positions"])
    for sym, sc in candidats[:max(0, slots)]:
        montant = load()["cash"] / max(1, min(slots, len(candidats)))
        if montant < 50:
            journal.append("Cash insuffisant pour de nouvelles lignes")
            break
        if dry_run:
            journal.append(f"ACHAT (simulé) {sym} — score {sc}, ~{montant:.0f} USD")
        else:
            t = acheter(sym, montant)
            journal.append(f"ACHAT {sym} — score {sc}, {t['montant_usd']} USD "
                           f"@ {t['prix_usd']}")
    if not journal:
        journal.append("Aucun signal actionnable "
                       f"(seuils : achat ≥ {seuil_achat}, vente ≤ {seuil_vente})")
    return journal


# réexport pratique pour le dashboard
label = signals.label
