"""Indice de surprise économique — aucun accès réseau."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlab import surprise


# ---------------------------------------------------------------------------
# Lecture des valeurs du flux
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texte,attendu", [
    ("3.2%", 3.2), ("-0.3%", -0.3), ("250K", 250_000.0), ("1.5M", 1_500_000.0),
    ("2.1B", 2_100_000_000.0), ("1,250", 1250.0), ("<0.1%", 0.1), ("4.25", 4.25),
    ("-45", -45.0),
])
def test_lecture_des_ecritures_du_flux(texte, attendu):
    assert surprise.nombre(texte) == pytest.approx(attendu)


@pytest.mark.parametrize("texte", ["", " ", "-", "--", None, "n/a", "Tentative"])
def test_valeur_illisible_donne_none_jamais_zero(texte):
    """Zéro se lirait « surprise nulle », pas « inconnu » — la confusion
    ferait entrer du faux dans la moyenne."""
    assert surprise.nombre(texte) is None


# ---------------------------------------------------------------------------
# Sens des indicateurs
# ---------------------------------------------------------------------------

def test_le_chomage_a_le_sens_inverse():
    """Un chômage PLUS ÉLEVÉ que prévu est une mauvaise surprise, alors que
    l'écart arithmétique est positif."""
    assert surprise.sens("Unemployment Rate") == -1
    assert surprise.sens("Unemployment Claims") == 1  # non listé : sens direct
    assert surprise.sens("Jobless Claims") == -1
    assert surprise.sens("Crude Oil Inventories") == -1


def test_les_indicateurs_ordinaires_ont_le_sens_direct():
    for titre in ("CPI m/m", "Non-Farm Employment Change", "GDP q/q",
                  "Retail Sales m/m", "ifo Business Climate"):
        assert surprise.sens(titre) == 1


# ---------------------------------------------------------------------------
# Reconstitution par chaînage
# ---------------------------------------------------------------------------

def _calendrier(lignes):
    return pd.DataFrame(lignes, columns=surprise.COLONNES_CAL)


def test_le_precedent_de_la_suivante_donne_le_resultat():
    """Cœur du module : CPI de juin prévu à 0,3 ; la publication de juillet
    annonce « précédent 0,5 » — donc juin est sorti à 0,5, surprise +0,2."""
    cal = _calendrier([
        ["2026-06-10", "USD", "CPI m/m", "High", 0.3, 0.2],
        ["2026-07-10", "USD", "CPI m/m", "High", 0.4, 0.5],
    ])
    rec = surprise.reconstituer(cal)
    assert len(rec) == 1
    assert rec.loc[0, "date"] == "2026-06-10"
    assert rec.loc[0, "resultat"] == pytest.approx(0.5)
    assert rec.loc[0, "ecart"] == pytest.approx(0.2)


def test_la_derniere_publication_na_pas_encore_de_surprise():
    """Son résultat n'est pas encore révélé : c'est normal, pas une perte."""
    cal = _calendrier([
        ["2026-06-10", "USD", "CPI m/m", "High", 0.3, 0.2],
        ["2026-07-10", "USD", "CPI m/m", "High", 0.4, 0.5],
        ["2026-08-10", "USD", "CPI m/m", "High", 0.3, 0.4],
    ])
    rec = surprise.reconstituer(cal)
    assert list(rec["date"]) == ["2026-06-10", "2026-07-10"]


def test_le_chainage_ne_traverse_pas_les_indicateurs():
    """Le « précédent » d'un indicateur ne doit jamais servir de résultat à un
    autre, ni d'une devise à l'autre."""
    cal = _calendrier([
        ["2026-06-10", "USD", "CPI m/m", "High", 0.3, 0.2],
        ["2026-06-11", "USD", "Retail Sales m/m", "High", 0.5, 0.1],
        ["2026-07-10", "USD", "CPI m/m", "High", 0.4, 0.9],
        ["2026-07-11", "EUR", "CPI m/m", "High", 0.2, 0.1],
    ])
    rec = surprise.reconstituer(cal)
    assert len(rec) == 1
    assert rec.loc[0, "evenement"] == "CPI m/m"
    assert rec.loc[0, "resultat"] == pytest.approx(0.9)


def test_le_sens_inverse_sapplique_a_lecart():
    cal = _calendrier([
        ["2026-06-10", "USD", "Unemployment Rate", "High", 4.0, 4.1],
        ["2026-07-10", "USD", "Unemployment Rate", "High", 4.2, 4.3],
    ])
    rec = surprise.reconstituer(cal)
    # sorti à 4,3 pour 4,0 attendus : plus de chômage que prévu = mauvaise
    assert rec.loc[0, "ecart"] == pytest.approx(-0.3)


def test_une_publication_sans_prevision_est_ignoree():
    cal = _calendrier([
        ["2026-06-10", "USD", "CPI m/m", "High", np.nan, 0.2],
        ["2026-07-10", "USD", "CPI m/m", "High", 0.4, 0.5],
    ])
    assert surprise.reconstituer(cal).empty


