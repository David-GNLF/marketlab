"""IC conditionnel au régime de marché — aucun accès réseau."""

import numpy as np
import pandas as pd
import pytest

from marketlab import decision, regimes


def _reference(valeurs):
    idx = pd.date_range("2024-01-01", periods=len(valeurs), freq="B")
    return pd.Series(valeurs, index=idx, dtype=float)


# ---------------------------------------------------------------------------
# Le piège central : le classement ne doit rien savoir du futur
# ---------------------------------------------------------------------------

def test_les_premieres_seances_restent_indeterminees(monkeypatch):
    """Tant qu'on n'a pas assez d'historique, on ne classe pas. Utiliser les
    quantiles de tout l'échantillon reviendrait à juger une décision de 2024
    avec la volatilité de 2026."""
    monkeypatch.setattr(regimes, "HISTORIQUE_MIN", 50)
    r = regimes.classer(_reference(np.linspace(10, 30, 200)))
    assert (r.iloc[:49] == "indetermine").all()
    assert "indetermine" not in set(r.iloc[60:])


def test_le_classement_nutilise_que_le_passe(monkeypatch):
    """Une flambée FINALE de volatilité ne doit pas reclasser le début de
    l'historique : à ces dates-là, personne ne la connaissait."""
    monkeypatch.setattr(regimes, "HISTORIQUE_MIN", 30)
    calme = list(np.linspace(10, 12, 100))
    debut = regimes.classer(_reference(calme)).iloc[30:60].tolist()
    avec_krach = regimes.classer(_reference(calme + [90] * 40)).iloc[30:60].tolist()
    assert debut == avec_krach


def test_les_trois_regimes_sont_distingues(monkeypatch):
    monkeypatch.setattr(regimes, "HISTORIQUE_MIN", 40)
    rng = np.random.default_rng(3)
    r = regimes.classer(_reference(rng.uniform(10, 40, 400)))
    assert set(r.iloc[60:]) == {"calme", "normal", "tendu"}


def test_reference_vide():
    assert regimes.classer(pd.Series(dtype=float)).empty


# ---------------------------------------------------------------------------
# Rattachement d'un verdict à son régime
# ---------------------------------------------------------------------------

def test_un_verdict_prend_le_dernier_regime_CONNU():
    """Un verdict rendu un week-end se rattache au dernier régime connu,
    jamais au suivant — qui n'existait pas encore."""
    reg = pd.Series(["calme", "tendu"],
                    index=pd.to_datetime(["2026-07-01", "2026-07-10"]))
    df = pd.DataFrame({"date": pd.to_datetime(["2026-07-05", "2026-07-15"])})
    out = regimes.attacher(df, reg)
    assert list(out["regime"]) == ["calme", "tendu"]


def test_un_verdict_anterieur_a_tout_est_indetermine():
    reg = pd.Series(["calme"], index=pd.to_datetime(["2026-07-10"]))
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"])})
    assert regimes.attacher(df, reg)["regime"].iloc[0] == "indetermine"


def test_attacher_sur_journal_vide():
    assert regimes.attacher(pd.DataFrame()).empty


# ---------------------------------------------------------------------------
# Le garde-fou sur la correction de Newey-West
# ---------------------------------------------------------------------------

def _journal_synthetique(n_dates, n_actifs, graine=0, bruit=1.0):
    """Journal où la note n'a AUCUN pouvoir : IC attendu nul."""
    rng = np.random.default_rng(graine)
    lignes = []
    for j in range(n_dates):
        for a in range(n_actifs):
            lignes.append({
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=j),
                "symbole": f"A{a}", "horizon": 20,
                "note": rng.normal(),
                "rendement_reel_%": rng.normal() * bruit,
            })
    df = pd.DataFrame(lignes)
    derive = df.groupby("symbole")["rendement_reel_%"].transform("median")
    df["rendement_relatif_%"] = df["rendement_reel_%"] - derive
    return df


def test_une_correction_degeneree_ne_produit_pas_de_certitude():
    """DÉFAUT CORRIGÉ, mesuré sur un vrai sous-échantillon de 63 dates :
    la somme des autocovariances annulait la variance, le plancher 1e-12
    donnait t = −288 819 pour un IC de −0,05. Un t de cet ordre n'est pas un
    signal, c'est une division par zéro."""
    df = _journal_synthetique(70, 10, graine=1)
    res = decision._mesurer_competence(df, "rendement_relatif_%",
                                       horizon_force=20)
    assert abs(res["t"]) < 100, f"t invraisemblable : {res['t']}"


