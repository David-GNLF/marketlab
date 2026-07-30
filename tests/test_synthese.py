"""Synthèse des verdicts : par classe, par devise, et décomposition de la note.

Aucun accès réseau — ce module ne fait que réorganiser des dossiers déjà
produits, il se teste donc entièrement sur des données posées à la main.
"""

import pytest

from marketlab import synthese


def _dossier(symbole, note, classe="Actions", avis="Neutre", composantes=None):
    return {"symbole": symbole, "note_globale": note, "classe": classe,
            "avis": avis, "composantes": composantes or []}


def _comp(nom, note, poids, raisons=None):
    return {"nom": nom, "note": note, "poids": poids,
            "raisons": raisons or []}


# ---------------------------------------------------------------------------
# D'où sort la note
# ---------------------------------------------------------------------------

def test_la_somme_des_contributions_redonne_la_note():
    """C'est ce qui sépare une explication d'une illustration : si la somme ne
    retombe pas sur la note publiée, le graphique ment."""
    d = _dossier("AAPL", 0, composantes=[
        _comp("technique", 60, 0.25), _comp("prevision", 20, 0.20),
        _comp("fondamentaux", -40, 0.15)])
    c = synthese.contributions(d)
    attendu = (60 * .25 + 20 * .20 - 40 * .15) / .60
    assert c["note_reconstituee"] == pytest.approx(attendu, abs=0.15)


def test_le_poids_compte_autant_que_la_note():
    """Une composante à +50 qui pèse 5 % explique MOINS qu'une à +20 qui en
    pèse 30 — la liste brute des notes laissait croire l'inverse."""
    d = _dossier("X", 0, composantes=[
        _comp("saisonnalite", 50, 0.05), _comp("technique", 20, 0.30)])
    lignes = {l["nom"]: l for l in synthese.contributions(d)["lignes"]}
    assert lignes["technique"]["contribution"] > lignes["saisonnalite"]["contribution"]
    assert synthese.contributions(d)["lignes"][0]["nom"] == "technique"


def test_le_desaccord_est_signale():
    """+40 et −40 donnent 0, exactement comme deux composantes muettes : la
    note finale efface la différence, la décomposition la rend."""
    accord = _dossier("A", 0, composantes=[
        _comp("technique", 40, 0.3), _comp("prevision", 30, 0.3)])
    desaccord = _dossier("B", 0, composantes=[
        _comp("technique", 40, 0.3), _comp("prevision", -40, 0.3)])
    assert synthese.contributions(accord)["desaccord"] is False
    assert synthese.contributions(desaccord)["desaccord"] is True


def test_moteur_et_frein_ne_sont_jamais_la_meme_chose():
    """DÉFAUT CORRIGÉ : en prenant le plus gros contributeur en valeur absolue,
    une composante négative était désignée à la fois moteur et frein — constaté
    sur MSFT (technique −9,0 dominante, fondamentaux +7,6)."""
    d = _dossier("MSFT", -3.3, composantes=[
        _comp("technique", -30.5, 0.25), _comp("fondamentaux", 43.2, 0.15)])
    c = synthese.contributions(d)
    assert c["moteur_principal"] == "Fondamentaux"
    assert c["frein_principal"] == "Analyse technique"
    assert c["moteur_principal"] != c["frein_principal"]


def test_sans_frein_quand_tout_pousse_dans_le_meme_sens():
    d = _dossier("A", 0, composantes=[_comp("technique", 40, 0.3),
                                      _comp("prevision", 10, 0.3)])
    c = synthese.contributions(d)
    assert c["frein_principal"] is None
    assert c["moteur_principal"] == "Analyse technique"


def test_chaque_composante_porte_son_fondement():
    """« Sur quoi ce raisonnement est-il basé » doit avoir une réponse pour
    chaque ligne affichée."""
    d = _dossier("A", 0, composantes=[_comp(n, 10, 0.2)
                                      for n in synthese.FONDEMENTS])
    for l in synthese.contributions(d)["lignes"]:
        assert l["fondement"], f"{l['nom']} sans explication"


def test_dossier_sans_composante():
    c = synthese.contributions(_dossier("A", 0))
    assert c["lignes"] == [] and c["desaccord"] is False


# ---------------------------------------------------------------------------
# Par classe d'actif
# ---------------------------------------------------------------------------

