"""Audit de cohérence de MarketLab — exécuté à chaque publication.

    python scripts/verifier_coherence.py            # config + modules
    python scripts/verifier_coherence.py --site     # + contenu de site/donnees

Raison d'être : les univers (Asie, matières premières…) avaient dérivé —
présents dans les alertes mais absents du screener, de l'historisation et
des fiches. Cet audit rend la dérive IMPOSSIBLE : toute liste locale qui
s'écarte de la source de vérité (config.SUIVIS / config.FICHES) fait
échouer la publication, et l'échec déclenche l'alerte ntfy du workflow.
"""

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from marketlab import alerts, config, publish

ECHECS = []


def verifier(nom: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {nom}")
    else:
        print(f"  FAIL {nom}" + (f" — {detail}" if detail else ""))
        ECHECS.append(nom)


def audit_config() -> None:
    print("== Config : sources de vérité ==")
    suivis = set(config.SUIVIS)
    verifier("SUIVIS sans doublon", len(config.SUIVIS) == len(suivis),
             f"{len(config.SUIVIS)} entrées, {len(suivis)} uniques")
    for nom_univers, membres in config.UNIVERS.items():
        if nom_univers == "BRVM":
            continue  # données manuelles, hors périmètre automatique
        manquants = set(membres) - suivis
        verifier(f"UNIVERS[{nom_univers}] ⊆ SUIVIS", not manquants,
                 f"absents : {sorted(manquants)}")
    verifier("FICHES ⊆ SUIVIS", set(config.FICHES) <= suivis,
             f"hors SUIVIS : {sorted(set(config.FICHES) - suivis)}")
    verifier("FICHES sans doublon",
             len(config.FICHES) == len(set(config.FICHES)))
    inconnus = set(config.NOMS_ACTIFS) - suivis
    verifier("NOMS_ACTIFS ⊆ SUIVIS", not inconnus,
             f"noms orphelins : {sorted(inconnus)}")


def audit_modules() -> None:
    print("== Modules : périmètres alignés ==")
    verifier("publish.TITRES_DETAILLES == config.FICHES",
             list(publish.TITRES_DETAILLES) == list(config.FICHES))
    univers_alertes = set(alerts.DEFAULT_UNIVERSES)
    attendus = set(config.UNIVERS) - {"BRVM", "Indices"}
    verifier("alertes couvrent tous les univers tradables",
             attendus <= univers_alertes,
             f"absents des alertes : {sorted(attendus - univers_alertes)}")
    # scripts non importables : vérification par le source
    src_historiser = (RACINE / "scripts" / "historiser.py").read_text(
        encoding="utf-8")
    verifier("historiser.py utilise config.SUIVIS",
             "config.SUIVIS" in src_historiser)
    src_sentiment = (RACINE / "marketlab" / "sentiment_marche.py").read_text(
        encoding="utf-8")
    verifier("largeur de marché inclut l'Asie",
             "ACTIONS_ASIE" in src_sentiment)


def audit_site() -> None:
    print("== Site : contenu publié cohérent ==")
    donnees = RACINE / "site" / "donnees"
    if not (donnees / "meta.json").exists():
        verifier("site généré", False, "meta.json absent — lancer la génération")
        return
    meta = json.loads((donnees / "meta.json").read_text(encoding="utf-8"))
    screener = json.loads((donnees / "screener.json").read_text(encoding="utf-8"))
    verdicts = json.loads((donnees / "verdicts.json").read_text(encoding="utf-8"))

    symboles_screener = {l["symbole"] for l in screener}
    verifier("screener == SUIVIS",
             symboles_screener == set(config.SUIVIS),
             f"écart : ±{sorted(symboles_screener ^ set(config.SUIVIS))[:6]}")

    fiches_disque = {p.stem for p in (donnees / "titres").glob("*.json")}
    verifier("fiches ⊇ FICHES", set(config.FICHES) <= fiches_disque,
             f"manquantes : {sorted(set(config.FICHES) - fiches_disque)[:6]}")
    verifier("meta.titres == FICHES", set(meta.get("titres", []))
             == set(config.FICHES))

    symboles_verdicts = {d["symbole"] for d in verdicts["dossiers"]}
    verifier("verdicts == FICHES", symboles_verdicts == set(config.FICHES),
             f"écart : ±{sorted(symboles_verdicts ^ set(config.FICHES))[:6]}")
    # Les contrôles qui suivent excluent les dossiers en erreur — sans quoi
    # ils signaleraient deux fois le même incident. Mais ils ne peuvent pas
    # être les SEULS : `decision.verdicts()` remplace un dossier en échec
    # par {"symbole", "erreur"}, si bien que le périmètre reste complet et
    # que 36 verdicts ratés passaient l'audit sans un mot. C'est la seule
    # étape bloquante avant l'envoi : elle doit voir les échecs.
    en_erreur = [d["symbole"] for d in verdicts["dossiers"] if "erreur" in d]
    part = len(en_erreur) / max(len(verdicts["dossiers"]), 1)
    verifier(f"verdicts calculés ({len(verdicts['dossiers']) - len(en_erreur)}"
             f"/{len(verdicts['dossiers'])})", part <= 0.10,
             f"{len(en_erreur)} en échec : {sorted(en_erreur)[:6]}")
    sans_conclusion = [d["symbole"] for d in verdicts["dossiers"]
                       if "erreur" not in d and not d.get("conclusion")]
    verifier("chaque verdict porte une conclusion", not sans_conclusion,
             str(sans_conclusion[:6]))
    sans_classe = [d["symbole"] for d in verdicts["dossiers"]
                   if "erreur" not in d and not d.get("classe")]
    verifier("chaque verdict porte une classe", not sans_classe,
             str(sans_classe[:6]))

    # Horizon court : même périmètre, même exigences. Sans ce garde-fou, le
    # second robot pourrait se retrouver à trader une liste amputée sans que
    # personne ne s'en aperçoive.
    courts = verdicts.get("dossiers_court") or []
    verifier("verdicts courts == FICHES",
             {d["symbole"] for d in courts} == set(config.FICHES),
             f"écart : ±{sorted({d['symbole'] for d in courts} ^ set(config.FICHES))[:6]}")
    verifier("verdicts courts au bon horizon",
             all(d.get("horizon") == verdicts.get("horizon_court")
                 for d in courts if "erreur" not in d),
             f"attendu {verdicts.get('horizon_court')}")
    verifier("les deux horizons sont distincts",
             verdicts.get("horizon_court") != verdicts.get("horizon_officiel"))


def main() -> int:
    audit_config()
    audit_modules()
    if "--site" in sys.argv:
        audit_site()
    print()
    if ECHECS:
        print(f"AUDIT ÉCHOUÉ : {len(ECHECS)} incohérence(s) — {ECHECS}")
        return 1
    print("AUDIT OK : périmètres cohérents de la config au site publié.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
