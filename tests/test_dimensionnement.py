"""Combien mettre sur une idée — aucun accès réseau."""

import pytest

from marketlab import dimensionnement as dim


@pytest.fixture(autouse=True)
def _sans_structure(monkeypatch):
    """Neutralise la part de saut : le CSV sera commité, et ces tests
    vérifient la géométrie de la chaîne, pas la structure d'un actif réel —
    un AAPL à 20 % de sauts changerait leurs valeurs exactes selon
    l'environnement, le défaut même qu'on a déjà payé deux fois."""
    from marketlab import microstructure
    monkeypatch.setattr(microstructure, "part_sauts", lambda *a, **k: None)


def _plan(risque_pct=5.0, esperance=2.0, survit=True, seuil=0.31):
    return {
        "risque_%": risque_pct,
        "esperance_%": esperance,
        "couts": {"survit_aux_frais": survit,
                  "esperance_brute_%": esperance,
                  "esperance_nette_%": esperance - seuil,
                  "seuil_actif_%": seuil},
    }


# ---------------------------------------------------------------------------
# L'invariant : la perte si stop vaut TOUJOURS le risque visé
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stop_pct", [6.5, 10.0, 15.0])
def test_la_perte_si_stop_vaut_le_risque_vise(stop_pct):
    """C'est TOUT l'objet du dimensionnement par le risque : que l'actif soit
    calme ou agité, que le stop soit proche ou lointain, ce qu'on perd si le
    stop est touché reste le même. Une taille fixe en pourcentage du capital ne
    donne pas cette propriété — c'est même exactement ce qu'elle casse.

    Stops assez larges pour qu'aucun plafond ne s'applique : le cas où un
    plafond mord est traité par le test suivant, qui vérifie la propriété qui
    compte alors."""
    r = dim.dimensionner("AAPL", _plan(risque_pct=stop_pct), 1000.0,
                         risque_pct=1.0, levier=2)
    assert r["retenue"] is True
    assert r["risque_%_equite"] == pytest.approx(1.0, abs=0.05)
    assert r["perte_si_stop"] == pytest.approx(10.0, abs=0.5)


@pytest.mark.parametrize("stop_pct", [0.1, 0.5, 2.0, 6.5, 15.0])
def test_la_chaine_ne_peut_que_REDUIRE_le_risque(stop_pct):
    """La propriété de sûreté, et la seule qui vaille sur toute la plage : un
    plafond peut faire descendre le risque SOUS la cible, jamais au-dessus.
    Un dimensionnement qui dépasserait sa propre cible ne serait pas un
    encadrement, ce serait un piège — d'autant plus dangereux qu'il afficherait
    « risque visé 1 % ».

    Sur un stop à 0,1 %, le calcul par le risque justifierait d'engager la
    moitié du compte ; c'est le plafond par position qui l'en empêche."""
    r = dim.dimensionner("AAPL", _plan(risque_pct=stop_pct), 1000.0,
                         risque_pct=1.0, levier=2)
    if r["retenue"]:
        assert r["risque_%_equite"] <= 1.0 + 0.05


def test_un_stop_lointain_donne_une_position_plus_petite():
    """À risque égal, plus le stop est loin, moins on engage. C'est l'inverse
    de l'intuition « j'y crois donc je mets gros »."""
    proche = dim.dimensionner("AAPL", _plan(risque_pct=2.0), 1000.0, levier=2)
    lointain = dim.dimensionner("AAPL", _plan(risque_pct=10.0), 1000.0, levier=2)
    assert lointain["notionnel"] < proche["notionnel"]


def test_la_taille_ne_depend_pas_de_la_note():
    """La note n'a pas démontré de pouvoir de classement : doser selon elle
    reviendrait à doser selon du bruit. Elle sert de porte, pas d'amplificateur.
    Deux plans identiques doivent donner la même mise, quelle que soit
    l'espérance — tant qu'elle survit aux frais."""
    modeste = dim.dimensionner("AAPL", _plan(esperance=1.0), 1000.0, levier=2)
    fort = dim.dimensionner("AAPL", _plan(esperance=25.0), 1000.0, levier=2)
    assert modeste["mise"] == pytest.approx(fort["mise"])


# ---------------------------------------------------------------------------
# Les portes : ce qui annule la position
# ---------------------------------------------------------------------------

