"""Séries de cotation prêtes à tracer : bougies quotidiennes et 5 minutes.

POURQUOI CE MODULE. Jusqu'ici la fiche d'un titre ne publiait que la CLÔTURE
(260 valeurs, plus deux moyennes mobiles). C'est assez pour une courbe, et
strictement rien d'autre : sans ouverture, plus haut et plus bas, on ne peut
tracer ni bougie, ni barre OHLC, ni mesurer l'amplitude d'une séance ; sans
volume, on ne sait pas si un mouvement a été suivi ; sans barres 5 minutes, il
n'existe aucune vue « aujourd'hui » et donc aucun moyen de placer une entrée
dans la journée. Toute l'ergonomie d'un terminal de marché découle de la
donnée qu'on lui sert — c'est donc ici que ça commence, pas dans l'interface.

FORMAT COLONNAIRE. Les séries sont publiées en colonnes parallèles
(`{"t": [...], "o": [...], ...}`) et non en liste d'objets. Sur 1 250 séances,
répéter les six clés à chaque ligne coûte environ 60 % du fichier pour zéro
information. Le front recompose les objets à la lecture, en une passe.

DEUX PROFONDEURS, DEUX RÔLES :

  * quotidien — cinq ans, pour la structure : tendance de fond, supports
    anciens, régime de volatilité. C'est l'échelle où le moteur de décision
    travaille.
  * une heure — cent vingt jours, pour la tendance intermédiaire : c'est le
    pas que regardent la plupart des opérateurs pour situer un mouvement entre
    la séance et le mois.
  * cinq minutes — cinq séances, pour l'exécution : à quel moment de la
    journée entrer, où la séance a réellement ouvert, si le plan a déjà été
    approché. C'est l'échelle qui manquait entièrement.

Les pas intermédiaires (15 min, 4 h, semaine, mois) ne sont PAS publiés : ils
s'agrègent dans le navigateur à partir de ces trois socles, en une passe.
Publier sept séries là où trois suffisent multiplierait le poids du site sans
ajouter une seule information.

Les barres 5 min publiées ici sont un SOCLE, pas le direct : elles datent de
la dernière publication ou du dernier balayage de veille. La fraîcheur à la
minute vient de `serie.php`, qui interroge la source en direct et dont le
front recolle le résultat en bout de série. Si ce relais est indisponible, le
graphique reste juste — simplement daté, et l'interface le dit.
"""

from __future__ import annotations

import pandas as pd

from marketlab import config, intraday

# Cinq ans de séances : au-delà, la structure ancienne n'éclaire plus grand
# chose et le fichier grossit pour rien.
JOURS_QUOTIDIEN = 1825
# Cinq séances de barres 5 min : de quoi voir la semaine en cours et comparer
# la journée à celles qui la précèdent. Yahoo ne sert de toute façon les
# barres fines que sur une fenêtre glissante courte.
SEANCES_INTRADAY = 5
# Profondeur de la série HORAIRE. Elle existe pour une raison simple : les pas
# « 1 h » et « 4 h » ne peuvent pas être fabriqués à partir des barres 5 min,
# qui ne couvrent que cinq séances — on obtiendrait trente bougies horaires,
# de quoi ne rien lire du tout. Cent vingt jours donnent environ 800 barres
# horaires, soit une vraie lecture de tendance intermédiaire.
JOURS_HORAIRE = 120
INTERVALLE_HORAIRE = "1h"

# Colonnes de bougie, dans l'ordre attendu par le front.
OHLCV = ("open", "high", "low", "close", "volume")
# Surcouches déjà calculées par `indicators.enrich` : republier ce qui existe
# coûte quelques kilo-octets et évite au navigateur de recalculer des moyennes
# mobiles sur 1 250 points à chaque changement d'onglet.
SURCOUCHES = ("sma50", "sma200", "bb_upper", "bb_lower")


def _colonne(serie: pd.Series, decimales: int = 4) -> list:
    """Une colonne JSON : arrondie, et `None` là où la donnée manque.

    `NaN` n'existe pas en JSON — json.dumps l'écrit `NaN`, que `JSON.parse`
    refuse. Les premières valeurs d'une SMA 200 sont vides par construction :
    sans cette conversion, la moitié des fiches produiraient un fichier que le
    navigateur rejette en bloc.
    """
    if decimales == 0:
        return [None if pd.isna(v) else int(v) for v in serie]
    return [None if pd.isna(v) else round(float(v), decimales)
            for v in serie]


