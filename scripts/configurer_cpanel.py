"""Configuration assistée de la publication FTPS vers cPanel.

    .venv\\Scripts\\python scripts\\configurer_cpanel.py

Écrit `data_local/cpanel.json` (jamais versionné) et vérifie immédiatement la
connexion. Le mot de passe est saisi en mode masqué : il n'apparaît ni à
l'écran, ni dans l'historique du terminal.

Si les identifiants existent déjà dans les secrets GitHub d'un autre projet,
ils n'y sont pas relisibles — GitHub ne restitue jamais la valeur d'un secret.
Les retrouver dans cPanel : *Fichiers → Comptes FTP*, où le mot de passe peut
être redéfini sans perdre le compte.
"""

import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlab import ftps


def _demander(question: str, defaut: str = "") -> str:
    reponse = input(f"{question}{f' [{defaut}]' if defaut else ''} : ").strip()
    return reponse or defaut


def main() -> int:
    print("=== Publication MarketLab vers cPanel (FTPS) ===\n")
    if ftps.CONFIG_PATH.exists():
        try:
            actuel = json.loads(ftps.CONFIG_PATH.read_text(encoding="utf-8"))
            print(f"Configuration existante : {actuel.get('utilisateur')} @ "
                  f"{actuel.get('hote')} -> {actuel.get('dossier_distant')}")
            if _demander("La remplacer ? (o/N)", "n").lower() != "o":
                print("Conservée.")
                return 0
        except Exception:
            pass

    hote = _demander("Hôte FTP", "cloud740.thundercloud.uk")
    utilisateur = _demander("Utilisateur FTP (compte dédié, pas le principal)")
    if not utilisateur:
        print("Utilisateur obligatoire — abandon.")
        return 1
    mot_de_passe = getpass.getpass("Mot de passe (saisie masquée) : ").strip()
    if not mot_de_passe:
        print("Mot de passe obligatoire — abandon.")
        return 1
    dossier = _demander("Dossier distant", "/public_html/marketlab")
    port = int(_demander("Port", "21"))

    ftps.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ftps.CONFIG_PATH.write_text(json.dumps({
        "hote": hote, "port": port, "utilisateur": utilisateur,
        "mot_de_passe": mot_de_passe, "dossier_distant": dossier,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nÉcrit : {ftps.CONFIG_PATH}")

    print("Test de connexion…")
    try:
        bilan = ftps.tester_connexion()
    except Exception as exc:
        print(f"ÉCHEC : {exc}")
        print("\nPistes : compte FTP inexistant, mot de passe erroné, ou "
              "dossier distant incorrect (vérifier dans cPanel → Comptes FTP).")
        return 1

    print(f"  connecté à {bilan['hote']}")
    print(f"  dossier {bilan['dossier_distant']} accessible "
          f"({bilan['elements_presents']} élément(s) déjà présents)")
    print("\nPublier maintenant avec :")
    print("  .venv\\Scripts\\python scripts\\publier.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
