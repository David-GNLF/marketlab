"""Rétro-journalisation : rejoue les verdicts sur 2 ans, sans regard du futur.

    .venv\\Scripts\\python scripts\\backfill_journal.py [--semaines 104] [--pas 5]

Pour chaque actif suivi et chaque semaine des deux dernières années, le verdict
est recalculé en n'utilisant QUE les données disponibles à cette date (tranche
`df[:t]` — les indicateurs, simulations et analogues ne voient rien au-delà).
Ces verdicts datés alimentent le journal, sont évalués contre les rendements
réellement advenus, puis `decision.calibrer()` en tire des pondérations
APPRISES — l'outil s'étalonne sur deux ans de réalité dès aujourd'hui.

Honnêteté du procédé — ce que le rétro-verdict CONTIENT et NE CONTIENT PAS :
- inclus : technique, prévision (Monte Carlo), analogues, et les moteurs
  historisables (carry forex via les séries de taux datées ; taux réels +
  dollar pour les métaux précieux) — mêmes barèmes que decision.py ;
- exclus, faute d'historique fiable : fondamentaux (yfinance ne donne que le
  présent), sentiment presse, saisonnalité, structure à terme (les contrats
  différés d'il y a deux ans ne cotent plus). Les composantes absentes sont
  renormalisées, comme en production ;
- pas de vetos (le plan complet par date serait prohibitif) : les avis rétro
  sont Favorable/Neutre/Défavorable, jamais « S'abstenir » ;
- les verdicts hebdomadaires à horizon 20 séances SE CHEVAUCHENT : les
  échantillons ne sont pas indépendants, l'IC reste indicatif — c'est le
  mélange progressif (λ) qui protège du sur-ajustement.

Les lignes portent `origine=retro` ; un verdict quotidien du même jour prime.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from marketlab import config, decision, forecast, indicators, score_history
from marketlab.data import fred, get_ohlcv

# Le périmètre vient de config.FICHES et de nulle part ailleurs. Recopié à
# la main, il avait déjà divergé : les quatre titres asiatiques (Toyota,
# Tencent, TSMC, Samsung) ont une salle de marché et un verdict quotidien,
# mais AUCUN verdict rétro-journalisé — ils ne pesaient donc rien dans
# l'apprentissage des pondérations.
ACTIFS = list(config.FICHES)
HORIZON = 20
MIN_HISTORIQUE = 320          # séances nécessaires avant le premier verdict
METAUX_PRECIEUX = {"GC=F", "SI=F"}


def _clip(x, borne=100.0):
    return float(max(-borne, min(borne, x)))


# --- moteurs historisables ----------------------------------------------------

def _series_taux() -> dict:
    """Précharge les séries de taux (4 ans) pour le carry point-in-time."""
    series = {}
    for devise, ids in decision.drivers.TAUX_DEVISES.items():
        for sid in ids:
            try:
                s = fred.get_series(sid, lookback_years=4)
                if len(s):
                    series[devise] = s
                    break
            except Exception:
                continue
    return series


def _note_moteurs(symbole: str, t: pd.Timestamp, taux: dict,
                  reels: pd.Series, dollar: pd.Series) -> float | None:
    """Même barème que decision._composante_moteurs, à la date t."""
    if symbole.endswith("=X"):
        base, cotee = symbole[:3], symbole[3:6]
        if base not in taux or cotee not in taux:
            return None
        sb, sc = taux[base][taux[base].index <= t], taux[cotee][taux[cotee].index <= t]
        if sb.empty or sc.empty:
            return None
        diff = float(sb.iloc[-1] - sc.iloc[-1])
        t6 = t - pd.DateOffset(months=6)
        sb6, sc6 = sb[sb.index <= t6], sc[sc.index <= t6]
        dyn = (diff - float(sb6.iloc[-1] - sc6.iloc[-1])
               if len(sb6) and len(sc6) else 0.0)
        return _clip(_clip(diff * 20, 60) + _clip(dyn * 40, 40))
    if symbole in METAUX_PRECIEUX:
        r = reels[reels.index <= t]
        d = dollar[dollar.index <= t]
        if len(r) < 70 or len(d) < 70:
            return None
        t3 = t - pd.DateOffset(months=3)
        r3, d3 = r[r.index <= t3], d[d.index <= t3]
        if r3.empty or d3.empty:
            return None
        var_reels = float(r.iloc[-1] - r3.iloc[-1])
        var_dollar = (float(d.iloc[-1]) / float(d3.iloc[-1]) - 1) * 100
        return _clip(_clip(-var_reels * 150, 70) + _clip(-var_dollar * 10, 30))
    return None  # structure à terme non historisable proprement


# --- verdict point-in-time ----------------------------------------------------

def retro_verdicts(symbole: str, semaines: int, pas: int, taux: dict,
                   reels: pd.Series, dollar: pd.Series) -> list[dict]:
    df = indicators.enrich(get_ohlcv(symbole, lookback_days=1825))
    scores = score_history.score_series(df)   # rolling : sûr point-in-time
    lignes = []
    positions = range(len(df) - 1 - HORIZON,
                      max(MIN_HISTORIQUE, len(df) - 1 - semaines * pas) - 1,
                      -pas)
    for i in positions:
        t = df.index[i]
        tranche = df.iloc[: i + 1]
        composantes = {"technique": float(scores.iloc[i])}
        try:
            proj = forecast.projeter(tranche, horizon=HORIZON, n_sim=3000,
                                     lookback=500)
            composantes["prevision"] = decision._composante_prevision(proj)["note"]
        except Exception:
            pass
        try:
            ana = forecast.analogues(tranche, horizon=HORIZON)
            composantes["analogues"] = decision._composante_analogues(ana)["note"]
        except Exception:
            pass
        m = _note_moteurs(symbole, t, taux, reels, dollar)
        if m is not None:
            composantes["moteurs"] = m

        if len(composantes) < 2:
            continue
        poids_total = sum(decision.POIDS[c] for c in composantes)
        note = sum(v * decision.POIDS[c]
                   for c, v in composantes.items()) / poids_total
        avis = ("Favorable" if note >= 30 else
                "Défavorable" if note <= -30 else "Neutre")
        ligne = {"date": t.strftime("%Y-%m-%d"), "symbole": symbole,
                 "avis": avis, "note": round(note, 1),
                 "prix": round(float(df["close"].iloc[i]), 4),
                 "horizon": HORIZON, "origine": "retro"}
        for c, v in composantes.items():
            ligne[f"c_{c}"] = round(v, 1)
        lignes.append(ligne)
    return lignes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semaines", type=int, default=104)
    parser.add_argument("--pas", type=int, default=5)
    args = parser.parse_args()

    print(f"Préchargement des séries de taux…", flush=True)
    taux = _series_taux()
    reels = fred.get_series("DFII10", lookback_years=4)
    dollar = fred.get_series("DTWEXBGS", lookback_years=4)
    print(f"  taux disponibles : {sorted(taux)}", flush=True)

    toutes, debut = [], time.time()
    for n, symbole in enumerate(ACTIFS, 1):
        try:
            lignes = retro_verdicts(symbole, args.semaines, args.pas,
                                    taux, reels, dollar)
            toutes.extend(lignes)
            print(f"  [{n:2}/{len(ACTIFS)}] {symbole:10} {len(lignes):4} verdicts "
                  f"rétro ({time.time() - debut:5.0f} s)", flush=True)
        except Exception as exc:
            print(f"  [{n:2}/{len(ACTIFS)}] {symbole:10} ECHEC : "
                  f"{str(exc)[:70]}", flush=True)

    if not toutes:
        print("Aucun verdict rétro généré.")
        return 1

    retro = pd.DataFrame(toutes)
    if decision.JOURNAL.exists():
        existant = pd.read_csv(decision.JOURNAL)
        if "origine" not in existant.columns:
            existant["origine"] = "quotidien"
        # l'existant passe en dernier : un verdict quotidien du même jour prime
        journal = pd.concat([retro, existant], ignore_index=True)
    else:
        journal = retro
    # La clé inclut l'HORIZON, comme dans decision.journaliser : le même
    # titre, le même jour, produit un verdict par horizon suivi, et ce sont
    # deux paris différents. Sans l'horizon, un passage de ce script
    # supprimait une des deux traces — donc tout le bilan à 5 séances, et
    # la matière du robot « claude5 ».
    journal = journal.drop_duplicates(subset=["date", "symbole", "horizon"],
                                      keep="last")
    journal = journal.sort_values(["date", "symbole"]).reset_index(drop=True)
    journal.to_csv(decision.JOURNAL, index=False)
    print(f"\nJournal : {len(journal)} lignes "
          f"({(journal['origine'] == 'retro').sum()} rétro) "
          f"-> {decision.JOURNAL}", flush=True)

    print("\n== Évaluation contre les rendements réels + calibrage ==", flush=True)
    rapport = decision.calibrer()
    print(f"  {rapport['statut']}")
    for nom, d in rapport.get("ic_par_composante", {}).items():
        print(f"    {nom:14} IC {d.get('ic')} (n={d.get('n')})")
    if rapport.get("poids"):
        print("  pondérations apprises :")
        for nom, p in rapport["poids"].items():
            print(f"    {nom:14} {decision.POIDS[nom]:.3f} -> {p:.3f}")

    print("\n== Bilan par catégorie d'avis ==", flush=True)
    b = decision.bilan()
    print(f"  {b['verdicts_evalues']} verdicts évalués depuis "
          f"{b.get('premiere_date')}")
    for ligne in b.get("par_avis", []):
        print(f"    {ligne['avis']:12} n={ligne['n']:5} rendement moyen "
              f"{ligne['rendement_moyen_%']:+6.2f} % | réussite "
              f"{ligne['taux_reussite_%']} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
