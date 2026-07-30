"""Indice de surprise économique — le moteur du forex à court terme.

L'IDÉE. Un chiffre économique ne déplace pas une devise parce qu'il est bon ou
mauvais, mais parce qu'il est MEILLEUR OU PIRE QU'ATTENDU. Un excellent emploi
américain déjà anticipé ne fait rien bouger ; un emploi médiocre mais moins
mauvais que prévu fait monter le dollar. C'est la surprise qui compte, et les
salles de marché la suivent sous forme d'indice agrégé par devise — le plus
connu étant l'indice de surprise économique de Citi.

LA DIFFICULTÉ DE DÉPART, ET SA SOLUTION. Le flux JSON de ForexFactory ne
publie PAS le résultat : ses seules clés sont country, date, forecast, impact,
previous, title. Vérifié sur le flux brut le 2026-07-30 — 92 événements, aucun
champ `actual`. Il n'y a donc pas de résultat à lire, et aucune source
gratuite ne fournit à la fois le consensus ET le réalisé.

Mais l'information est là, décalée : la valeur `previous` d'une publication EST
le résultat de la publication précédente du même indicateur. En conservant un
instantané du calendrier chaque jour, on reconstitue donc les résultats par
CHAÎNAGE :

    surprise(juillet) = previous(publication d'août) − forecast(publication de juillet)

Le prix à payer est un décalage d'un cycle : la surprise d'un indicateur
mensuel n'est connue qu'au mois suivant. Pour un indice agrégé sur trois mois,
c'est acceptable ; pour réagir à une publication dans la minute, ça ne l'est
pas — et ce module ne le prétend pas.

LA VOIE SANS RETARD, en complément. FRED publie le résultat le jour même. En
croisant le réalisé FRED et le consensus ForexFactory, la surprise est connue
IMMÉDIATEMENT — pour les indicateurs américains dont la correspondance a été
PROUVÉE (cf. `marketlab/realises.py`, qui confronte chaque correspondance au
chiffre que le flux imprime déjà et écarte celles qui ne concordent pas).
`surprises()` réunit les deux sources et fait primer la voie sans retard.

Le chaînage garde son utilité : il couvre les devises que FRED ne suit pas et
les indicateurs hors table. Mais il ne produit rien avant un à deux mois
d'accumulation, là où la voie FRED produit dès la première publication
observée.

TROIS AUTRES DIFFICULTÉS, TRAITÉES EXPLICITEMENT.

1. Une surprise brute n'est pas comparable d'un indicateur à l'autre : rater le
   PIB de 0,2 point n'a rien à voir avec rater les inscriptions au chômage de
   20 000. On divise donc chaque écart par l'écart-type HISTORIQUE des
   surprises du MÊME indicateur, ce qui le rend sans dimension.
2. Le signe n'est pas toujours celui de l'écart. Un chômage plus élevé que
   prévu est une MAUVAISE surprise, alors que l'écart est positif. D'où une
   table d'indicateurs à sens inversé (`MOTS_INVERSES`).
3. Le flux ne sert QUE la semaine en cours. L'historique ne peut donc se
   constituer que par accumulation : d'où `data_local/calendrier_historique.csv`,
   versionné et immuable.

STATUT. Brique CANDIDATE, journalisée sous `c_surprise` et de poids NUL, comme
`c_brokers` et les briques de contexte. Elle ne pèsera sur le verdict que le
jour où son IC l'aura prouvée sur le journal. Une brique gagne sa place.
"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

from marketlab import config, eco_calendar

# Instantané brut du calendrier, accumulé jour après jour.
CALENDRIER_PATH = config.DATA_DIR / "calendrier_historique.csv"
COLONNES_CAL = ["date", "devise", "evenement", "impact", "prevision", "precedent"]

# Surprises reconstituées (en mémoire, jamais stockées : elles se redéduisent
# du calendrier, et un dérivé stocké finit toujours par diverger de sa source).
COLONNES = ["date", "devise", "evenement", "impact", "prevision", "resultat", "ecart"]

# Devises suivies : celles des paires de config.FOREX, plus la zone euro.
DEVISES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"]

# Fenêtre d'agrégation du score, en jours. Trois mois : assez long pour lisser
# le bruit d'une publication isolée, assez court pour que le score suive un
# retournement de cycle.
FENETRE_JOURS = 90

# Observations minimales avant de normaliser par l'écart-type propre à un
# indicateur. En dessous, l'écart-type serait lui-même du bruit et on se rabat
# sur celui de la devise.
OBS_MIN_ECART_TYPE = 5

# Conversion du z-score moyen en note −100..100. Une surprise moyenne de
# 2,5 écarts-types sature l'échelle : au-delà, l'information est déjà passée
# dans les cours.
ECHELLE_NOTE = 40.0

# Indicateurs dont une valeur PLUS ÉLEVÉE que prévu est une MAUVAISE nouvelle
# pour la devise. Repérage par mot-clé sur l'intitulé anglais du flux ; c'est
# une heuristique, assumée comme telle, et c'est pourquoi la brique reste
# candidate jusqu'à preuve statistique.
MOTS_INVERSES = (
    "unemployment rate", "jobless claims", "unemployment change",
    "claimant count", "continuing claims", "inventories",
    "trade deficit", "budget deficit", "delinquenc",
)

_SUFFIXES = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


# ---------------------------------------------------------------------------
# Lecture des valeurs
# ---------------------------------------------------------------------------

def nombre(texte) -> float | None:
    """Convertit une valeur ForexFactory en nombre. None si illisible.

    Le flux mélange les écritures : « 3.2% », « 250K », « 1.5M », « -0.3 »,
    « 4.25% », « <0.1% », « 1,250 ». Toute valeur non convertible renvoie None
    et l'événement est simplement ignoré — jamais remplacé par un zéro, qui
    serait lu comme « surprise nulle » et non comme « inconnu ».
    """
    if texte is None:
        return None
    brut = str(texte).strip()
    if not brut or brut in {"-", "--"}:
        return None
    brut = brut.replace(",", "").replace("%", "").replace("<", "").replace(">", "")
    brut = brut.replace("−", "-").strip()  # signe moins typographique
    facteur = 1.0
    if brut and brut[-1].upper() in _SUFFIXES:
        facteur = _SUFFIXES[brut[-1].upper()]
        brut = brut[:-1]
    try:
        return float(brut) * facteur
    except ValueError:
        return None


def sens(evenement: str) -> int:
    """+1 si « plus haut que prévu » soutient la devise, −1 sinon."""
    titre = (evenement or "").lower()
    return -1 if any(mot in titre for mot in MOTS_INVERSES) else 1


# ---------------------------------------------------------------------------
# Accumulation de l'historique
# ---------------------------------------------------------------------------

def instantane(evenements: pd.DataFrame | None = None) -> pd.DataFrame:
    """Instantané du calendrier de la semaine, prêt à être accumulé.

    On conserve les événements CHIFFRÉS (prévision ou valeur précédente
    lisible) ; les réunions et discours sans chiffre n'ont pas de surprise à
    livrer.
    """
    if evenements is None:
        evenements = eco_calendar.get_events()
    if evenements is None or evenements.empty:
        return pd.DataFrame(columns=COLONNES_CAL)

    lignes = []
    for _, ev in evenements.iterrows():
        prevu = nombre(ev.get("prevision"))
        precedent = nombre(ev.get("precedent"))
        if prevu is None and precedent is None:
            continue
        lignes.append({
            "date": pd.Timestamp(ev["quand"]).date().isoformat(),
            "devise": ev.get("devise", ""),
            "evenement": ev.get("evenement", ""),
            "impact": ev.get("impact", ""),
            "prevision": prevu,
            "precedent": precedent,
        })
    if not lignes:
        return pd.DataFrame(columns=COLONNES_CAL)
    return pd.DataFrame(lignes)[COLONNES_CAL]


def charger_calendrier() -> pd.DataFrame:
    if not CALENDRIER_PATH.exists():
        return pd.DataFrame(columns=COLONNES_CAL)
    try:
        df = pd.read_csv(CALENDRIER_PATH)
    except Exception:
        return pd.DataFrame(columns=COLONNES_CAL)
    for col in COLONNES_CAL:
        if col not in df.columns:
            df[col] = np.nan
    return df[COLONNES_CAL]


def fusionner(ancien: pd.DataFrame, nouveau: pd.DataFrame) -> pd.DataFrame:
    """Union immuable : une ligne déjà relevée n'est pas réécrite.

    Même raison que pour la volatilité réalisée : l'historique doit être stable,
    et le fichier ne doit bouger que lorsqu'une VRAIE publication s'ajoute,
    sinon le workflow produirait un commit à chaque passage.

    Conséquence VOULUE : c'est le PREMIER instantané d'un événement qui fait
    foi. Une prévision de consensus révisée la veille de la publication ne
    remplace donc pas celle qu'on avait notée — et c'est bien ce qu'on veut,
    puisqu'une surprise se mesure contre ce qui était attendu au moment où on
    l'a observé, pas contre une attente réécrite après coup.
    """
    cadres = [c for c in (ancien, nouveau) if c is not None and not c.empty]
    if not cadres:
        return pd.DataFrame(columns=COLONNES_CAL)
    fusion = pd.concat(cadres, ignore_index=True)[COLONNES_CAL]
    fusion = fusion.drop_duplicates(subset=["date", "devise", "evenement"], keep="first")
    return fusion.sort_values(["date", "devise", "evenement"]).reset_index(drop=True)


def mettre_a_jour_calendrier(evenements: pd.DataFrame | None = None,
                             ecrire: bool = True) -> dict:
    """Accumule l'instantané du jour dans l'historique. Ne lève jamais."""
    try:
        nouveau = instantane(evenements)
    except Exception as exc:
        return {"vus": 0, "ajoutees": 0, "total": 0, "erreur": str(exc)[:80]}
    ancien = charger_calendrier()
    fusion = fusionner(ancien, nouveau)
    if ecrire:
        CALENDRIER_PATH.parent.mkdir(parents=True, exist_ok=True)
        fusion.to_csv(CALENDRIER_PATH, index=False, float_format="%.10g")
    return {
        "vus": len(nouveau),
        "ajoutees": len(fusion) - len(ancien),
        "total": len(fusion),
        "devises": sorted(set(fusion["devise"])) if not fusion.empty else [],
    }


def reconstituer(historique: pd.DataFrame | None = None) -> pd.DataFrame:
    """Déduit les surprises par CHAÎNAGE des valeurs précédentes.

    Pour chaque indicateur (devise + intitulé), les publications sont triées
    par date ; la valeur `precedent` d'une publication est le RÉSULTAT de celle
    qui la précède. La surprise d'une publication est donc :

        (résultat révélé par la suivante) − (sa propre prévision)

    Une publication n'a de surprise que si elle porte une prévision ET qu'une
    publication ultérieure du même indicateur a été observée. La dernière
    publication de chaque indicateur n'en a donc jamais : son résultat n'a pas
    encore été révélé. C'est normal, pas une perte.
    """
    hist = charger_calendrier() if historique is None else historique
    if hist is None or hist.empty:
        return pd.DataFrame(columns=COLONNES)

    hist = hist.copy()
    hist["prevision"] = pd.to_numeric(hist["prevision"], errors="coerce")
    hist["precedent"] = pd.to_numeric(hist["precedent"], errors="coerce")
    hist = hist.sort_values(["devise", "evenement", "date"])

    # le résultat d'une ligne est la valeur « précédente » de la ligne suivante
    # du MÊME indicateur
    hist["resultat"] = hist.groupby(["devise", "evenement"])["precedent"].shift(-1)
    pret = hist.dropna(subset=["prevision", "resultat"]).copy()
    if pret.empty:
        return pd.DataFrame(columns=COLONNES)

    pret["ecart"] = ((pret["resultat"] - pret["prevision"])
                     * pret["evenement"].map(sens))
    return pret[COLONNES].sort_values(["date", "devise", "evenement"]) \
                         .reset_index(drop=True)


def sans_retard(historique: pd.DataFrame | None = None,
                valides: set | None = None) -> pd.DataFrame:
    """Surprises du JOUR MÊME, via le réalisé FRED croisé au consensus du flux.

    Le chaînage ci-dessus attend la publication suivante pour connaître un
    résultat. FRED, lui, publie le jour même : pour les indicateurs dont la
    correspondance est PROUVÉE (`realises.correspondances_valides()`), la
    surprise est disponible immédiatement.

    Seules les correspondances vérifiées sont utilisées. Une surprise absente
    vaut mieux qu'une surprise fausse, qui aurait l'apparence d'un signal.
    """
    from marketlab import realises  # import tardif : évite un cycle

    hist = charger_calendrier() if historique is None else historique
    if hist is None or hist.empty:
        return pd.DataFrame(columns=COLONNES)
    try:
        valides = realises.correspondances_valides() if valides is None else valides
    except Exception:
        return pd.DataFrame(columns=COLONNES)
    if not valides:
        return pd.DataFrame(columns=COLONNES)

    lignes = []
    for _, ev in hist.iterrows():
        devise, titre = ev.get("devise", ""), ev.get("evenement", "")
        if (devise, titre) not in valides:
            continue
        prevu = pd.to_numeric(ev.get("prevision"), errors="coerce")
        if not np.isfinite(prevu):
            continue
        try:
            # arrondi à la précision du consensus : le marché a comparé le
            # chiffre publié arrondi, pas l'indice à pleine précision
            reel = realises.valeur_realisee(
                devise, titre, ev["date"],
                precision=realises.decimales(prevu))
        except Exception:
            reel = None
        if reel is None or not np.isfinite(reel):
            continue
        lignes.append({
            "date": str(ev["date"]), "devise": devise, "evenement": titre,
            "impact": ev.get("impact", ""), "prevision": float(prevu),
            "resultat": float(reel),
            "ecart": (float(reel) - float(prevu)) * sens(titre),
        })
    if not lignes:
        return pd.DataFrame(columns=COLONNES)
    return pd.DataFrame(lignes)[COLONNES]


def surprises(historique: pd.DataFrame | None = None) -> pd.DataFrame:
    """Toutes les surprises connues : sans retard quand c'est possible, par
    chaînage sinon.

    Les deux sources se recouvrent partiellement — le chaînage finit par
    connaître ce que FRED savait déjà. En cas de doublon, la version SANS
    RETARD gagne : c'est la même publication, mesurée plus tôt et sans passer
    par une valeur « précédente » qui a pu être révisée entre-temps.
    """
    hist = charger_calendrier() if historique is None else historique
    immediat = sans_retard(hist)
    chaine = reconstituer(hist)
    if not immediat.empty:
        immediat = immediat.assign(source="fred")
    if not chaine.empty:
        chaine = chaine.assign(source="chainage")
    cadres = [c for c in (immediat, chaine) if not c.empty]
    if not cadres:
        return pd.DataFrame(columns=[*COLONNES, "source"])
    fusion = pd.concat(cadres, ignore_index=True)
    fusion = fusion.drop_duplicates(subset=["date", "devise", "evenement"],
                                    keep="first")
    return fusion.sort_values(["date", "devise", "evenement"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Normalisation et score
# ---------------------------------------------------------------------------

def normaliser(historique: pd.DataFrame) -> pd.DataFrame:
    """Ajoute la colonne `z` : l'écart divisé par sa dispersion de référence.

    Référence choisie par indicateur dès qu'il compte assez d'observations,
    sinon par devise. Un indicateur isolé sans référence utilisable est écarté
    plutôt que normalisé au jugé.
    """
    if historique is None or historique.empty:
        return pd.DataFrame(columns=[*COLONNES, "z"])
    hist = historique.copy()
    hist["ecart"] = pd.to_numeric(hist["ecart"], errors="coerce")
    hist = hist.dropna(subset=["ecart"])
    if hist.empty:
        return pd.DataFrame(columns=[*COLONNES, "z"])

    par_evenement = hist.groupby("evenement")["ecart"].agg(["std", "count"])
    utilisables = par_evenement[(par_evenement["count"] >= OBS_MIN_ECART_TYPE)
                                & (par_evenement["std"] > 0)]["std"]
    par_devise = hist.groupby("devise")["ecart"].std()

    def reference(ligne):
        ref = utilisables.get(ligne["evenement"])
        if ref is None or not np.isfinite(ref) or ref <= 0:
            ref = par_devise.get(ligne["devise"])
        return ref if ref and np.isfinite(ref) and ref > 0 else np.nan

    hist["reference"] = hist.apply(reference, axis=1)
    hist = hist.dropna(subset=["reference"])
    if hist.empty:
        return pd.DataFrame(columns=[*COLONNES, "z"])
    # bornage à 4 écarts-types : une révision d'assiette ou une coquille du
    # fournisseur ne doit pas emporter la moyenne de tout un trimestre
    hist["z"] = np.clip(hist["ecart"] / hist["reference"], -4, 4)
    return hist[[*COLONNES, "z"]]


def score_par_devise(historique: pd.DataFrame | None = None,
                     jours: int = FENETRE_JOURS,
                     aujourdhui: dt.date | None = None) -> dict:
    """Score de surprise par devise, sur la fenêtre glissante.

    Positif = les statistiques sortent MIEUX qu'attendu, ce qui soutient la
    devise ; négatif = elles déçoivent.
    """
    hist = surprises() if historique is None else historique
    z = normaliser(hist)
    if z.empty:
        return {}
    fin = aujourdhui or dt.datetime.now(dt.timezone.utc).date()
    debut = (fin - dt.timedelta(days=jours)).isoformat()
    fenetre = z[z["date"] >= debut]
    if fenetre.empty:
        return {}
    scores = {}
    for devise, part in fenetre.groupby("devise"):
        scores[devise] = {
            "score": round(float(np.clip(part["z"].mean() * ECHELLE_NOTE, -100, 100)), 1),
            "n_publications": int(len(part)),
            "z_moyen": round(float(part["z"].mean()), 3),
        }
    return scores


def devises_de(symbole: str) -> tuple[str, str] | None:
    """Devises d'une paire de change (`EURUSD=X` → `('EUR', 'USD')`)."""
    if not symbole.endswith("=X"):
        return None
    paire = symbole[:-2]
    if len(paire) != 6:
        return None
    base, contre = paire[:3].upper(), paire[3:].upper()
    if base not in DEVISES or contre not in DEVISES:
        return None
    return base, contre


