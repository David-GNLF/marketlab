"""Valeurs RÉALISÉES des indicateurs macro, via FRED — pour une surprise sans retard.

POURQUOI CE MODULE. Le calendrier ForexFactory donne le consensus (`forecast`)
mais jamais le résultat. `surprise.py` le reconstitue par chaînage, au prix d'un
cycle de retard : la surprise d'un indicateur mensuel n'est connue qu'au mois
suivant. FRED, lui, publie le résultat le jour même. Croiser les deux —
consensus ForexFactory × réalisé FRED — donne la surprise SANS retard.

    surprise = FRED(période de référence) − ForexFactory(prévision)

LIMITE À CONNAÎTRE, ET ELLE EST STRUCTURELLE. Sans clé d'API, FRED ne sert que
la DERNIÈRE version d'une série, pas le millésime publié le jour J. Or les
statistiques sont révisées : l'emploi américain l'est deux fois, le PIB
davantage. Ce qui a fait bouger le marché, c'est la première estimation — pas
la valeur corrigée qu'on lit aujourd'hui. Pour les indices de prix, la
révision est marginale ; pour l'emploi ou le PIB, elle ne l'est pas.

D'OÙ LA VÉRIFICATION AUTOMATIQUE. Plutôt que de faire confiance à la table de
correspondance et d'espérer que les révisions soient petites, on MESURE : la
valeur « précédente » d'une publication du calendrier est le résultat de la
publication d'avant, connu et imprimé par ForexFactory. On recalcule la même
chose depuis FRED et on compare. Une correspondance dont l'écart dépasse la
tolérance est ÉCARTÉE — mauvaise série, mauvaise transformation, mauvaise
unité, mauvais décalage ou révisions trop lourdes, peu importe la cause : elle
ne sert pas. `correspondances_valides()` fait ce tri, et seules les survivantes
alimentent la surprise.

PÉRIMÈTRE : dollar américain. FRED couvre l'international de façon trop
parcellaire et trop retardée pour qu'on prétende y traiter les autres devises ;
elles restent sur le chaînage de `surprise.py`. Ce n'est pas si limitant :
le dollar est d'un côté de presque toutes les paires suivies.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from marketlab import eco_calendar
from marketlab.data import fred

# Tolérance relative de la vérification : écart accepté entre la valeur
# recalculée depuis FRED et celle imprimée par ForexFactory. 10 % laisse passer
# les arrondis d'affichage et les révisions mineures, pas une erreur d'unité
# (qui se trompe d'un facteur 1000) ni une mauvaise série.
TOLERANCE = 0.10

# Écart absolu toléré quand la grandeur est proche de zéro, où le relatif
# n'a plus de sens (un CPI passant de 0,0 à 0,1 est un écart relatif infini).
TOLERANCE_ABSOLUE = 0.05

# Correspondances calendrier → FRED.
#   serie      — identifiant FRED
#   transfo    — niveau | diff | pct_m | pct_a | pct_t_annualise
#   facteur    — multiplicateur pour retrouver l'unité imprimée par le flux
#                (ForexFactory écrit « 147K » = 147 000 alors que PAYEMS est en
#                milliers : facteur 1000)
#   decalage   — nombre de mois entre le mois de PUBLICATION et la période de
#                RÉFÉRENCE (l'emploi de juin sort début juillet : décalage 1)
#   frequence  — mensuelle | hebdomadaire | trimestrielle
CORRESPONDANCES = {
    "USD": {
        "Non-Farm Employment Change":
            {"serie": "PAYEMS", "transfo": "diff", "facteur": 1000, "decalage": 1},
        "Unemployment Rate":
            {"serie": "UNRATE", "transfo": "niveau", "facteur": 1, "decalage": 1},
        "Average Hourly Earnings m/m":
            {"serie": "CES0500000003", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "CPI m/m":
            {"serie": "CPIAUCSL", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "CPI y/y":
            {"serie": "CPIAUCSL", "transfo": "pct_a", "facteur": 1, "decalage": 1},
        "Core CPI m/m":
            {"serie": "CPILFESL", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "PPI m/m":
            {"serie": "PPIFIS", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "Retail Sales m/m":
            {"serie": "RSAFS", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "Core Retail Sales m/m":
            {"serie": "RSFSXMV", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "Industrial Production m/m":
            {"serie": "INDPRO", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "Core PCE Price Index m/m":
            {"serie": "PCEPILFE", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "Personal Spending m/m":
            {"serie": "PCE", "transfo": "pct_m", "facteur": 1, "decalage": 1},
        "Housing Starts":
            {"serie": "HOUST", "transfo": "niveau", "facteur": 1000, "decalage": 1},
        "Building Permits":
            {"serie": "PERMIT", "transfo": "niveau", "facteur": 1000, "decalage": 1},
        "Trade Balance":
            {"serie": "BOPGSTB", "transfo": "niveau", "facteur": 1e6, "decalage": 2},
        "Unemployment Claims":
            {"serie": "ICSA", "transfo": "niveau", "facteur": 1, "decalage": 0,
             "frequence": "hebdomadaire"},
        "UoM Consumer Sentiment":
            {"serie": "UMCSENT", "transfo": "niveau", "facteur": 1, "decalage": 0},
    },
}

# Indicateurs volontairement ABSENTS de la table, et pourquoi : le PIB
# (« Advance/Prelim/Final GDP q/q ») est publié trois fois pour un même
# trimestre et lourdement révisé — la dernière version FRED n'a plus grand
# rapport avec la première estimation, seule à avoir fait bouger le marché.
# Les indices ISM ne sont plus redistribués par FRED (licence).


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------

def transformer(serie: pd.Series, transfo: str) -> pd.Series:
    """Amène une série FRED à l'unité dans laquelle le calendrier la publie."""
    serie = serie.sort_index()
    if transfo == "niveau":
        return serie
    if transfo == "diff":
        return serie.diff()
    if transfo == "pct_m":
        return serie.pct_change() * 100
    if transfo == "pct_a":
        return serie.pct_change(12) * 100
    if transfo == "pct_t_annualise":
        return ((serie / serie.shift(1)) ** 4 - 1) * 100
    raise ValueError(f"Transformation inconnue : {transfo}")


