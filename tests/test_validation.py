"""Mesure sans recouvrement : éliminer plutôt que corriger. Aucun réseau."""

import numpy as np
import pandas as pd
import pytest

from marketlab import validation


def _journal(n_dates, n_actifs=12, ic_vrai=0.0, graine=0, depart="2024-01-01"):
    """Journal synthétique où la note a un pouvoir CHOISI.

    `ic_vrai` = 0 : bruit pur. Positif : les mieux notés montent ensuite.
    Les dates sont QUOTIDIENNES, donc fortement recouvrantes à horizon 20 —
    c'est exactement la configuration que ce module doit savoir démêler.
    """
    rng = np.random.default_rng(graine)
    lignes = []
    for j in range(n_dates):
        date = pd.Timestamp(depart) + pd.Timedelta(days=j)
        notes = rng.normal(size=n_actifs)
        bruit = rng.normal(size=n_actifs)
        rendements = ic_vrai * notes + np.sqrt(max(1 - ic_vrai ** 2, 0)) * bruit
        for a in range(n_actifs):
            lignes.append({"date": date, "symbole": f"A{a}",
                           "note": notes[a],
                           "rendement_relatif_%": rendements[a]})
    return pd.DataFrame(lignes)


# ---------------------------------------------------------------------------
# La purge elle-même
# ---------------------------------------------------------------------------

def test_les_dates_retenues_sont_espacees_en_SEANCES():
    """Le contrat porte sur des SÉANCES, pas sur des jours calendaires.

    L'ancienne version comparait des jours calendaires à un écart exprimé en
    séances : 23 jours valent environ 16 séances, donc deux observations
    « disjointes » se recouvraient encore de quatre séances sur un horizon de
    20. C'est ce à quoi tenait la validité du veto de régime.
    """
    dates = pd.date_range("2024-01-01", periods=250, freq="B").tolist()
    gardees = validation.dates_espacees(dates, ecart=23)
    seances = [int(np.busday_count(gardees[i].date(), gardees[i + 1].date()))
               for i in range(len(gardees) - 1)]
    assert min(seances) >= 23, f"recouvrement residuel : {min(seances)} seances"


def test_un_ecart_en_jours_ne_suffit_pas():
    """Le defaut exact, rejoue : sur des seances, 23 jours calendaires
    laisseraient passer des dates trop rapprochees."""
    dates = pd.date_range("2024-01-01", periods=250, freq="B").tolist()
    gardees = validation.dates_espacees(dates, ecart=23)
    jours = [(gardees[i + 1] - gardees[i]).days for i in range(len(gardees) - 1)]
    # 23 seances font environ 31 jours calendaires : si la fonction comptait
    # encore en jours, ce minimum serait de 23.
    assert min(jours) > 25


def test_chaque_depart_donne_un_echantillonnage_different():
    """Le point de départ est arbitraire : c'est ce qui permet de parcourir
    plusieurs découpages tous propres, au lieu d'en privilégier un."""
    dates = pd.date_range("2024-01-01", periods=250, freq="B").tolist()
    a = validation.dates_espacees(dates, 23, depart=0)
    b = validation.dates_espacees(dates, 23, depart=5)
    assert a != b and len(a) > 3 and len(b) > 3


def test_ecart_plus_grand_que_lhistorique():
    dates = pd.date_range("2024-01-01", periods=5, freq="D").tolist()
    assert len(validation.dates_espacees(dates, 1000)) == 1


def test_depart_hors_bornes():
    assert validation.dates_espacees([pd.Timestamp("2024-01-01")], 10, depart=9) == []


# ---------------------------------------------------------------------------
# Ce que la méthode doit trancher
# ---------------------------------------------------------------------------

def test_sur_du_bruit_pur_rien_nest_confirme():
    res = validation.ic_purge(_journal(600, ic_vrai=0.0, graine=1), horizon=20)
    assert res["mesurable"]
    assert res["verdict"] in {"rien de démontré", "fragile"}
    assert abs(res["ic_moyen"]) < 0.08


