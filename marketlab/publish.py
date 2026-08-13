"""Génération d'un instantané statique publiable sur un hébergement mutualisé.

Bascule d'architecture : au lieu de calculer pendant la requête web — ce qu'un
hébergement mutualisé ne permet pas (délais courts, pas de processus
permanent, quotas serrés) — tout est calculé **en amont** par une tâche
planifiée, puis publié sous forme de fichiers JSON.

Le site devient alors un ensemble de fichiers statiques : aucune dépendance
Python côté serveur, aucun calcul à la visite, un affichage instantané, et
un fonctionnement garanti sur n'importe quel hébergement — y compris depuis
un téléphone.

Sortie (dossier `site/`) :
    index.html, assets/…        le front React compilé
    donnees/meta.json           horodatage et inventaire
    donnees/screener.json       tableau de bord des signaux
    donnees/macro.json          régime macroéconomique
    donnees/calendrier.json     événements économiques à venir
    donnees/resultats.json      publications trimestrielles à venir
    donnees/correlations.json   matrice et risque du portefeuille
    donnees/fondamentaux.json   notation des actions
    donnees/titres/<SYM>.json   fiche complète par titre
    donnees/series/<SYM>.json   bougies quotidiennes, horaires et 5 min

Chaque bloc est calculé indépendamment : l'échec de l'un n'empêche pas la
publication des autres, et l'erreur est consignée dans meta.json.
"""

import json
import math
import posixpath
import re
import shutil
from pathlib import Path

import pandas as pd

from marketlab import (alerts, broker_tools, config, correlations, cot,
                       coulisses, decision, drivers, eco_calendar, events,
                       forecast,
                       fundamentals, glossaire, implicite, indicators, intraday,
                       journal_chaine, levels, macro,
                       news, paper, position, regimes, screener, seasonality, synthese,
                       sentiment_marche, serie, signals)
from marketlab.data import get_ohlcv

RACINE_SITE = config.ROOT / "site"
DOSSIER_DONNEES = RACINE_SITE / "donnees"

# Titres pour lesquels une fiche détaillée (salle de marché) est produite.
# Chaque fiche coûte des requêtes réseau et quelques secondes de calcul :
# la liste reste un choix, pas un « tout l'univers ».
TITRES_DETAILLES = list(config.FICHES)

# Deux horizons suivis EN PARALLÈLE. L'officiel (20 séances) est celui de
# l'historique ; le court (5 séances) a été retenu parce qu'il donne 4,3 fois
# plus d'épisodes de marché indépendants à durée d'observation égale — de
# quoi trancher en ~2 ans au lieu de ~21.
HORIZON_OFFICIEL = 20
HORIZON_COURT = 5


def _assainir(donnees):
    """NaN et infinis → None, récursivement, AVANT la sérialisation.

    `json.dumps` écrit `NaN` tel quel (extension Python, JSON invalide) et un
    seul suffit à tuer TOUTE la page qui charge le fichier : constaté sur
    Coulisses le 2026-08-02 — la sonde FRED a émis `"recalcule": NaN` pour un
    indicateur sans donnée, `JSON.parse` a levé, et la page entière est morte
    en silence. Le nettoyage vit ICI, au point unique d'écriture : aucune
    sonde future n'aura à y penser.
    """
    if isinstance(donnees, dict):
        return {k: _assainir(v) for k, v in donnees.items()}
    if isinstance(donnees, (list, tuple)):
        return [_assainir(v) for v in donnees]
    if isinstance(donnees, float) and not math.isfinite(donnees):
        return None
    return donnees


def _ecrire(chemin: Path, donnees) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(_assainir(donnees), ensure_ascii=False,
                                 default=str, allow_nan=False),
                      encoding="utf-8")


