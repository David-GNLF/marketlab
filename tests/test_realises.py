"""Pont FRED → surprise sans retard — aucun accès réseau."""

import numpy as np
import pandas as pd
import pytest

from marketlab import realises, surprise


def _serie_mensuelle(valeurs, depart="2026-01-01"):
    idx = pd.date_range(depart, periods=len(valeurs), freq="MS")
    return pd.Series(valeurs, index=idx, dtype=float)


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------

def test_transformations():
    s = _serie_mensuelle([100.0, 101.0, 102.01])
    assert realises.transformer(s, "niveau").iloc[-1] == pytest.approx(102.01)
    assert realises.transformer(s, "diff").iloc[-1] == pytest.approx(1.01)
    assert realises.transformer(s, "pct_m").iloc[-1] == pytest.approx(1.0, abs=1e-6)


def test_transformation_inconnue_leve():
    with pytest.raises(ValueError, match="Transformation inconnue"):
        realises.transformer(_serie_mensuelle([1.0, 2.0]), "n_importe_quoi")


# ---------------------------------------------------------------------------
# Période de référence
# ---------------------------------------------------------------------------

def test_la_publication_porte_sur_le_mois_precedent():
    """L'emploi de juin sort début juillet. Confondre publication et référence
    comparerait un chiffre au consensus d'un autre mois."""
    assert realises.reference("2026-07-03", 1) == pd.Timestamp("2026-06-01")
    assert realises.reference("2026-01-09", 1) == pd.Timestamp("2025-12-01")


def test_decalage_de_deux_mois():
    assert realises.reference("2026-07-08", 2) == pd.Timestamp("2026-05-01")


def test_decalage_nul():
    assert realises.reference("2026-07-15", 0) == pd.Timestamp("2026-07-01")


# ---------------------------------------------------------------------------
# Précision : le nerf de la guerre
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valeur,attendu", [
    (0.3, 1), (0.25, 2), (187000.0, 0), (4.0, 0), (-0.35, 2), (1.234, 3),
])
def test_comptage_des_decimales(valeur, attendu):
    assert realises.decimales(valeur) == attendu


def test_larrondi_supprime_la_fausse_surprise(monkeypatch):
    """Cas RÉEL : FRED recalcule le Core PCE à 0,320051 quand l'agence publie
    0,3. Comparé sans arrondi à un consensus de 0,3, ça fabrique une surprise
    de +0,02 alors que le marché n'a vu AUCUNE surprise. Sans cet arrondi,
    chaque indicateur produirait du bruit systématique."""
    monkeypatch.setattr(realises.fred, "get_series",
                        lambda *a, **k: _serie_mensuelle([100.0, 100.320051]))
    monkeypatch.setitem(realises.CORRESPONDANCES["USD"], "Test m/m",
                        {"serie": "X", "transfo": "pct_m", "facteur": 1,
                         "decalage": 1})
    brut = realises.valeur_realisee("USD", "Test m/m", "2026-03-15")
    arrondi = realises.valeur_realisee("USD", "Test m/m", "2026-03-15", precision=1)
    assert brut == pytest.approx(0.320051, abs=1e-4)
    assert arrondi == pytest.approx(0.3)


def test_indicateur_hors_table_donne_none():
    assert realises.valeur_realisee("USD", "Indicateur inexistant", "2026-07-15") is None
    assert realises.valeur_realisee("XYZ", "CPI m/m", "2026-07-15") is None


def test_periode_absente_de_la_serie_donne_none(monkeypatch):
    """Une publication du jour dont le chiffre n'est pas encore chez FRED ne
    doit pas produire une surprise inventée."""
    monkeypatch.setattr(realises.fred, "get_series",
                        lambda *a, **k: _serie_mensuelle([100.0, 101.0]))
    monkeypatch.setitem(realises.CORRESPONDANCES["USD"], "Test m/m",
                        {"serie": "X", "transfo": "pct_m", "facteur": 1,
                         "decalage": 1})
    assert realises.valeur_realisee("USD", "Test m/m", "2027-01-15") is None


def test_le_facteur_retrouve_lunite_du_flux(monkeypatch):
    """ForexFactory écrit « 147K » = 147000 ; PAYEMS est en milliers."""
    monkeypatch.setattr(realises.fred, "get_series",
                        lambda *a, **k: _serie_mensuelle([150000.0, 150147.0]))
    v = realises.valeur_realisee("USD", "Non-Farm Employment Change", "2026-03-15")
    assert v == pytest.approx(147_000.0)