def test_une_idee_qui_ne_survit_pas_aux_frais_nest_pas_dimensionnee():
    """Le dimensionnement ne rattrape pas une espérance négative : il ne fait
    que choisir combien perdre."""
    r = dim.dimensionner("EURUSD=X", _plan(esperance=0.12, survit=False),
                         1000.0)
    assert r["retenue"] is False and r["mise"] == 0.0
    assert any("frais" in e for e in r["etapes"])
    assert "perdante" in r["lecture"]


def test_un_regime_suspendu_annule_la_position():
    r = dim.dimensionner("AAPL", _plan(), 1000.0,
                         avis_suspendu={"regime": "tendu"})
    assert r["retenue"] is False
    assert "erreur connue" in r["lecture"]


def test_sans_stop_exploitable_aucune_taille():
    """Sans distance connue, aucune taille ne peut être justifiée — et inventer
    une taille par défaut serait pire que ne rien proposer."""
    r = dim.dimensionner("AAPL", _plan(risque_pct=0.0), 1000.0)
    assert r["retenue"] is False
    assert "stop" in r["lecture"].lower()


def test_equite_nulle():
    assert dim.dimensionner("AAPL", _plan(), 0.0)["retenue"] is False


def test_plan_absent():
    assert dim.dimensionner("AAPL", {}, 1000.0)["retenue"] is False


# ---------------------------------------------------------------------------
# Les plafonds
# ---------------------------------------------------------------------------

def test_le_plafond_par_position_borne_un_stop_tres_proche():
    """Un stop à 0,1 % justifierait, par le calcul, d'engager la moitié du
    compte. Un simple écart de cotation suffirait à le franchir."""
    r = dim.dimensionner("AAPL", _plan(risque_pct=0.1), 1000.0, levier=2)
    assert r["mise_%_equite"] <= dim.PLAFOND_MISE_PCT + 0.01
    assert any("plafond" in e for e in r["etapes"])


def test_une_mise_residuelle_derisoire_fait_ecarter(monkeypatch):
    monkeypatch.setattr(dim, "MISE_MIN", 500.0)
    r = dim.dimensionner("AAPL", _plan(risque_pct=5.0), 1000.0, levier=2)
    assert r["retenue"] is False
    assert "ne vaut pas ses frais" in r["lecture"]


def test_la_concentration_reduit_la_mise(monkeypatch):
    """Le contrôle de concentration s'applique APRÈS le calcul par le risque :
    une idée correctement dimensionnée peut rester un pari déjà pris."""
    from marketlab import risque_portefeuille
    monkeypatch.setattr(risque_portefeuille, "evaluer", lambda *a, **k: {
        "facteur": 0.5, "mesurable": True,
        "raisons": ["même pari que EURUSD=X"]})
    seul = dim.dimensionner("GBPUSD=X", _plan(), 1000.0, levier=2)
    avec = dim.dimensionner("GBPUSD=X", _plan(), 1000.0, levier=2,
                            positions=[{"symbole": "EURUSD=X", "sens": "long",
                                        "marge": 50.0, "levier": 2}])
    assert avec["mise"] == pytest.approx(seul["mise"] * 0.5, rel=0.02)
    assert any("concentration" in e for e in avec["etapes"])


# ---------------------------------------------------------------------------
# La chaîne doit être lisible
# ---------------------------------------------------------------------------

def test_chaque_reduction_est_nommee():
    """Une taille sans explication est un chiffre qu'on suit ou qu'on ignore ;
    une taille expliquée est une décision."""
    r = dim.dimensionner("AAPL", _plan(risque_pct=0.1), 1000.0, levier=2)
    assert len(r["etapes"]) >= 2
    assert all(isinstance(e, str) and e for e in r["etapes"])


def test_la_lecture_dit_ce_qui_a_determine_la_taille():
    r = dim.dimensionner("AAPL", _plan(), 1000.0, levier=2)
    assert "pas la note" in r["lecture"]


