"""État de santé des briques d'analyse, pour la console DevApp de l'admin.

CE QUE C'EST, ET CE QUE CE N'EST PAS. Une console de DIAGNOSTIC : elle dit ce
qui fonctionne, ce qui est rejeté et pourquoi. Ce n'est PAS un coffre-fort et
elle ne conserve aucun secret — les clés d'API n'y apparaissent que sous forme
de booléen « configurée ou non », jamais leur valeur, jamais un fragment.

POURQUOI ELLE EXISTE. Le projet accumule des briques qui décident SEULES de
s'activer ou non : le modèle de volatilité ne pèse que s'il bat l'EWMA, les
correspondances FRED ne servent que si elles concordent, les composantes
candidates du verdict restent à poids nul tant que leur IC ne les valide pas.
Ces verdicts se prononcent aujourd'hui dans les journaux de GitHub Actions, que
personne ne lit. Une brique qui s'est désactivée en silence est indiscernable
d'une brique qui n'a jamais marché.

OÙ ÇA TOURNE. Ici, chez GitHub Actions, avec le reste du calcul — puis le
résultat part en JSON statique vers l'hébergement, comme tous les autres blocs.
La page PHP ne fait que l'afficher : aucun Python ne tourne sur le mutualisé.

Chaque rubrique est isolée : une brique en panne se signale comme telle et
n'empêche pas les autres de rendre compte.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from marketlab import config


def _horodatage() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _sur(fonction, defaut=None):
    """Exécute une sonde ; renvoie l'erreur au lieu de la propager.

    Une console de diagnostic qui tombe en panne parce qu'une des briques
    qu'elle surveille est en panne ne sert à rien — c'est précisément le moment
    où on la consulte.
    """
    try:
        return fonction()
    except Exception as exc:
        return {"erreur": f"{type(exc).__name__}: {str(exc)[:120]}",
                **(defaut or {})}


# ---------------------------------------------------------------------------
# Sondes
# ---------------------------------------------------------------------------

def _volatilite() -> dict:
    """Couverture du relevé de volatilité réalisée."""
    from marketlab import intraday
    releve = intraday.charger_releve()
    if releve.empty:
        return {"lignes": 0, "titres": 0,
                "avertissement": "relevé vide — la capture intrajournalière "
                                 "n'a encore rien conservé"}
    par_titre = releve.groupby("symbole")["date"].count()
    return {
        "lignes": int(len(releve)),
        "titres": int(releve["symbole"].nunique()),
        "attendus": len(config.SUIVIS),
        "periode": [str(releve["date"].min()), str(releve["date"].max())],
        "seances_par_titre_median": int(par_titre.median()),
        "manquants": [s for s in config.SUIVIS
                      if s not in set(releve["symbole"])],
    }


def _modele_volatilite() -> dict:
    """Verdict de l'arbitrage HAR-RV contre l'EWMA et la marche au hasard."""
    from marketlab import har
    enregistre = har.charger_modele()
    if not enregistre:
        return {"retenu": False,
                "avertissement": "aucun arbitrage effectué à ce jour"}
    horizons = {}
    for h, entree in (enregistre.get("horizons") or {}).items():
        horizons[h] = {
            "retenu": bool(entree.get("har_retenu")),
            "gagnant_qlike": entree.get("gagnant"),
            "gagnant_rmse": entree.get("gagnant_rmse"),
            "scores": entree.get("scores"),
            "n_test": entree.get("n_test"),
            "raison": entree.get("raison"),
        }
    from marketlab import vol_ohlc
    gkyz = vol_ohlc.charger_modele() or {}
    if gkyz.get("retenu"):
        lecture = ("HAR-sur-GKYZ (horizon 20, 5 ans d'OHLC) pilote la largeur "
                   "du cône via har.vol_cible ; l'arbitrage 5 min reste "
                   + ("retenu en repli" if enregistre.get("retenu")
                      else "non retenu"))
    elif enregistre.get("retenu"):
        lecture = "HAR 5 min pilote la largeur du cône"
    else:
        lecture = "aucun modèle retenu : le cône garde son comportement inconditionnel"
    arb = (gkyz.get("arbitrage") or {}).get("scores") or {}
    return {
        "retenu": bool(enregistre.get("retenu")),
        "horizons": horizons,
        "gkyz": {"retenu": bool(gkyz.get("retenu")),
                 "horizon": gkyz.get("horizon"),
                 "scores": arb,
                 "n_test": (gkyz.get("arbitrage") or {}).get("n_test")},
        # Cette phrase a longtemps été fausse : le modèle etait retenu et
        # branche nulle part. Elle est vraie depuis que decision, levels,
        # position et publish passent har.vol_cible() au simulateur — et
        # depuis le 2026-08-02, c'est le modèle GKYZ horizon 20 qui parle
        # en premier dans cet arbitre.
        "lecture": lecture,
    }


def _correspondances_fred() -> dict:
    """Quelles correspondances calendrier → FRED sont prouvées, et lesquelles
    sont rejetées — avec leur écart, qui est le diagnostic lui-même."""
    from marketlab import realises
    rapport = realises.verifier()
    if rapport.empty:
        return {"testees": 0,
                "avertissement": "aucune correspondance testable : le "
                                 "calendrier accumulé ne contient encore aucun "
                                 "indicateur de la table"}
    lignes = [{
        "evenement": r["evenement"], "serie": r["serie"],
        "recalcule": r["recalcule"], "imprime": r["imprime"],
        "ecart_%": r["ecart_%"], "concorde": bool(r["concorde"]),
    } for _, r in rapport.iterrows()]
    return {
        "testees": len(lignes),
        "valides": int(rapport["concorde"].sum()),
        "table": len(realises.CORRESPONDANCES.get("USD", {})),
        "details": sorted(lignes, key=lambda x: (x["concorde"], x["evenement"])),
    }


def _surprises() -> dict:
    """État de l'indice de surprise économique."""
    from marketlab import surprise
    calendrier = surprise.charger_calendrier()
    mesures = surprise.surprises()
    scores = surprise.score_par_devise()
    etat = {
        "calendrier_accumule": int(len(calendrier)),
        "surprises": int(len(mesures)),
        "scores": scores,
    }
    if not mesures.empty and "source" in mesures.columns:
        etat["par_source"] = mesures["source"].value_counts().to_dict()
    if not scores:
        etat["avertissement"] = (
            "aucun score : il faut soit une correspondance FRED prouvée avec "
            "une publication déjà sortie, soit deux publications successives "
            "d'un même indicateur pour le chaînage")
    return etat