def reference(date_publication, decalage: int, frequence: str = "mensuelle"):
    """Période de RÉFÉRENCE visée par une publication.

    L'emploi de juin est publié début juillet : la publication du 03/07 porte
    sur la période du 01/06. Confondre les deux compare un chiffre au consensus
    d'un autre mois, ce qui produit des surprises absurdes mais crédibles.
    """
    d = pd.Timestamp(date_publication).normalize()
    if frequence == "hebdomadaire":
        return d  # traité à part : on prend la dernière observation antérieure
    mois = d.to_period("M") - decalage
    return mois.to_timestamp()


def decimales(valeur) -> int:
    """Nombre de décimales significatives d'une valeur (0.3 → 1, 187000 → 0)."""
    try:
        texte = f"{float(valeur):.10g}"
    except (TypeError, ValueError):
        return 0
    if "e" in texte or "E" in texte:
        return 0
    return len(texte.split(".")[1]) if "." in texte else 0


def valeur_realisee(devise: str, evenement: str, date_publication,
                    precision: int | None = None) -> float | None:
    """Valeur réellement publiée, dans l'unité du calendrier. None si hors table.

    `precision` — nombre de décimales auquel arrondir. INDISPENSABLE quand on
    calcule une surprise : FRED sert un indice à pleine précision (Core PCE
    recalculé à 0,320051) alors que le marché a comparé le chiffre ARRONDI que
    l'agence publie (0,3) au consensus (0,3), soit aucune surprise. Sans cet
    arrondi, chaque indicateur fabriquerait une petite surprise de pur bruit
    d'arrondi, systématiquement, et l'indice agrégé serait du sable.
    """
    regle = CORRESPONDANCES.get(devise, {}).get(evenement)
    if regle is None:
        return None
    brute = None
    try:
        # Millésime d'abord : la série telle qu'elle était connue le jour de la
        # publication, donc le chiffre réellement imprimé ce jour-là. C'est le
        # seul qui ait fait bouger le marché. Repli sur les valeurs révisées si
        # aucune clé n'est configurée.
        brute = fred.serie_millesime(regle["serie"], date_publication)
    except Exception:
        brute = None
    if brute is None or brute.empty:
        try:
            brute = fred.get_series(regle["serie"], lookback_years=12)
        except Exception:
            return None
    if brute is None or brute.empty:
        return None

    serie = transformer(brute, regle["transfo"]).dropna()
    if serie.empty:
        return None

    frequence = regle.get("frequence", "mensuelle")
    if frequence == "hebdomadaire":
        anterieures = serie[serie.index < pd.Timestamp(date_publication).normalize()]
        if anterieures.empty:
            return None
        valeur = float(anterieures.iloc[-1])
    else:
        cible = reference(date_publication, regle["decalage"], frequence)
        if cible not in serie.index:
            return None
        valeur = float(serie.loc[cible])
    valeur *= regle["facteur"]
    return valeur if precision is None else round(valeur, precision)