def test_comparaison_a_la_taille_fixe():
    """Montre ce que le changement fait vraiment : sur un actif calme il
    autorise davantage, sur un actif agité beaucoup moins."""
    r = dim.dimensionner("AAPL", _plan(risque_pct=6.5), 1000.0, levier=2)
    c = dim.comparer_a_taille_fixe(r, 1000.0, part_fixe_pct=5.0)
    assert c["fixe"] == pytest.approx(50.0)
    assert c["rapport"] == pytest.approx(r["mise"] / 50.0, rel=0.01)
    assert "risque" in c["lecture"]


def test_le_resultat_est_serialisable():
    import json
    json.dumps(dim.dimensionner("AAPL", _plan(), 1000.0))


# ---------------------------------------------------------------------------
# La part de saut : ce que le stop ne protège pas
# ---------------------------------------------------------------------------

def test_la_part_de_saut_reduit_la_mise(monkeypatch):
    """Deux actifs de même volatilité mais de structures différentes ne
    doivent pas porter la même mise : le stop de l'un s'exécute où on l'a mis,
    celui de l'autre est traversé."""
    from marketlab import microstructure
    lisse = dim.dimensionner("AAPL", _plan(), 1000.0, levier=2)
    monkeypatch.setattr(microstructure, "part_sauts",
                        lambda *a, **k: {"part_saut": 0.30, "n_seances": 40})
    sauteur = dim.dimensionner("AAPL", _plan(), 1000.0, levier=2)
    assert sauteur["mise"] == pytest.approx(lisse["mise"] / 1.30, rel=0.01)
    assert any("TRAVERSE les stops" in e for e in sauteur["etapes"])


def test_une_part_de_saut_negligeable_ne_reduit_rien(monkeypatch):
    from marketlab import microstructure
    monkeypatch.setattr(microstructure, "part_sauts",
                        lambda *a, **k: {"part_saut": 0.03, "n_seances": 40})
    r = dim.dimensionner("AAPL", _plan(), 1000.0, levier=2)
    assert not any("sauts" in e for e in r["etapes"])


# ---------------------------------------------------------------------------
# Kelly : un plafond, jamais une cible
# ---------------------------------------------------------------------------

def _plan_avec_probas(**kw):
    p = _plan(**{k: v for k, v in kw.items() if k in
                 ("risque_pct", "esperance", "survit", "seuil")})
    p["proba_toucher_objectif_%"] = kw.get("p_obj", 42.0)
    p["proba_toucher_stop_%"] = kw.get("p_stop", 40.0)
    p["gain_potentiel_%"] = kw.get("gain", 10.0)
    if "risque_pct" in kw:
        p["risque_%"] = kw["risque_pct"]
    return p


def test_kelly_est_calcule_et_affiche_la_marge():
    """f* = (p·g − q·s) / (g·s·(p+q)) — et la part de Kelly réellement
    utilisée dit la marge de sécurité du dimensionnement par le risque."""
    plan = _plan_avec_probas(risque_pct=10.0, p_obj=42, p_stop=40, gain=10)
    r = dim.dimensionner("AAPL", plan, 1000.0, levier=2)
    attendu = (0.42 * 0.10 - 0.40 * 0.10) / (0.10 * 0.10 * 0.82)
    assert r["kelly"]["fraction"] == pytest.approx(attendu, rel=1e-3)
    assert r["kelly"]["part_utilisee_%"] < 100


def test_kelly_plafonne_une_exposition_excessive(monkeypatch):
    """Le jour où un gros budget de risque et un stop large dépassent Kelly,
    c'est Kelly qui a raison : au-delà, même des probabilités EXACTES perdent
    de l'argent en croissance composée."""
    monkeypatch.setattr(dim, "PLAFOND_MISE_PCT", 100.0)
    plan = _plan_avec_probas(risque_pct=10.0, p_obj=42, p_stop=40, gain=10)
    r = dim.dimensionner("AAPL", plan, 1000.0, levier=2, risque_pct=5.0)
    kelly_notionnel = r["kelly"]["plafond_notionnel"]
    assert r["notionnel"] == pytest.approx(kelly_notionnel, rel=1e-3)
    assert any("Kelly" in e for e in r["etapes"])
    assert r["risque_%_equite"] < 5.0        # la chaîne n'a fait que réduire


def test_sans_probas_pas_de_kelly():
    """On ne fabrique pas un plafond avec des chiffres absents."""
    r = dim.dimensionner("AAPL", _plan(), 1000.0, levier=2)
    assert "kelly" not in r