def _journal() -> dict:
    """Track record du moteur de décision."""
    from marketlab import decision
    if not decision.JOURNAL.exists():
        return {"verdicts": 0, "avertissement": "journal absent"}
    journal = pd.read_csv(decision.JOURNAL)
    etat = {"verdicts": int(len(journal))}
    if "date" in journal.columns and len(journal):
        etat["periode"] = [str(journal["date"].min()), str(journal["date"].max())]
    candidates = [c for c in journal.columns if c.startswith("c_")]
    etat["composantes_journalisees"] = sorted(candidates)
    return etat


# Les clés d'API et le périmètre ne figurent PAS ici : `coulisses.sources()`
# et `coulisses.perimetre()` les couvrent déjà. Deux sondes qui répondent à la même
# question finissent toujours par se contredire, et c'est alors la console
# qu'on cesse de croire.
def _duel_previsionnistes() -> dict:
    from marketlab import implicite
    return implicite.comparer_previsionnistes()


SONDES = {
    "volatilite_realisee": _volatilite,
    "modele_volatilite": _modele_volatilite,
    "correspondances_fred": _correspondances_fred,
    "surprises": _surprises,
    # Nos prévisions de volatilité contre celle du MARCHÉ (options), jugées au
    # QLIKE quand les fenêtres arrivent à maturité. Tant que trop peu de duels
    # sont mûrs, la sonde le dit — même contrat que HAR contre EWMA.
    "duel_previsionnistes": _duel_previsionnistes,
    "journal": _journal,
}


def etat() -> dict:
    """Rapport complet. Ne lève jamais : c'est un diagnostic."""
    rapport = {"genere_le": _horodatage()}
    for nom, sonde in SONDES.items():
        rapport[nom] = _sur(sonde)
    rapport["alertes"] = _resumer(rapport)
    return rapport


def _resumer(rapport: dict) -> list[dict]:
    """Ce qui mérite l'attention, en tête de page.

    Une console qui déverse cinquante chiffres sans les hiérarchiser se lit
    aussi mal qu'un journal de CI — c'est justement ce qu'on cherche à
    remplacer.
    """
    points = []

    for nom, sonde in rapport.items():
        if isinstance(sonde, dict) and sonde.get("erreur"):
            points.append({"niveau": "erreur",
                           "texte": f"Sonde « {nom} » en échec : {sonde['erreur']}"})

    vol = rapport.get("volatilite_realisee") or {}
    if isinstance(vol, dict) and vol.get("manquants"):
        points.append({"niveau": "attention",
                       "texte": f"{len(vol['manquants'])} titre(s) sans "
                                f"volatilité relevée : "
                                f"{', '.join(vol['manquants'][:6])}"})

    fred_c = rapport.get("correspondances_fred") or {}
    if isinstance(fred_c, dict) and fred_c.get("testees"):
        rejetees = fred_c["testees"] - fred_c.get("valides", 0)
        if rejetees:
            points.append({"niveau": "info",
                           "texte": f"{rejetees} correspondance(s) FRED "
                                    f"rejetée(s) — écartées du calcul, pas "
                                    f"utilisées à tort."})

    modele = rapport.get("modele_volatilite") or {}
    if isinstance(modele, dict) and not modele.get("retenu"):
        points.append({"niveau": "info",
                       "texte": "Modèle HAR non retenu : il n'a pas battu "
                                "l'EWMA en place. Le cône est inchangé."})

    surp = rapport.get("surprises") or {}
    if isinstance(surp, dict) and surp.get("avertissement"):
        points.append({"niveau": "info", "texte": surp["avertissement"]})

    duel = rapport.get("duel_previsionnistes") or {}
    if isinstance(duel, dict) and duel.get("mesurable"):
        points.append({"niveau": "info",
                       "texte": f"Prévision de volatilité : {duel.get('gagnant')} "
                                f"gagne le duel sur {duel.get('n_duels')} "
                                f"fenêtres mûres."})

    if not points:
        points.append({"niveau": "ok", "texte": "Aucun point d'attention."})
    return points