def test_reconstitution_sur_calendrier_vide():
    assert surprise.reconstituer(_calendrier([])).empty
    assert surprise.reconstituer(pd.DataFrame()).empty


# ---------------------------------------------------------------------------
# Accumulation immuable
# ---------------------------------------------------------------------------

def test_le_premier_instantane_fait_foi():
    """Une prévision révisée après coup ne doit pas remplacer celle qu'on avait
    observée : une surprise se mesure contre l'attente du moment."""
    ancien = _calendrier([["2026-07-10", "USD", "CPI m/m", "High", 0.3, 0.2]])
    revise = _calendrier([["2026-07-10", "USD", "CPI m/m", "High", 0.9, 0.2]])
    fusion = surprise.fusionner(ancien, revise)
    assert len(fusion) == 1
    assert fusion.loc[0, "prevision"] == pytest.approx(0.3)


def test_une_publication_nouvelle_sajoute():
    ancien = _calendrier([["2026-07-10", "USD", "CPI m/m", "High", 0.3, 0.2]])
    neuf = _calendrier([["2026-07-11", "EUR", "PMI", "Medium", 51.0, 50.4]])
    assert len(surprise.fusionner(ancien, neuf)) == 2


def test_fusion_de_deux_calendriers_vides():
    assert surprise.fusionner(_calendrier([]), _calendrier([])).empty


# ---------------------------------------------------------------------------
# Normalisation et score
# ---------------------------------------------------------------------------

def _surprises(devise, ecarts, evenement="CPI m/m", depart="2026-07-01"):
    dates = pd.date_range(depart, periods=len(ecarts), freq="7D").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "date": dates, "devise": devise, "evenement": evenement,
        "impact": "High", "prevision": 0.3,
        "resultat": [0.3 + e for e in ecarts], "ecart": ecarts,
    })[surprise.COLONNES]


def test_normalisation_rend_les_indicateurs_comparables():
    """Deux indicateurs d'échelles très différentes doivent produire des z du
    même ordre — c'est tout l'objet de la division par la dispersion."""
    petit = _surprises("USD", [0.1, -0.1, 0.2, -0.2, 0.1, -0.1], "CPI m/m")
    grand = _surprises("USD", [10e3, -10e3, 20e3, -20e3, 10e3, -10e3],
                       "Jobless Claims")
    z = surprise.normaliser(pd.concat([petit, grand], ignore_index=True))
    ecart_z = z.groupby("evenement")["z"].std()
    assert ecart_z.max() / ecart_z.min() < 1.5


def test_le_z_est_borne():
    """Une révision d'assiette ne doit pas emporter tout un trimestre."""
    ecarts = [0.1, -0.1, 0.1, -0.1, 0.1, 50.0]
    z = surprise.normaliser(_surprises("USD", ecarts))
    assert z["z"].max() <= 4.0


def test_score_positif_quand_les_chiffres_surprennent_bien():
    hist = _surprises("USD", [0.2, 0.3, 0.25, 0.2, 0.3, 0.2])
    scores = surprise.score_par_devise(hist, aujourdhui=dt.date(2026, 8, 20))
    assert scores["USD"]["score"] > 0
    assert scores["USD"]["n_publications"] == 6


def test_la_fenetre_ecarte_les_publications_trop_anciennes():
    hist = _surprises("USD", [0.2] * 6, depart="2025-01-01")
    assert surprise.score_par_devise(hist, aujourdhui=dt.date(2026, 8, 20)) == {}


def test_score_sur_historique_vide():
    assert surprise.score_par_devise(pd.DataFrame(columns=surprise.COLONNES)) == {}


# ---------------------------------------------------------------------------
# Note d'une paire
# ---------------------------------------------------------------------------

def test_paire_reconnue_et_paire_ignoree():
    assert surprise.devises_de("EURUSD=X") == ("EUR", "USD")
    assert surprise.devises_de("AAPL") is None
    assert surprise.devises_de("GC=F") is None
    assert surprise.devises_de("BTCUSDT") is None


def test_la_note_oppose_les_deux_economies():
    scores = {"EUR": {"score": 30.0, "n_publications": 8},
              "USD": {"score": -10.0, "n_publications": 9}}
    n = surprise.note("EURUSD=X", scores)
    assert n["note"] == pytest.approx(40.0)
    assert n["base"] == "EUR" and n["contre"] == "USD"
    assert "mieux" in n["lecture"]


def test_pas_de_note_sans_les_deux_devises():
    """Jamais de repli sur une moitié de paire : sans les deux côtés, il n'y a
    pas de comparaison à faire."""
    assert surprise.note("EURUSD=X", {"EUR": {"score": 30.0,
                                              "n_publications": 8}}) is None
    assert surprise.note("EURUSD=X", {}) is None


def test_pas_de_note_hors_forex():
    """La surprise économique est un moteur de DEVISE : l'étendre aux actions
    demanderait une chaîne de transmission qu'on n'a pas mesurée."""
    assert surprise.note("AAPL", {"USD": {"score": 20.0, "n_publications": 9}}) is None
