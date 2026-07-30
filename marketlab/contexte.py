"""Contexte de marché : trois briques CANDIDATES au verdict.

Ces trois mesures font partie de l'outillage que les praticiens utilisent le
plus couramment. Elles sont ici **candidates**, au sens strict du projet :
calculées et journalisées à chaque verdict, mesurées comme les autres
composantes, mais SANS aucun poids tant qu'elles n'ont rien prouvé. Une
brique gagne sa place, elle ne la reçoit pas.

1. **Filtre de tendance de marché.** Ne pas acheter à contre-courant de son
   marché de rattachement. L'un des effets les mieux documentés de la
   littérature : la distribution des rendements d'un titre est très
   différente selon que son indice est au-dessus ou en dessous de sa moyenne
   200 séances.

2. **Force relative.** Un titre qui fait mieux que son indice sur 60 séances
   se comporte différemment de celui qui traîne derrière. On mesure l'écart,
   pas la performance absolue — cette dernière ne dit rien qu'on ne sache
   déjà par la composante technique.

3. **Alignement des horizons.** Une tendance confirmée à l'échelle
   hebdomadaire ET quotidienne est traitée différemment d'une divergence
   entre les deux. Le désaccord vaut avertissement.

Aucune de ces trois n'est une nouveauté : elles sont banales, et c'est
justement ce qui permet de les tester honnêtement contre le journal.
"""

import numpy as np
import pandas as pd

from marketlab.data import get_ohlcv

# Marché de rattachement de chaque famille d'actifs. Le forex n'en a pas de
# naturel — un taux de change n'appartient à aucun marché directeur — et les
# matières premières non plus : elles sont donc laissées sans référence
# plutôt que rattachées de force à un indice qui ne les gouverne pas.
REFERENCES = {
    ".PA": "^FCHI", ".DE": "^GDAXI", ".AS": "^STOXX50E", ".MI": "^STOXX50E",
    ".T": "^N225", ".HK": "^HSI", ".KS": "^KS11", ".TW": "^HSI",
}
REFERENCE_US = "^GSPC"
REFERENCE_CRYPTO = "BTCUSDT"


def reference(symbole: str) -> str | None:
    """Indice de rattachement, ou None si l'actif n'en a pas de pertinent."""
    if symbole.endswith("USDT"):
        return None if symbole == REFERENCE_CRYPTO else REFERENCE_CRYPTO
    if "=X" in symbole or "=F" in symbole or symbole.startswith("^"):
        return None                      # forex, matières, indices eux-mêmes
    for suffixe, indice in REFERENCES.items():
        if symbole.endswith(suffixe):
            return indice
    return REFERENCE_US                  # actions US par défaut


def _clip(x: float, borne: float = 100.0) -> float:
    return float(max(-borne, min(borne, x)))


def filtre_marche(symbole: str) -> dict | None:
    """Le marché de rattachement est-il porteur ?

    Note graduée par l'écart à la moyenne 200 séances, pas binaire : un
    indice 8 % au-dessus de sa moyenne n'est pas dans la même situation
    qu'un indice qui vient tout juste de la franchir.
    """
    ref = reference(symbole)
    if not ref:
        return None
    try:
        cours = get_ohlcv(ref, lookback_days=500)["close"]
    except Exception:
        return None
    if len(cours) < 200:
        return None
    prix = float(cours.iloc[-1])
    moyenne = float(cours.rolling(200).mean().iloc[-1])
    if not moyenne:
        return None
    ecart = (prix / moyenne - 1) * 100
    pente = float(cours.rolling(200).mean().diff(20).iloc[-1] or 0)

    note = _clip(ecart * 8, 80)
    if pente > 0:
        note = _clip(note + 20)
    elif pente < 0:
        note = _clip(note - 20)
    etat = "porteur" if ecart > 0 else "contraire"
    return {"note": note, "reference": ref,
            "raison": f"{ref} {abs(ecart):.1f} % {'au-dessus' if ecart > 0 else 'sous'} "
                      f"sa moyenne 200 séances, moyenne "
                      f"{'en hausse' if pente > 0 else 'en baisse'} — marché {etat}"}


def force_relative(symbole: str, df: pd.DataFrame | None = None,
                   fenetre: int = 60) -> dict | None:
    """Écart de performance entre le titre et son indice sur `fenetre`."""
    ref = reference(symbole)
    if not ref:
        return None
    try:
        titre = (df["close"] if df is not None
                 else get_ohlcv(symbole, lookback_days=200)["close"])
        indice = get_ohlcv(ref, lookback_days=200)["close"]
    except Exception:
        return None
    if len(titre) < fenetre + 1 or len(indice) < fenetre + 1:
        return None
    perf_titre = (float(titre.iloc[-1]) / float(titre.iloc[-fenetre - 1]) - 1) * 100
    perf_indice = (float(indice.iloc[-1]) / float(indice.iloc[-fenetre - 1]) - 1) * 100
    ecart = perf_titre - perf_indice
    return {"note": _clip(ecart * 4), "reference": ref,
            "raison": f"{ecart:+.1f} points de performance contre {ref} sur "
                      f"{fenetre} séances ({perf_titre:+.1f} % contre "
                      f"{perf_indice:+.1f} %)"}


def alignement_horizons(df: pd.DataFrame) -> dict | None:
    """Les tendances hebdomadaire et quotidienne disent-elles la même chose ?

    Accord haussier ou baissier : note franche. Désaccord : note nulle et
    mention explicite — c'est le cas qui mérite la prudence, pas un demi-avis.
    """
    if df is None or len(df) < 220:
        return None
    quotidien = df["close"]
    hebdo = quotidien.resample("W").last().dropna()
    if len(hebdo) < 32:
        return None
    j_prix = float(quotidien.iloc[-1])
    j_moy = float(quotidien.rolling(50).mean().iloc[-1])
    h_prix = float(hebdo.iloc[-1])
    h_moy = float(hebdo.rolling(30).mean().iloc[-1])
    if not j_moy or not h_moy:
        return None

    j_haut, h_haut = j_prix > j_moy, h_prix > h_moy
    if j_haut and h_haut:
        return {"note": 80.0, "raison": "hausse confirmée aux deux échelles "
                                        "(au-dessus des moyennes 50 j et 30 sem.)"}
    if not j_haut and not h_haut:
        return {"note": -80.0, "raison": "baisse confirmée aux deux échelles "
                                         "(sous les moyennes 50 j et 30 sem.)"}
    return {"note": 0.0,
            "raison": f"désaccord entre les échelles : "
                      f"{'hausse' if j_haut else 'baisse'} au quotidien, "
                      f"{'hausse' if h_haut else 'baisse'} à la semaine — "
                      "signal à prendre avec prudence"}


def candidats(symbole: str, df: pd.DataFrame | None = None) -> dict:
    """Les trois notes candidates, prêtes à être journalisées.

    Chaque mesure est indépendante : l'échec de l'une n'empêche pas les
    autres. Une brique absente vaut mieux qu'un verdict qui plante.
    """
    resultat = {}
    for nom, calcul in (
        ("filtre_marche", lambda: filtre_marche(symbole)),
        ("force_relative", lambda: force_relative(symbole, df)),
        ("alignement_horizons", lambda: alignement_horizons(df)),
    ):
        try:
            r = calcul()
        except Exception:
            r = None
        if r is not None:
            resultat[nom] = r
    return resultat