def test_un_vrai_signal_est_confirme_par_la_majorite():
    """Un effet RÉEL doit survivre au découpage, quel que soit le départ —
    c'est précisément ce qui le distingue d'un artefact de recouvrement."""
    res = validation.ic_purge(_journal(600, ic_vrai=0.30, graine=2), horizon=20)
    assert res["verdict"] == "effet confirmé"
    assert res["part_concluante_%"] >= 60
    assert res["ic_moyen"] > 0.15


def test_un_effet_inverse_est_nomme_comme_tel():
    res = validation.ic_purge(_journal(600, ic_vrai=-0.30, graine=3), horizon=20)
    assert res["verdict"] == "effet inversé confirmé"
    assert res["ic_moyen"] < 0


def test_un_signal_porte_par_une_seule_periode_reste_fragile():
    """Cas qui motive tout le module : une poignée de dates très marquées
    suffit à faire conclure une mesure corrigée. Ici, seuls les découpages qui
    tombent dessus concluent — la part concluante le révèle."""
    df = _journal(400, ic_vrai=0.0, graine=4)
    epoque = df["date"] < pd.Timestamp("2024-01-01") + pd.Timedelta(days=25)
    df.loc[epoque, "rendement_relatif_%"] = df.loc[epoque, "note"] * 3
    res = validation.ic_purge(df, horizon=20)
    assert res["verdict"] in {"fragile", "rien de démontré"}
    assert res["part_concluante_%"] < 60


def test_trop_peu_de_dates_le_dit():
    res = validation.ic_purge(_journal(5), horizon=20)
    assert res["mesurable"] is False and "raison" in res


def test_horizon_trop_long_pour_lhistorique():
    res = validation.ic_purge(_journal(60), horizon=200)
    assert res["mesurable"] is False


def test_l_embargo_espace_davantage_que_l_horizon():
    """L'horizon seul suffirait en théorie ; la mémoire du marché déborde un
    peu la fenêtre de mesure."""
    df = _journal(600, graine=5)
    sans = validation.ic_purge(df, horizon=20, embargo=0)
    avec = validation.ic_purge(df, horizon=20, embargo=10)
    assert avec["obs_par_echantillonnage"] < sans["obs_par_echantillonnage"]


# ---------------------------------------------------------------------------
# Confrontation des deux méthodes
# ---------------------------------------------------------------------------

def test_le_desaccord_entre_methodes_est_signale():
    """Quand l'écart-type corrigé conclut et que la purge ne conclut pas, c'est
    la correction qui a surestimé — et il faut le dire."""
    df = _journal(600, ic_vrai=0.0, graine=6)
    corrigee = {"ic_transversal_moyen": -0.07, "t": -2.37, "sens": "négatif"}
    res = validation.comparer_methodes(df, corrigee, horizon=20)
    assert res["accord"] is False
    assert "DIVERGENT" in res["lecture"]
    assert "prudente" in res["lecture"]


def test_l_accord_entre_methodes_est_signale():
    df = _journal(600, ic_vrai=0.0, graine=7)
    corrigee = {"ic_transversal_moyen": -0.01, "t": -0.3, "sens": "indéterminé"}
    res = validation.comparer_methodes(df, corrigee, horizon=20)
    assert res["accord"] is True


def test_comparaison_sans_mesure_corrigee():
    res = validation.comparer_methodes(_journal(600), None, horizon=20)
    assert res["accord"] is None


# ---------------------------------------------------------------------------
# Ce qui est publié doit se lire
# ---------------------------------------------------------------------------

def test_la_lecture_donne_les_chiffres_qui_permettent_de_juger():
    res = validation.ic_purge(_journal(600, ic_vrai=0.30, graine=8), horizon=20)
    for attendu in ("échantillonnage", "indépendantes", "IC moyen"):
        assert attendu in res["lecture"]


def test_le_resultat_est_serialisable():
    import json
    json.dumps(validation.ic_purge(_journal(300), horizon=20))
