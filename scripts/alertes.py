"""CLI des alertes MarketLab.

Exemples :
    python scripts/alertes.py --dry-run          # évalue et affiche sans envoyer
    python scripts/alertes.py --test             # envoie un message de test Telegram
    python scripts/alertes.py                    # évalue et envoie sur Telegram
    python scripts/alertes.py --univers Crypto Forex

Planification (toutes les heures, Planificateur de tâches Windows) :
    schtasks /Create /SC HOURLY /TN "MarketLab alertes" ^
      /TR "python C:\\Users\\Dav\\Downloads\\PROJET\\claude\\marketlab\\scripts\\alertes.py"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlab import alerts


def main() -> int:
    parser = argparse.ArgumentParser(description="Alertes MarketLab → Telegram")
    parser.add_argument("--dry-run", action="store_true",
                        help="afficher les alertes sans les envoyer")
    parser.add_argument("--test", action="store_true",
                        help="envoyer un message de test sur les canaux actifs")
    parser.add_argument("--univers", nargs="*", default=None,
                        help="univers à surveiller (défaut : US, EU, Forex, Crypto)")
    args = parser.parse_args()

    if args.test:
        if not alerts.est_configure():
            print("Aucun canal de notification configuré.\n"
                  "  Lancer : python scripts\\configurer_alertes.py")
            return 1
        ok = alerts.envoyer_message(
            "✅ <b>MarketLab</b> : canal de notification opérationnel.")
        print("Message de test envoyé." if ok else "ECHEC de l'envoi (voir la config).")
        return 0 if ok else 1

    bilan = alerts.run(universes=args.univers, dry_run=args.dry_run)
    print(f"Alertes générées : {bilan['alertes']} | envoyées : {bilan['envoyees']} | "
          f"canaux : {', '.join(bilan['canaux']) or 'aucun'} | dry-run : {bilan['dry_run']}")
    if not bilan["configure"] and not args.dry_run:
        print("(aucun canal configuré : les alertes ont été affichées et RESTENT "
              "en attente ; lancer python scripts\\configurer_alertes.py)")

    # fil des alertes récentes sur le site : chaque passage réel horodate le
    # fil et y verse ce qui vient d'être envoyé. Un échec ici ne doit jamais
    # compromettre la livraison des alertes elle-même.
    if not args.dry_run:
        try:
            from marketlab import fil_alertes
            r = fil_alertes.publier(bilan.get("messages_envoyes", []))
            print(f"Fil du site : {r['publiees']} publiée(s), "
                  f"{r['total_fil']} au fil.")
        except Exception as exc:
            print(f"Fil du site indisponible (non bloquant) : {str(exc)[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
