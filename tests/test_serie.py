"""Séries de bougies : format publié, garde-fous du relais, noms d'actifs.

Trois choses sont vérifiées ici, et chacune correspond à un défaut réel :

1. le format publié — un `NaN` laissé dans le JSON le rend illisible EN ENTIER
   par le navigateur, et les premières valeurs d'une SMA 200 sont vides par
   construction : sans conversion, une fiche sur deux serait cassée ;
2. les garde-fous de `serie.php` — sans liste blanche de symboles, le relais
   serait un proxy ouvert vers n'importe quelle URL ;
3. la table des noms — la fiche affichait « AAPL » en titre là où elle devait
   afficher « Apple ». Le test porte sur TOUT le périmètre suivi, pour qu'un
   actif ajouté demain ne réintroduise pas le trou en silence.
"""

import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from marketlab import config, serie


def _bougies(n=10, avec_trous=False):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    close = np.linspace(100, 110, n)
    df = pd.DataFrame({
        "open": close - 0.5, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.arange(n) * 1000.0,
        "sma50": close, "sma200": close,
    }, index=idx)
    if avec_trous:
        df.loc[df.index[:3], "sma200"] = np.nan
    return df


def test_format_colonnaire_et_json_valide():
    bloc = serie.serie(_bougies(5))
    assert list(bloc["t"]) == ["2026-01-01", "2026-01-02", "2026-01-03",
                               "2026-01-04", "2026-01-05"]
    assert set("ohlcv") <= set(bloc)
    assert bloc["n"] == 5
    # toutes les colonnes ont la même longueur : le front les lit en parallèle,
    # un décalage d'un cran ferait mentir chaque bougie
    longueurs = {len(v) for k, v in bloc.items() if isinstance(v, list)}
    assert longueurs == {5}


def test_les_trous_deviennent_null_et_pas_nan():
    bloc = serie.serie(_bougies(6, avec_trous=True))
    assert bloc["sma200"][:3] == [None, None, None]
    # le vrai test : json.dumps produit du JSON que JSON.parse accepte.
    # « NaN » passe json.dumps sans broncher et casse le navigateur.
    texte = json.dumps(bloc)
    assert "NaN" not in texte
    json.loads(texte)


def test_volumes_entiers():
    bloc = serie.serie(_bougies(4))
    assert all(isinstance(v, int) for v in bloc["v"])


def test_horodatage_intraday_en_secondes_utc():
    idx = pd.date_range("2026-01-05 14:30", periods=3, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3,
                       "close": [1.0] * 3, "volume": [0.0] * 3}, index=idx)
    bloc = serie.serie(df, quotidien=False, surcouches=False)
    assert bloc["t"] == [int(d.timestamp()) for d in idx]
    # l'écart de 300 s est ce qui permet à la bibliothèque de placer les
    # bougies : un horodatage en millisecondes les tasserait toutes ensemble
    assert bloc["t"][1] - bloc["t"][0] == 300


def test_serie_vide_ne_casse_pas():
    assert serie.serie(pd.DataFrame()) == {}
    assert serie.serie(None) == {}


def test_payload_sans_intraday_reste_utilisable():
    """Un titre sans barres fines garde sa série quotidienne.

    C'est le cas de la BRVM et de tout titre nouvellement suivi : l'absence
    d'intrajournalier retire une échelle de temps au graphique, elle ne doit
    pas retirer le graphique.
    """
    p = serie.payload("SYMBOLE-INEXISTANT-XYZ", _bougies(30))
    assert p["quotidien"]["n"] == 30
    assert "intraday" not in p


def test_tous_les_actifs_suivis_ont_un_nom():
    sans_nom = [s for s in config.SUIVIS if s not in config.NOMS_ACTIFS]
    assert not sans_nom, (
        "actifs suivis sans nom lisible (la fiche afficherait le ticker) : "
        f"{sans_nom}")


# --------------------------------------------------------------- relais PHP

php = shutil.which("php")


def _appeler_serie_php(params: dict) -> tuple[int, dict]:
    """Exécute serie.php hors serveur web et renvoie (code HTTP, JSON)."""
    # Le code HTTP est imprimé depuis une fonction d'arrêt : serie.php se
    # termine par `exit` sur les cas d'erreur, et rien de ce qui suit
    # l'`include` ne s'exécuterait.
    script = (
        "register_shutdown_function(function () {"
        "  fwrite(STDERR, (string) http_response_code()); });"
        "$_GET = json_decode(getenv('ML_GET'), true);"
        "include getenv('ML_SCRIPT');"
    )
    r = subprocess.run(
        [php, "-r", script],
        env={**__import__("os").environ,
             "ML_GET": json.dumps(params),
             "ML_SCRIPT": str(config.ROOT / "deploy" / "serie.php")},
        capture_output=True, text=True, timeout=60)
    return int(r.stderr.strip() or 200), json.loads(r.stdout or "{}")


@pytest.mark.skipif(not php, reason="php absent de cette machine")
def test_relais_refuse_un_symbole_inconnu():
    """Sans cette porte, serie.php serait un proxy ouvert."""
    code, corps = _appeler_serie_php({"s": "../../etc/passwd"})
    assert code == 404
    assert "erreur" in corps


@pytest.mark.skipif(not php, reason="php absent de cette machine")
def test_relais_refuse_un_pas_de_temps_hors_liste():
    code, corps = _appeler_serie_php({"s": "AAPL", "i": "1s"})
    assert code == 400
    assert corps["acceptes"] == ["5m", "15m", "1h"]