def note(symbole: str, scores: dict | None = None) -> dict | None:
    """Note −100..100 pour une paire de change. None hors du forex.

    Une paire oppose DEUX économies : la note est l'écart entre le score de la
    devise de base et celui de la devise de contrepartie. EUR/USD monte si les
    statistiques européennes surprennent mieux que les américaines, pas si
    elles sont bonnes dans l'absolu.

    None pour tout le reste : la surprise économique est un moteur de DEVISE.
    L'étendre aux actions demanderait une chaîne de transmission qu'on n'a pas
    mesurée, et inventer un lien serait exactement ce que ce projet refuse.
    """
    couple = devises_de(symbole)
    if couple is None:
        return None
    base, contre = couple
    scores = score_par_devise() if scores is None else scores
    s_base, s_contre = scores.get(base), scores.get(contre)
    if not s_base or not s_contre:
        return None
    valeur = float(np.clip(s_base["score"] - s_contre["score"], -100, 100))
    return {
        "note": round(valeur, 1),
        "base": base,
        "contre": contre,
        "score_base": s_base["score"],
        "score_contre": s_contre["score"],
        "n_publications": s_base["n_publications"] + s_contre["n_publications"],
        "lecture": (f"Les statistiques {base} surprennent "
                    f"{'mieux' if valeur >= 0 else 'moins bien'} que les {contre} "
                    f"sur {FENETRE_JOURS} jours."),
    }
