"""Configuration assistée de la clé d'API FRED.

    .venv\\Scripts\\python scripts\\configurer_fred.py

Écrit `fred_api_key` dans `data_local/providers.json` (jamais versionné) à côté
d'une éventuelle clé Twelve Data, puis vérifie immédiatement que la clé est
acceptée par l'API.

SAISIE MASQUÉE, et ce n'est pas une coquetterie. Le dépôt MarketLab est PUBLIC.
Une clé tapée en clair se retrouve dans l'historique du terminal, et une clé
passée dans une URL se retrouve en plus dans l'historique du navigateur et les
journaux du serveur. Ici elle ne transite par aucun des deux.

À QUOI SERT CETTE CLÉ. Sans elle, le CSV public de FRED ne sert que la DERNIÈRE
version d'une série. Avec elle, l'API donne les millésimes ALFRED : le chiffre
exactement tel qu'il a été publié le jour J, avant révision — ce qui est le seul
chiffre ayant fait bouger le marché, donc le seul dont la surprise ait un sens.

Obtenir une clé (gratuite, immédiate) : https://fredaccount.stlouisfed.org/apikeys
"""

import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from marketlab.data import fred

# 32 caractères alphanumériques minuscules — le format annoncé par FRED.
LONGUEUR_CLE = 32
TEST_URL = "https://api.stlouisfed.org/fred/series"
TEST_SERIE = "CPIAUCSL"


def main() -> int:
    print("=== Clé d'API FRED pour MarketLab ===\n")
    chemin = fred.CONFIG_PATH
    config = {}
    if chemin.exists():
        try:
            config = json.loads(chemin.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    if config.get("fred_api_key"):
        print("Une clé FRED est déjà enregistrée.")
        if input("La remplacer ? (o/N) : ").strip().lower() != "o":
            print("Conservée.")
            return 0

    print("Obtenir une clé : https://fredaccount.stlouisfed.org/apikeys")
    print("(gratuite, immédiate, 32 caractères)\n")
    cle = getpass.getpass("Clé FRED (saisie masquée) : ").strip()
    if not cle:
        print("Clé vide — abandon.")
        return 1
    if len(cle) != LONGUEUR_CLE or not cle.isalnum() or cle != cle.lower():
        print(f"Format inattendu ({len(cle)} caractères) : FRED attend "
              f"{LONGUEUR_CLE} caractères alphanumériques minuscules.")
        if input("Continuer quand même ? (o/N) : ").strip().lower() != "o":
            return 1

    print("\nVérification auprès de FRED…")
    try:
        resp = requests.get(TEST_URL, timeout=20, params={
            "series_id": TEST_SERIE, "api_key": cle, "file_type": "json"})
    except Exception as exc:
        print(f"ÉCHEC réseau : {str(exc)[:100]}")
        return 1
    if resp.status_code == 400:
        print("REFUSÉE : FRED répond « api_key invalide ». Vérifier la clé "
              "sur https://fredaccount.stlouisfed.org/apikeys")
        return 1
    if resp.status_code != 200:
        print(f"Réponse inattendue de FRED : HTTP {resp.status_code}")
        return 1

    config["fred_api_key"] = cle
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(config, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    print(f"  clé acceptée (série témoin {TEST_SERIE} lisible)")
    print(f"  écrite dans {chemin}  (ignoré par git)")
    print("\nPour que GitHub Actions y ait accès, ajouter un secret de dépôt :")
    print("  gh secret set MARKETLAB_FRED_API_KEY")
    print("(la commande demande la valeur sans l'afficher ; ne jamais la "
          "passer en argument, elle resterait dans l'historique du shell)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