def valeur_precedente(devise: str, evenement: str, date_publication,
                      precision: int | None = None) -> float | None:
    """Valeur de la publication PRÉCÉDENTE — celle que le flux imprime en
    « précédent ». Sert de témoin à la vérification."""
    regle = CORRESPONDANCES.get(devise, {}).get(evenement)
    if regle is None:
        return None
    frequence = regle.get("frequence", "mensuelle")
    d = pd.Timestamp(date_publication).normalize()
    if frequence == "hebdomadaire":
        # Millésime d'abord, comme `valeur_realisee` : sur une série
        # hebdomadaire, l'état de la série au jour de la publication tranche
        # sans ambiguïté quelle semaine venait d'être publiée et laquelle
        # était la précédente. Sur la série révisée, la question n'a pas de
        # réponse — toutes les semaines existent déjà.
        # Millésime de LA VEILLE, et sa dernière observation. C'est la seule
        # formulation non ambiguë.
        #
        # Compter les semaines à rebours ne marche pas : « l'avant-dernière
        # observation » désigne la semaine précédente si la publication du jour
        # est déjà chez FRED, mais celle d'avant si elle ne l'est pas encore
        # (les inscriptions sortent à 8h30 New York, et FRED suit avec un
        # délai). La réponse changeait donc selon l'heure d'interrogation —
        # mesuré : 209 000 au lieu des 187 000 imprimés. Ce que FRED savait la
        # VEILLE, en revanche, est la valeur précédente par construction.
        veille = d - pd.Timedelta(days=1)
        brute = None
        try:
            brute = fred.serie_millesime(regle["serie"], veille)
        except Exception:
            brute = None
        if brute is None or brute.empty:
            try:
                brute = fred.get_series(regle["serie"], lookback_years=12)
            except Exception:
                return None
        try:
            serie = transformer(brute, regle["transfo"]).dropna()
        except Exception:
            return None
        anterieures = serie[serie.index < d]
        if anterieures.empty:
            return None
        valeur = float(anterieures.iloc[-1]) * regle["facteur"]
        return valeur if precision is None else round(valeur, precision)
    # Mensuel/trimestriel : reculer d'une période de PUBLICATION en gardant le
    # jour du mois.
    #
    # PIÈGE RÉVÉLÉ PAR LES MILLÉSIMES. La version précédente ramenait au 1er du
    # mois (`to_period("M") - 1`). Avec les valeurs révisées, cela passait
    # inaperçu : toute la série existe, quelle que soit la date qu'on prétend
    # être. Avec un millésime, demander la valeur de mai en se plaçant au
    # 1er juin renvoie VIDE — elle n'était pas encore publiée à cette date, le
    # rapport sortant vers le 26. Il faut donc reculer de mois en gardant le
    # jour : 30 juillet → 30 juin.
    pas = 3 if frequence == "trimestrielle" else 1
    return valeur_realisee(devise, evenement, d - pd.DateOffset(months=pas),
                           precision=precision)


# ---------------------------------------------------------------------------
# Vérification des correspondances
# ---------------------------------------------------------------------------