def _horodatages(index: pd.DatetimeIndex, quotidien: bool) -> list:
    """Dates de bougies, au format attendu par la bibliothèque de graphique.

    Quotidien : une chaîne « AAAA-MM-JJ » — c'est un jour de bourse, pas un
    instant, et le fuseau du lecteur ne doit pas pouvoir le décaler d'un jour.
    Intrajournalier : un entier de secondes UTC — là au contraire c'est bien
    un instant, et le navigateur l'affichera dans l'heure locale.
    """
    if quotidien:
        return [d.strftime("%Y-%m-%d") for d in index]
    idx = index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return [int(d.timestamp()) for d in idx]


def serie(df: pd.DataFrame, quotidien: bool = True,
          surcouches: bool = True) -> dict:
    """Convertit un tableau OHLCV en séries colonnaires prêtes à tracer."""
    if df is None or df.empty:
        return {}
    bloc: dict = {"t": _horodatages(df.index, quotidien)}
    for col in OHLCV:
        if col not in df.columns:
            continue
        # Les volumes sont des entiers : quatre décimales n'y ajoutent rien
        # et pèsent quatre caractères par ligne.
        bloc[col[0] if col != "volume" else "v"] = _colonne(
            df[col], 0 if col == "volume" else 4)
    if surcouches:
        for col in SURCOUCHES:
            if col in df.columns:
                bloc[col] = _colonne(df[col])
    bloc["n"] = len(df)
    return bloc


def quotidienne(df: pd.DataFrame, jours: int = JOURS_QUOTIDIEN) -> dict:
    """Série quotidienne, surcouches comprises."""
    return serie(df.tail(jours), quotidien=True, surcouches=True)


def intrajournaliere(symbole: str, seances: int = SEANCES_INTRADAY,
                     interval: str = intraday.INTERVALLE_DEFAUT) -> dict:
    """Barres fines lues dans le magasin local. Renvoie {} si rien n'y est.

    Ne déclenche AUCUN appel réseau : la capture est le travail d'un autre
    étage (la veille, ou l'étape de publication qui l'appelle une fois pour
    tout le périmètre). Un module de mise en forme qui télécharge en douce est
    un module dont on ne sait plus quand il coûte cher.
    """
    journees = intraday.journees_archivees(symbole, interval)
    if not journees:
        return {}
    depuis = journees[-seances:][0]
    df = intraday.lire(symbole, interval, depuis=depuis)
    if df is None or df.empty:
        return {}
    bloc = serie(df, quotidien=False, surcouches=False)
    bloc["interval"] = interval
    bloc["seances"] = len(journees[-seances:])
    return bloc


def horaire(symbole: str, jours: int = JOURS_HORAIRE) -> dict:
    """Barres horaires lues dans le magasin local. Renvoie {} si rien n'y est."""
    journees = intraday.journees_archivees(symbole, INTERVALLE_HORAIRE)
    if not journees:
        return {}
    df = intraday.lire(symbole, INTERVALLE_HORAIRE, depuis=journees[-jours:][0])
    if df is None or df.empty:
        return {}
    bloc = serie(df, quotidien=False, surcouches=False)
    bloc["interval"] = INTERVALLE_HORAIRE
    return bloc


def payload(symbole: str, df: pd.DataFrame) -> dict:
    """Le fichier `titres/<SYM>.serie.json` complet.

    L'intrajournalier est facultatif par construction : la BRVM n'en a pas,
    un titre nouvellement suivi n'en a pas encore, et un fournisseur peut
    tomber. Son absence retire une échelle de temps au graphique, elle ne le
    casse pas.
    """
    bloc = {
        "symbole": symbole,
        "nom": config.NOMS_ACTIFS.get(symbole, symbole),
        "quotidien": quotidienne(df),
    }
    intra = intrajournaliere(symbole)
    if intra:
        bloc["intraday"] = intra
    heure = horaire(symbole)
    if heure:
        bloc["horaire"] = heure
    return bloc