# ---------------------------------------------------------------------------
# Vérification et tri des correspondances
# ---------------------------------------------------------------------------

def test_concordance_tolere_larrondi_mais_pas_lerreur_dunite():
    assert realises._concordent(0.32, 0.3) is True       # arrondi d'affichage
    assert realises._concordent(0.3, 0.3) is True
    assert realises._concordent(300.0, 0.3) is False     # facteur 1000 oublié
    assert realises._concordent(209000.0, 187000.0) is False
    assert realises._concordent(None, 0.3) is False
    assert realises._concordent(float("nan"), 0.3) is False


def test_une_correspondance_non_verifiee_nest_pas_utilisee():
    """Mieux vaut une surprise absente qu'une surprise fausse, qui aurait
    l'apparence d'un signal."""
    rapport = pd.DataFrame([
        {"devise": "USD", "evenement": "CPI m/m", "concorde": True},
        {"devise": "USD", "evenement": "Trade Balance", "concorde": False},
    ])
    assert realises.correspondances_valides(rapport) == {("USD", "CPI m/m")}


def test_correspondances_valides_sur_rapport_vide():
    assert realises.correspondances_valides(pd.DataFrame()) == set()


# ---------------------------------------------------------------------------
# Surprise sans retard
# ---------------------------------------------------------------------------

def _calendrier(lignes):
    return pd.DataFrame(lignes, columns=surprise.COLONNES_CAL)


def test_surprise_sans_retard(monkeypatch):
    monkeypatch.setattr(realises.fred, "get_series",
                        lambda *a, **k: _serie_mensuelle([100.0, 100.5]))
    monkeypatch.setitem(realises.CORRESPONDANCES["USD"], "Test m/m",
                        {"serie": "X", "transfo": "pct_m", "facteur": 1,
                         "decalage": 1})
    cal = _calendrier([["2026-03-15", "USD", "Test m/m", "High", 0.2, 0.1]])
    out = surprise.sans_retard(cal, valides={("USD", "Test m/m")})
    assert len(out) == 1
    assert out.loc[0, "resultat"] == pytest.approx(0.5)
    assert out.loc[0, "ecart"] == pytest.approx(0.3)


def test_sans_correspondance_valide_rien_ne_sort():
    cal = _calendrier([["2026-03-15", "USD", "CPI m/m", "High", 0.2, 0.1]])
    assert surprise.sans_retard(cal, valides=set()).empty


def test_la_voie_sans_retard_prime_sur_le_chainage(monkeypatch):
    """Même publication vue par les deux voies : on garde la mesure la plus
    précoce, qui n'est pas passée par une valeur « précédente » révisable."""
    monkeypatch.setattr(surprise, "sans_retard", lambda *a, **k: pd.DataFrame([{
        "date": "2026-06-10", "devise": "USD", "evenement": "CPI m/m",
        "impact": "High", "prevision": 0.3, "resultat": 0.5, "ecart": 0.2,
    }])[surprise.COLONNES])
    cal = _calendrier([
        ["2026-06-10", "USD", "CPI m/m", "High", 0.3, 0.2],
        ["2026-07-10", "USD", "CPI m/m", "High", 0.4, 0.9],
    ])
    out = surprise.surprises(cal)
    ligne = out[out["date"] == "2026-06-10"].iloc[0]
    assert ligne["source"] == "fred"
    assert ligne["resultat"] == pytest.approx(0.5)   # FRED, pas le 0.9 chaîné


def test_le_chainage_couvre_ce_que_fred_ignore(monkeypatch):
    """L'euro n'est pas dans la table FRED : il doit rester servi par le
    chaînage, pas disparaître."""
    monkeypatch.setattr(surprise, "sans_retard",
                        lambda *a, **k: pd.DataFrame(columns=surprise.COLONNES))
    cal = _calendrier([
        ["2026-06-10", "EUR", "German CPI m/m", "High", 0.2, 0.1],
        ["2026-07-10", "EUR", "German CPI m/m", "High", 0.3, 0.4],
    ])
    out = surprise.surprises(cal)
    assert len(out) == 1
    assert out.loc[0, "source"] == "chainage"
    assert out.loc[0, "ecart"] == pytest.approx(0.2)


def test_surprises_sur_calendrier_vide(monkeypatch):
    monkeypatch.setattr(surprise, "sans_retard",
                        lambda *a, **k: pd.DataFrame(columns=surprise.COLONNES))
    assert surprise.surprises(_calendrier([])).empty