def _concordent(recalcule: float, imprime: float) -> bool:
    if recalcule is None or imprime is None:
        return False
    if not np.isfinite(recalcule) or not np.isfinite(imprime):
        return False
    ecart = abs(recalcule - imprime)
    if ecart <= TOLERANCE_ABSOLUE:
        return True
    denominateur = max(abs(imprime), 1e-9)
    return ecart / denominateur <= TOLERANCE


def verifier(evenements: pd.DataFrame | None = None) -> pd.DataFrame:
    """Confronte chaque correspondance au « précédent » imprimé par le flux.

    C'est la seule preuve disponible sans clé d'API : si la série, la
    transformation, l'unité et le décalage sont justes, la valeur recalculée
    depuis FRED doit retrouver le chiffre que ForexFactory affiche déjà.

    Renvoie une ligne par correspondance testée : recalculé, imprimé, écart,
    et verdict.
    """
    from marketlab import surprise  # import tardif : évite un cycle

    # Par défaut on vérifie sur l'historique ACCUMULÉ, pas sur la seule semaine
    # en cours : sinon un indicateur absent du calendrier de la semaine serait
    # « non vérifié » donc écarté, et la couverture clignoterait d'une semaine
    # à l'autre. L'historique, lui, ne fait que grandir.
    if evenements is None:
        evenements = surprise.charger_calendrier()
    colonnes = ["devise", "evenement", "date", "serie",
                "recalcule", "imprime", "ecart_%", "concorde"]
    if evenements is None or evenements.empty:
        return pd.DataFrame(columns=colonnes)

    champ_date = "date" if "date" in evenements.columns else "quand"
    # la publication la PLUS RÉCENTE de chaque indicateur fait foi : une
    # correspondance se juge sur l'état actuel de la série, pas sur un chiffre
    # d'il y a six mois qui a pu être révisé depuis
    evenements = evenements.sort_values(champ_date, ascending=False)

    lignes = []
    vus = set()
    for _, ev in evenements.iterrows():
        devise, titre = ev.get("devise", ""), ev.get("evenement", "")
        regle = CORRESPONDANCES.get(devise, {}).get(titre)
        if regle is None or (devise, titre) in vus:
            continue
        imprime = surprise.nombre(ev.get("precedent"))
        if imprime is None:
            continue
        vus.add((devise, titre))
        date_pub = pd.Timestamp(ev[champ_date]).normalize()
        # arrondi à la précision du chiffre imprimé : on compare ce que le
        # marché a lu, pas la pleine précision de l'indice sous-jacent
        recalcule = valeur_precedente(devise, titre, date_pub,
                                      precision=decimales(imprime))
        ecart_pct = (abs(recalcule - imprime) / max(abs(imprime), 1e-9) * 100
                     if recalcule is not None else np.nan)
        lignes.append({
            "devise": devise, "evenement": titre,
            "date": date_pub.date().isoformat(), "serie": regle["serie"],
            "recalcule": recalcule, "imprime": imprime,
            "ecart_%": round(float(ecart_pct), 2) if np.isfinite(ecart_pct) else None,
            "concorde": _concordent(recalcule, imprime),
        })
    return pd.DataFrame(lignes, columns=colonnes)


_MEMO: dict = {}


def correspondances_valides(rapport: pd.DataFrame | None = None) -> set:
    """Couples (devise, indicateur) dont la correspondance est PROUVÉE.

    Une correspondance non vérifiée n'est pas utilisée : mieux vaut une
    surprise absente qu'une surprise fausse, qui aurait l'air d'un signal.

    Mémorisé pour la durée du processus : la publication calcule 32 salles de
    marché, et refaire la vérification à chaque fois relirait toute la table
    FRED à chaque symbole. Le cache disque de `fred.get_series` (24 h) fait le
    reste entre deux exécutions.
    """
    if rapport is None and "valides" in _MEMO:
        return _MEMO["valides"]
    calcule = verifier() if rapport is None else rapport
    if calcule is None or calcule.empty:
        resultat = set()
    else:
        ok = calcule[calcule["concorde"]]
        resultat = {(r["devise"], r["evenement"]) for _, r in ok.iterrows()}
    if rapport is None:
        _MEMO["valides"] = resultat
    return resultat