def test_regroupement_et_classement_par_note():
    dossiers = [_dossier("A", 40, "Actions"), _dossier("B", 20, "Actions"),
                _dossier("C", -30, "Matières")]
    cats = synthese.par_categorie(dossiers)
    assert [c["classe"] for c in cats] == ["Actions", "Matières"]
    assert cats[0]["note_moyenne"] == pytest.approx(30.0)
    assert cats[0]["n"] == 2


def test_une_classe_dispersee_est_dite_sans_orientation():
    """Moyenne nulle et avis opposés : la classe ne dit rien de commun, et
    l'annoncer « neutre » serait trompeur."""
    dossiers = [_dossier("A", 80, "Actions"), _dossier("B", -80, "Actions")]
    lecture = synthese.par_categorie(dossiers)[0]["lecture"]
    assert "distinguent beaucoup" in lecture


def test_meilleur_et_pire_de_chaque_classe():
    dossiers = [_dossier("A", 40, "Actions"), _dossier("B", -10, "Actions")]
    c = synthese.par_categorie(dossiers)[0]
    assert c["meilleur"]["symbole"] == "A" and c["pire"]["symbole"] == "B"


# ---------------------------------------------------------------------------
# Par devise — le vrai pari
# ---------------------------------------------------------------------------

def test_une_paire_se_reporte_sur_ses_deux_devises():
    """Un avis favorable sur EUR/USD dit du bien de l'euro ET du mal du
    dollar : c'est ce report qui fait apparaître le pari commun."""
    forces = {f["devise"]: f for f in
              synthese.par_devise([_dossier("EURUSD=X", 40, "Forex")])["forces"]}
    assert forces["EUR"]["force"] == pytest.approx(40.0)
    assert forces["USD"]["force"] == pytest.approx(-40.0)


def test_trois_paires_favorables_revelent_un_pari_unique():
    """EUR/USD, GBP/USD et AUD/USD favorables ne sont pas trois idées : c'est
    « vendre le dollar », comptée trois fois."""
    d = [_dossier(s, 40, "Forex")
         for s in ("EURUSD=X", "GBPUSD=X", "AUDUSD=X")]
    res = synthese.par_devise(d)
    usd = next(f for f in res["forces"] if f["devise"] == "USD")
    assert usd["n_paires"] == 3
    assert usd["force"] == pytest.approx(-40.0)
    assert "BAISSE du USD" in res["lecture"]
    assert "répéter" in res["lecture"]


def test_une_devise_vue_sur_une_seule_paire_nest_pas_commentee():
    """DÉFAUT CORRIGÉ : la lecture désignait la devise la mieux placée, souvent
    vue sur UNE paire — sa force n'est alors que le miroir d'un seul verdict."""
    res = synthese.par_devise([_dossier("EURUSD=X", 40, "Forex")])
    assert "un seul verdict" in res["lecture"] or "isolé" in res["lecture"]


def test_les_actifs_hors_forex_sont_ignores():
    res = synthese.par_devise([_dossier("AAPL", 50), _dossier("GC=F", 30)])
    assert res["forces"] == [] and res["paires"] == []


def test_accord_mesure_la_convergence():
    """Trois paires dans le même sens : accord parfait. Deux contre une :
    accord partiel — l'avis est moins solide."""
    unanime = synthese.par_devise([_dossier(s, 40, "Forex") for s in
                                   ("EURUSD=X", "GBPUSD=X", "AUDUSD=X")])
    partage = synthese.par_devise([_dossier("EURUSD=X", 40, "Forex"),
                                   _dossier("GBPUSD=X", 40, "Forex"),
                                   _dossier("AUDUSD=X", -40, "Forex")])
    usd_u = next(f for f in unanime["forces"] if f["devise"] == "USD")
    usd_p = next(f for f in partage["forces"] if f["devise"] == "USD")
    assert abs(usd_u["accord"]) == pytest.approx(1.0)
    assert abs(usd_p["accord"]) < 1.0


def test_bloc_complet_est_serialisable():
    import json
    b = synthese.bloc([_dossier("EURUSD=X", 40, "Forex"),
                       _dossier("AAPL", 20, "Actions")])
    json.dumps(b)
    assert b["n_dossiers"] == 2
    assert set(b["fondements"]) == set(synthese.FONDEMENTS)


def test_bloc_sur_liste_vide():
    b = synthese.bloc([])
    assert b["par_categorie"] == [] and b["par_devise"]["forces"] == []