def test_le_rejet_de_la_correction_est_signale():
    """Une correction rejetée veut dire que le t SURESTIME la certitude : le
    taire reviendrait à publier une confiance qu'on sait excessive."""
    df = _journal_synthetique(70, 10, graine=2)
    res = decision._mesurer_competence(df, "rendement_relatif_%",
                                       horizon_force=20)
    assert "correction_rejetee" in res


def test_le_taux_de_faux_positifs_reste_maitrise():
    """Sur du bruit pur, un test à 5 % DOIT se tromper parfois : juger sur un
    seul tirage ne prouverait rien, ni dans un sens ni dans l'autre. C'est le
    TAUX qui se mesure.

    On tolère plus que les 5 % théoriques, et c'est assumé : quand la
    correction de Newey-West est rejetée, on retombe sur la variance simple,
    qui surestime la certitude. C'est le prix payé pour ne plus produire de
    t aberrants — un excès de prudence remplacé par un excès de confiance
    BORNÉ, et signalé par `correction_rejetee`.
    """
    faux_positifs = 0
    essais = 40
    for graine in range(essais):
        res = decision._mesurer_competence(
            _journal_synthetique(120, 12, graine=graine),
            "rendement_relatif_%", horizon_force=20)
        if res.get("sens") in {"positif", "négatif"}:
            faux_positifs += 1
    taux = faux_positifs / essais
    assert taux <= 0.30, f"taux de faux positifs trop élevé : {taux:.0%}"


# ---------------------------------------------------------------------------
# Analyse d'ensemble
# ---------------------------------------------------------------------------

def test_sans_journal_le_module_le_dit(monkeypatch):
    monkeypatch.setattr(decision, "_evaluer_journal",
                        lambda *a, **k: pd.DataFrame())
    a = regimes.analyser()
    assert a["mesurable"] is False and "raison" in a


def test_lecture_quand_rien_nest_prouve():
    par_regime = {"calme": {"note": {"statut": "pas encore mesurable"},
                            "n_dates": 10}}
    lecture = regimes._lecture(par_regime)
    assert "Aucun régime" in lecture and "absence de signal" in lecture


def test_la_lecture_sappuie_sur_la_mesure_stricte():
    """Deux mesures cohabitent ; quand la purge est disponible, c'est ELLE qui
    fait foi. Publier la conclusion la plus généreuse alors qu'on dispose de la
    plus exigeante reviendrait à choisir le chiffre qui arrange."""
    par_regime = {
        "tendu": {"n_dates": 122, "note": {"sens": "négatif"},
                  "purge": {"mesurable": True, "verdict": "effet inversé confirmé",
                            "ic_moyen": -0.254, "part_concluante_%": 82.6,
                            "obs_par_echantillonnage": 13}},
        "calme": {"n_dates": 63, "note": {"statut": "pas mesurable"},
                  "purge": {"mesurable": True, "verdict": "rien de démontré",
                            "ic_moyen": -0.018, "part_concluante_%": 0.0,
                            "obs_par_echantillonnage": 8}},
    }
    lecture = regimes._lecture(par_regime)
    assert "INVERSÉ" in lecture
    assert "83 % des découpages" in lecture
    assert "13 observations" in lecture
    assert "marché calme" not in lecture      # la purge ne le confirme pas
    assert "prudence" in lecture              # 13 observations, c'est peu


def test_une_purge_qui_ne_confirme_pas_prime_sur_le_t_corrige():
    """Cas RÉEL : sur l'ensemble des verdicts, le t corrigé annonçait
    « négatif » alors que seuls 21,7 % des découpages concluent."""
    par_regime = {"normal": {
        "n_dates": 258, "note": {"sens": "négatif"},
        "purge": {"mesurable": True, "verdict": "fragile", "ic_moyen": -0.03,
                  "part_concluante_%": 21.7, "obs_par_echantillonnage": 21}}}
    assert "Aucun régime" in regimes._lecture(par_regime)


def test_sans_purge_on_se_rabat_sur_la_mesure_corrigee():
    par_regime = {"tendu": {"n_dates": 122,
                            "note": {"sens": "négatif",
                                     "episodes_independants": 6}}}
    lecture = regimes._lecture(par_regime)
    assert "mesure corrigée seulement" in lecture