def _table(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> enregistrements JSON (NaN -> None, dates ISO)."""
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out = out.reset_index(names="date")
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M")
    return out.astype(object).where(out.notna(), None).to_dict(orient="records")


# --- Blocs ------------------------------------------------------------------

def _theme_actif(symbole: str) -> str:
    """Thème d'un actif, tel que l'utilisateur le pense.

    Tiré de config.UNIVERS, qui est la source de vérité — et non deviné
    d'après le suffixe : « Actions US », « Actions EU » et « Actions Asie »
    ne se distinguent pas autrement, et c'est justement la distinction utile
    pour lire le tableau.
    """
    for nom, liste in config.UNIVERS.items():
        if symbole in liste:
            return nom
    return _classe_actif(symbole)


def bloc_screener() -> list[dict]:
    lignes = _table(screener.scan(config.SUIVIS))
    for l in lignes:
        l["theme"] = _theme_actif(l.get("symbole", ""))
        l["nom"] = config.NOMS_ACTIFS.get(l.get("symbole", ""), l.get("symbole"))
    return lignes


def bloc_macro() -> dict:
    sortie = {"regime": macro.regime(), "indicateurs": _table(macro.snapshot())}
    # La prime de variance : ce que les acheteurs d'assurance paient au-dessus
    # de la volatilité ensuite réalisée. Mesurée sur l'historique VIX/S&P déjà
    # en cache ; son échec ne doit pas priver la page du régime macro.
    try:
        sortie["prime_variance"] = implicite.prime_variance_vix()
    except Exception as exc:
        sortie["prime_variance"] = {"mesurable": False, "raison": str(exc)[:80]}
    return sortie


def bloc_calendrier() -> list[dict]:
    df = eco_calendar.get_events(impacts=["High", "Medium"])
    df = df[df["quand"] >= pd.Timestamp.now().normalize()]
    return _table(df.head(60))


def bloc_resultats() -> list[dict]:
    symboles = (config.ACTIONS_US + config.ACTIONS_EU
                + config.ACTIONS_ASIE)
    return _table(events.prochaines_publications(symboles, jours=45))


def bloc_fondamentaux() -> list[dict]:
    return _table(fundamentals.comparer(
        config.ACTIONS_US + config.ACTIONS_EU + config.ACTIONS_ASIE))


def bloc_correlations() -> dict:
    symboles = config.ACTIONS_US[:10] + config.ACTIONS_EU[:4] + config.CRYPTO[:2]
    matrice = correlations.matrice(symboles)
    resultat = {
        "symboles": list(matrice.columns),
        "matrice": {c: {k: float(v) for k, v in matrice[c].items()}
                    for c in matrice.columns},
        "extremes": correlations.paires_extremes(symboles),
        "par_regime": correlations.correlation_par_regime(symboles),
    }
    try:
        resultat["portefeuille"] = correlations.analyser_paper()
    except Exception:
        resultat["portefeuille"] = None  # moins de 2 positions, ou données absentes
    return resultat


def bloc_paper() -> dict | None:
    try:
        etat = paper.etat()
    except RuntimeError:
        return None
    etat["positions"] = _table(etat["positions"]) if len(etat["positions"]) else []
    return etat


def donnees_titre(symbole: str) -> pd.DataFrame:
    """Le tableau OHLCV enrichi d'un titre, cinq ans de profondeur.

    Sorti de `fiche_titre` pour être calculé UNE fois par titre et servir
    aussi bien la fiche que la série de bougies. Les deux en avaient besoin ;
    l'enrichissement (moyennes mobiles, bandes, RSI sur 1 250 lignes) n'a
    aucune raison de tourner deux fois.
    """
    return indicators.enrich(get_ohlcv(symbole,
                                       lookback_days=serie.JOURS_QUOTIDIEN))


def fiche_titre(symbole: str, df: pd.DataFrame | None = None) -> dict:
    """Fiche complète d'un titre : cours, signaux, prévision, niveaux, contexte."""
    df = donnees_titre(symbole) if df is None else df
    fiche = {"symbole": symbole,
             "nom": config.NOMS_ACTIFS.get(symbole, symbole)}

    sig = signals.compute_signals(df)
    sig["avis"] = signals.label(sig["score"])
    fiche["signaux"] = sig

    colonnes = ["close", "sma50", "sma200", "rsi14", "bb_upper", "bb_lower"]
    historique = df[colonnes].tail(260).round(4)
    fiche["historique"] = _table(historique)

    fiche["brokers"] = broker_tools.analyse(df)

    for nom, calcul in (
        # la stratégie répond aux 3 questions (quand / quel sens / quelle
        # marge) et embarque le verdict complet du moteur de décision
        ("strategie", lambda: position.strategie(symbole)),
        # moteurs fondamentaux propres à la classe d'actif (carry, taux
        # réels, structure à terme) — liste vide pour une action, c'est normal
        ("moteurs", lambda: drivers.moteurs(symbole)),
        ("regime", lambda: forecast.regime(df)),
        ("projection", lambda: {k: v for k, v in
                                forecast.projeter(
                                    df, horizon=20,
                                    vol_cible=har.vol_cible(symbole)).items()
                                if not k.startswith("_")}),
        ("analogues", lambda: forecast.analogues(df, horizon=20)),
        # ce que le marché des options price pour ce titre — lu dans le relevé
        # accumulé, aucun réseau ici
        ("implicite", lambda: implicite.synthese_titre(symbole, df)),
        ("niveaux", lambda: {"zones": levels.zones_proches(df),
                             "pivots": levels.pivots(df)}),
        ("saisonnalite", lambda: seasonality.analyser(symbole)),
        ("sentiment", lambda: news.sentiment(symbole)),
    ):
        try:
            fiche[nom] = calcul()
        except Exception as exc:
            fiche[nom] = {"erreur": str(exc)[:120]}

    if events.a_des_resultats(symbole):
        try:
            fiche["resultats"] = events.risque_evenement(symbole, horizon=20)
        except Exception as exc:
            fiche["resultats"] = {"erreur": str(exc)[:120]}
    return fiche


def _classe_actif(symbole: str) -> str:
    if symbole.endswith("=X"):
        return "Forex"
    if symbole.endswith("=F"):
        return "Matières"
    if symbole.endswith("USDT"):
        return "Crypto"
    if symbole.startswith("^"):
        return "Indices"
    return "Actions"


def _sur_regimes() -> dict:
    """IC conditionnel au regime. Ne doit jamais faire tomber la publication :
    c'est une mesure d'appoint, pas un bloc vital."""
    try:
        return regimes.analyser()
    except Exception as exc:
        return {"mesurable": False, "raison": f"{type(exc).__name__}: {exc}"}


def bloc_verdicts() -> dict:
    """Dossiers de décision + journalisation + bilan des verdicts passés.

    C'est le bloc central du site : la synthèse motivée de toutes les
    analyses, et le tableau qui mesure ce que valaient les verdicts
    précédents une fois leur horizon écoulé. Chaque dossier est enrichi de
    la classe d'actif (filtres du front) et du consensus des six outils
    brokers (ADX, Supertrend, Ichimoku, Fibonacci, Stochastique, OBV).
    """
    dossiers = decision.verdicts(TITRES_DETAILLES)
    for d in dossiers:
        if "erreur" in d:
            continue
        d["classe"] = _classe_actif(d["symbole"])
        d["nom"] = config.NOMS_ACTIFS.get(d["symbole"], d["symbole"])
        try:
            df = indicators.enrich(get_ohlcv(d["symbole"], lookback_days=1825))
            d["brokers"] = broker_tools.consensus(df)
        except Exception as exc:
            d["brokers"] = {"texte": f"indisponible : {str(exc)[:60]}"}
    decision.journaliser(dossiers)

    # Second jeu de verdicts à horizon COURT, produit en parallèle. Ce ne sont
    # pas les mêmes verdicts relus autrement : la prévision, les analogues,
    # le stop et l'objectif sont recalculés pour une fenêtre de 5 séances.
    # Deux paris distincts, deux traces distinctes, deux bilans distincts —
    # et c'est le seul moyen de savoir lequel des deux vaut quelque chose
    # avant de très nombreuses années.
    courts = decision.verdicts(TITRES_DETAILLES, horizon=HORIZON_COURT)
    for d in courts:
        if "erreur" in d:
            continue
        d["classe"] = _classe_actif(d["symbole"])
        d["nom"] = config.NOMS_ACTIFS.get(d["symbole"], d["symbole"])
    decision.journaliser(courts)

    # Le journal de la CHAÎNE, à côté de celui des notes : ce que le filtre a
    # retenu ou écarté, et à quel maillon. C'est ce qui permettra de juger le
    # filtre à l'échéance au lieu de le croire sur parole. Non bloquant : une
    # trace manquée ne vaut pas une publication manquée.
    try:
        journal_chaine.journaliser(dossiers + courts)
    except Exception as exc:
        print(f"journal de la chaîne non consigné (non bloquant) : "
              f"{type(exc).__name__}: {str(exc)[:80]}")

    # Le banc d'essai du côté VENTE : les candidats vendeurs du jour reçoivent
    # un plan de short hypothétique, jamais exécuté, jugé à l'échéance. C'est
    # ce qui permettra de voir VENIR le jour où vendre paiera — au lieu de le
    # découvrir en subissant. Non bloquant, comme toute trace.
    try:
        from marketlab import banc_ventes
        n = banc_ventes.journaliser(dossiers + courts)
        if n:
            print(f"banc des ventes : {n} short(s) hypothétique(s) consigné(s)")
    except Exception as exc:
        print(f"banc des ventes non consigné (non bloquant) : "
              f"{type(exc).__name__}: {str(exc)[:80]}")

    # Décomposition de la note en apports de chaque composante : la somme
    # redonne exactement la note publiée, donc l'explication est vérifiable et
    # non illustrative. Attachée au dossier pour que la fiche n'ait rien à
    # recalculer.
    for d in dossiers:
        try:
            d["contributions"] = synthese.contributions(d)
        except Exception as exc:
            d["contributions"] = {"lignes": [], "erreur": str(exc)[:80]}

    return {"dossiers": dossiers, "bilan": decision.bilan(HORIZON_OFFICIEL),
            # Lecture d'ensemble : où est le vent par classe d'actif, et quel
            # est le VRAI pari derrière les paires de devises.
            "synthese": synthese.bloc(dossiers),
            # L'IC est-il uniforme, ou l'outil se trompe-t-il surtout dans un
            # regime ? Une moyenne unique suppose une capacite constante ; ce
            # bloc verifie l'hypothese au lieu de la poser.
            "par_regime": _sur_regimes(),
            "horizon_officiel": HORIZON_OFFICIEL,
            "horizon_court": HORIZON_COURT,
            "dossiers_court": courts,
            "bilan_court": decision.bilan(HORIZON_COURT)}


# --- Orchestration ----------------------------------------------------------

def bloc_cot() -> list[dict]:
    """Panorama COT : positionnement hebdomadaire des spéculateurs (CFTC)."""
    return _table(cot.panorama())


BLOCS = {
    # le calibrage s'exécute AVANT les verdicts : les dossiers du jour
    # utilisent aussitôt les pondérations apprises du bilan réel
    "apprentissage": decision.calibrer,
    "verdicts": bloc_verdicts,
    # le tribunal des alertes, publié à côté de celui des verdicts : une
    # règle qui crie pour rien doit se voir
    "bilan_alertes": alerts.bilan_alertes,
    "cot": bloc_cot,
    "sentiment_marche": sentiment_marche.indice,
    "barometres": drivers.barometres,
    "screener": bloc_screener,
    "macro": bloc_macro,
    "calendrier": bloc_calendrier,
    "resultats": bloc_resultats,
    "fondamentaux": bloc_fondamentaux,
    "correlations": bloc_correlations,
    "paper": bloc_paper,
    # Le vocabulaire, écrit une seule fois et publié comme le reste : le front
    # React et les pages PHP le lisent au même endroit. Trois définitions du
    # même mot dans trois fichiers, c'est deux définitions fausses à terme.
    "glossaire": glossaire.bloc,
}


def generer(titres: list[str] | None = None, blocs: list[str] | None = None,
            verbeux: bool = True) -> dict:
    """Produit l'instantané complet dans `site/`. Renvoie le bilan."""
    titres = titres if titres is not None else TITRES_DETAILLES
    noms_blocs = blocs if blocs is not None else list(BLOCS)
    DOSSIER_DONNEES.mkdir(parents=True, exist_ok=True)

    erreurs, produits = {}, []
    for nom in noms_blocs:
        if verbeux:
            print(f"  {nom}…", flush=True)
        try:
            _ecrire(DOSSIER_DONNEES / f"{nom}.json", BLOCS[nom]())
            produits.append(nom)
        except Exception as exc:
            erreurs[nom] = f"{type(exc).__name__}: {exc}"
            if verbeux:
                print(f"    ECHEC : {str(exc)[:120]}", flush=True)

    # Barres 5 min de TOUT le périmètre en un seul appel groupé, AVANT les
    # fiches. Les faire titre par titre dans la boucle, ce serait 36 requêtes
    # là où une suffit — et c'est exactement le motif de limitation de débit
    # qui produit des trous silencieux chez les fournisseurs gratuits.
    # Non bloquant : sans barres fines, le graphique perd l'échelle « 1 jour »,
    # il ne perd pas l'échelle quotidienne.
    if titres:
        try:
            bilan_intra = intraday.capturer(
                list(titres), jours=serie.SEANCES_INTRADAY + 1)
            bilan_h = intraday.capturer(
                list(titres), interval=serie.INTERVALLE_HORAIRE,
                jours=serie.JOURS_HORAIRE)
            if verbeux:
                print(f"  barres 5 min : {bilan_intra['barres']} sur "
                      f"{bilan_intra['titres']} titre(s) ; barres 1 h : "
                      f"{bilan_h['barres']} sur {bilan_h['titres']} titre(s)",
                      flush=True)
        except Exception as exc:
            erreurs["intraday"] = f"{type(exc).__name__}: {exc}"
            if verbeux:
                print(f"  barres 5 min indisponibles : {str(exc)[:100]}",
                      flush=True)

    fiches = []
    for symbole in titres:
        if verbeux:
            print(f"  fiche {symbole}…", flush=True)
        try:
            df_titre = donnees_titre(symbole)
            _ecrire(DOSSIER_DONNEES / "titres" / f"{symbole}.json",
                    fiche_titre(symbole, df_titre))
            fiches.append(symbole)
        except Exception as exc:
            erreurs[f"titre:{symbole}"] = f"{type(exc).__name__}: {exc}"
            if verbeux:
                print(f"    ECHEC : {str(exc)[:120]}", flush=True)
            continue
        # La série de bougies est un fichier SÉPARÉ de la fiche : elle pèse
        # à elle seule plus que tout le reste, et la page l'appelle après le
        # premier rendu. Son échec ne doit pas emporter la fiche, qui contient
        # le verdict — c'est-à-dire l'essentiel.
        try:
            _ecrire(DOSSIER_DONNEES / "series" / f"{symbole}.json",
                    serie.payload(symbole, df_titre))
        except Exception as exc:
            erreurs[f"serie:{symbole}"] = f"{type(exc).__name__}: {exc}"
            if verbeux:
                print(f"    série ECHEC : {str(exc)[:120]}", flush=True)

    meta = {
        # heure du Bénin calculée depuis UTC : la machine qui génère (poste
        # local ou runner GitHub) peut être sur n'importe quel fuseau
        "genere_le": (pd.Timestamp.utcnow() + pd.Timedelta(hours=1))
                     .strftime("%Y-%m-%d %H:%M"),
        "fuseau": "Bénin (UTC+1)",
        "blocs": produits,
        "titres": fiches,
        "univers": config.UNIVERS,
        "erreurs": erreurs,
        "avertissement": ("Analyses statistiques, pas des prédictions. "
                          "Aucun contenu ne constitue un conseil en "
                          "investissement."),
    }
    _ecrire(DOSSIER_DONNEES / "meta.json", meta)

    # La page « Coulisses » est écrite EN DERNIER, et ce n'est pas un détail : il
    # compte les fiches et les séries réellement présentes sur le disque, et
    # relit meta.json. Le produire plus tôt donnerait l'état de la génération
    # PRÉCÉDENTE — un tableau de bord qui décrit la veille est pire que pas
    # de tableau de bord.
    try:
        _ecrire(DOSSIER_DONNEES / "coulisses.json", coulisses.etat())
    except Exception as exc:
        erreurs["coulisses"] = f"{type(exc).__name__}: {exc}"
        if verbeux:
            print(f"  coulisses ECHEC : {str(exc)[:120]}", flush=True)
    return meta


# Relais PHP embarqués dans la publication. Liste EXPLICITE, et volontairement
# courte : ce sont des fichiers de code pur, sans état, dont le dépôt est la
# seule source. Tout le reste de `deploy/` en est exclu — les espaces trading,
# admin et accès gardent des dossiers d'état sur l'hébergement (comptes,
# sessions) et leur mise en ligne reste un geste délibéré, pas un effet de bord
# de la publication nocturne. Quant à `installer.sh`, aux unités systemd et à
# la configuration nginx, ils n'ont RIEN à faire dans une racine web publique.
#
# POURQUOI LES AJOUTER. Jusqu'ici un correctif sur cours.php n'atteignait
# l'hébergement que par un envoi manuel : le dépôt et le site pouvaient donc
# diverger sans que rien ne le signale. C'est le genre d'écart qui fait
# chercher un bug dans du code déjà corrigé.
RELAIS_PHP = ["cours.php", "cours_lib.php", "serie.php", "lexique.php"]

# Pages PHP des espaces, énumérées FICHIER PAR FICHIER — jamais un dossier.
# La nuance est capitale : `deploy/trading/` ne contient que du code, mais son
# jumeau sur l'hébergement contient `comptes/`, c'est-à-dire les portefeuilles
# réels des utilisateurs et des robots. Copier un dossier entier, un jour où
# un fichier d'état traînerait côté local, écraserait des comptes. Une liste
# explicite ne peut pas faire ça.
#
# Les `.htaccess` sont volontairement EXCLUS : ce sont des réglages
# d'hébergement (authentification, redirections), posés une fois, et le dépôt
# n'est pas l'autorité dessus.
PAGES_ESPACES = [
    "trading/index.php",
    "admin/index.php",
    "admin/commun.php",
    "admin/comptes_lib.php",
    "admin/compte.php",
    "admin/comparer.php",
    "acces/index.php",
]

# `require` / `include` d'un fichier voisin, tels qu'écrits dans ces pages.
_DEPENDANCE_PHP = re.compile(
    r"(?:require|include)(?:_once)?\s*__DIR__\s*\.\s*['\"]/([^'\"]+)['\"]")


def _dependances_manquantes(copies: list[str]) -> list[str]:
    """Fichiers exigés par une page publiée mais absents de la publication.

    LE PIÈGE QUE ÇA FERME, vécu le 2026-07-30. `admin/index.php` a reçu un
    `require __DIR__ . '/comptes_lib.php'` sans que le fichier soit ajouté à
    PAGES_ESPACES. La publication a donc mis en ligne un index qui exige un
    fichier absent : erreur fatale PHP, panneau d'administration injoignable,
    et RIEN ne l'avait signalé — ni les tests, ni la vérification de cohérence,
    puisque tout allait bien côté dépôt.

    La liste explicite reste la bonne conception (copier `deploy/trading/` en
    entier écraserait les portefeuilles réels), mais elle demande qu'on pense à
    l'alimenter. Cette vérification s'en charge à notre place.
    """
    publies = set(copies)
    manquantes = []
    for nom in copies:
        chemin = RACINE_SITE / nom
        try:
            contenu = chemin.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        dossier = str(Path(nom).parent).replace("\\", "/")
        for exige in _DEPENDANCE_PHP.findall(contenu):
            brut = exige if dossier in ("", ".") else f"{dossier}/{exige}"
            # normaliser les « .. » : `trading/index.php` exige
            # `__DIR__ . '/../cours_lib.php'`, qui désigne bien le
            # `cours_lib.php` de la racine, déjà publié
            attendu = posixpath.normpath(brut)
            if attendu not in publies:
                manquantes.append(f"{nom} exige {attendu}")
    return manquantes


def copier_php() -> list[str]:
    """Copie les fichiers PHP versionnés dans le site. Renvoie les noms copiés.

    Lève si une page publiée dépend d'un fichier qui ne l'est pas : mieux vaut
    une publication qui échoue bruyamment qu'un espace en erreur fatale.
    """
    copies = []
    for nom in RELAIS_PHP + PAGES_ESPACES:
        source = config.ROOT / "deploy" / nom
        if source.is_file():
            cible = RACINE_SITE / nom
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)
            copies.append(nom)
    manquantes = _dependances_manquantes(copies)
    if manquantes:
        raise RuntimeError(
            "Dépendance PHP non publiée — ajouter le fichier à PAGES_ESPACES : "
            + " ; ".join(manquantes))
    return copies


def copier_module_graphique() -> bool:
    """Copie le graphique en module autonome, pour l'espace de trading (PHP).

    C'est le MÊME code que celui du site, construit une seconde fois par Vite
    en bundle sans dépendance. L'espace de trading n'a pas de bundler : sans
    ce fichier, il faudrait y réécrire un graphique, et deux implémentations
    divergent toujours.
    """
    source = config.ROOT / "front" / "dist-trading" / "marketlab-graphique.js"
    if not source.is_file():
        return False
    RACINE_SITE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, RACINE_SITE / source.name)
    return True


def copier_front() -> bool:
    """Copie le front React compilé (`front/dist`) à la racine du site."""
    dist = config.ROOT / "front" / "dist"
    if not dist.exists():
        return False
    for element in dist.iterdir():
        cible = RACINE_SITE / element.name
        if element.is_dir():
            shutil.rmtree(cible, ignore_errors=True)
            shutil.copytree(element, cible)
        else:
            RACINE_SITE.mkdir(parents=True, exist_ok=True)
            shutil.copy2(element, cible)
    return True
