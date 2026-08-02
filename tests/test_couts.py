"""Coût réel d'une idée — aucun accès réseau."""

import pytest

from marketlab import couts


@pytest.fixture(autouse=True)
def _table_seule(monkeypatch):
    """Fige ces tests sur la TABLE de spreads.

    Depuis que le spread est mesuré (estimateur de Roll, relevé versionné dans
    data_local/spreads_mesures.csv), `couts()` préfère la mesure quand elle
    existe. Ces tests vérifient l'arithmétique des coûts, pas l'estimateur —
    qui a son propre fichier de tests. Sans cette fixture, ils passeraient en
    local (pas de CSV) et casseraient en CI (CSV commité) : un test dont le
    verdict dépend de l'environnement ne teste rien.
    """
    from marketlab import microstructure
    monkeypatch.setattr(microstructure, "spread_median", lambda *a, **k: None)


def test_le_spread_est_paye_deux_fois():
    """Un aller-retour, c'est une entrée ET une sortie."""
    c = couts.couts("AAPL", horizon=0, levier=1)
    assert c["cout_spread_%"] == pytest.approx(2 * couts.SPREAD_PCT["Actions"])


def test_sans_levier_il_ny_a_pas_de_portage():
    """On ne paie d'intérêts que sur ce qu'on emprunte."""
    c = couts.couts("EURUSD=X", horizon=20, levier=1)
    assert c["cout_financement_%"] == pytest.approx(0.0)


def test_le_portage_croit_avec_la_duree():
    court = couts.couts("EURUSD=X", horizon=5, levier=5)
    long = couts.couts("EURUSD=X", horizon=40, levier=5)
    assert long["cout_financement_%"] > court["cout_financement_%"] * 3


def test_le_portage_domine_sur_un_horizon_long():
    """Contre-intuitif et mesuré : à 20 séances et effet 5 sur le forex, le
    portage pèse 92 % du coût. C'est le spread que tout le monde regarde."""
    c = couts.couts("EURUSD=X", horizon=20, levier=5)
    assert c["part_financement_%"] > 80


def test_le_levier_multiplie_le_cout_sur_la_mise():
    """LE POINT QUE LE LEVIER REND CONTRE-INTUITIF. Beaucoup le voient comme un
    simple multiplicateur de gain ; il multiplie aussi ce qu'il faut rembourser
    avant de gagner. Mesuré : 9,35 % de la mise à effet 20 sur EURUSD."""
    faible = couts.couts("EURUSD=X", horizon=20, levier=2)
    fort = couts.couts("EURUSD=X", horizon=20, levier=20)
    assert fort["seuil_mise_%"] > faible["seuil_mise_%"] * 10
    assert fort["seuil_mise_%"] > 9


def test_les_deux_lectures_sont_coherentes():
    """`seuil_mise_%` doit valoir exactement `seuil_actif_%` × levier : ce sont
    deux façons de lire le MÊME coût, pas deux calculs."""
    for levier in (1, 3, 5, 10):
        c = couts.couts("GC=F", horizon=20, levier=levier)
        assert c["seuil_mise_%"] == pytest.approx(
            c["seuil_actif_%"] * levier, abs=0.01)


def test_chaque_classe_a_son_spread():
    assert couts.classe_actif("EURUSD=X") == "Forex"
    assert couts.classe_actif("GC=F") == "Matières"
    assert couts.classe_actif("BTCUSDT") == "Crypto"
    assert couts.classe_actif("^GSPC") == "Indices"
    assert couts.classe_actif("AAPL") == "Actions"
    forex = couts.couts("EURUSD=X", horizon=0, levier=1)["cout_spread_%"]
    matiere = couts.couts("GC=F", horizon=0, levier=1)["cout_spread_%"]
    assert matiere > forex          # une matière première coûte plus cher


# ---------------------------------------------------------------------------
# Espérance nette : le chiffre qui décide
# ---------------------------------------------------------------------------

def test_une_esperance_maigre_est_annulee_par_les_frais():
    """Cas central : +0,15 % attendus sur le forex à effet 5, contre 0,40 % de
    coût. L'idée était affichée « favorable » et elle perd de l'argent."""
    n = couts.net(0.15, "EURUSD=X", horizon=20)
    assert n["survit_aux_frais"] is False
    assert n["esperance_nette_%"] < 0
    assert "ANNULÉE" in n["lecture"]


def test_une_esperance_solide_survit():
    n = couts.net(2.30, "AAPL", horizon=20)
    assert n["survit_aux_frais"] is True
    assert n["esperance_nette_%"] == pytest.approx(2.30 - n["seuil_actif_%"], abs=1e-3)


def test_une_idee_deja_negative_est_dite_telle_quelle():
    n = couts.net(-1.0, "GC=F", horizon=20)
    assert "déjà négative avant frais" in n["lecture"]


def test_une_marge_mince_est_signalee_comme_telle():
    """Survivre de justesse n'est pas survivre : une exécution un peu moins
    bonne que prévu suffit à effacer le reste."""
    seuil = couts.couts("EURUSD=X", horizon=20)["seuil_actif_%"]
    n = couts.net(seuil * 1.6, "EURUSD=X", horizon=20)
    assert n["survit_aux_frais"] is True
    assert "absorbent" in n["lecture"]


def test_horizon_nul():
    c = couts.couts("AAPL", horizon=0)
    assert c["cout_financement_%"] == pytest.approx(0.0)
    assert c["seuil_actif_%"] > 0        # le spread reste dû


def test_le_resultat_est_serialisable():
    import json
    json.dumps(couts.net(1.0, "AAPL", horizon=20))
