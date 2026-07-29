"""CLI des alertes MarketLab.

Exemples :
    python scripts/alertes.py --dry-run          # évalue et affiche sans envoyer
    python scripts/alertes.py --test             # message de test sur les canaux
    python scripts/alertes.py                    # un passage, puis on sort
    python scripts/alertes.py --univers Crypto Forex
    python scripts/alertes.py --boucle           # balaie pendant 55 min

POURQUOI LE MODE BOUCLE. Les exécutions planifiées gratuites de GitHub sont
au mieux-effort : mesuré sur 24 h, un cron horaire n'a donné que 10 passages
au lieu de 24, et tripler les crons n'a rien changé (7 passages en 15 h alors
que 45 étaient programmés — environ 15 % d'honorés). Insister sur la
planification était un cul-de-sac.

Le mode boucle renverse le problème : au lieu d'espérer beaucoup de
déclenchements courts, on tire parti de CHAQUE déclenchement obtenu en
balayant plusieurs fois pendant qu'on tient le processus. Un passage honoré
couvre alors une heure entière avec une granularité de quelques minutes, au
lieu d'un instantané unique.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlab import alerts

DUREE_DEFAUT_MIN = 55       # un peu moins qu'une heure : on laisse la main
INTERVALLE_DEFAUT_MIN = 8


def un_passage(universes=None, dry_run=False) -> dict:
    """Un balayage complet : règles, envoi, puis publication du fil."""
    bilan = alerts.run(universes=universes, dry_run=dry_run)
    print(f"Alertes générées : {bilan['alertes']} | envoyées : {bilan['envoyees']} | "
          f"canaux : {', '.join(bilan['canaux']) or 'aucun'} | dry-run : {bilan['dry_run']}")
    if not bilan["configure"] and not dry_run:
        print("(aucun canal configuré : les alertes ont été affichées et RESTENT "
              "en attente ; lancer python scripts\\configurer_alertes.py)")

    # Le fil du site est horodaté à CHAQUE passage, même sans alerte : c'est
    # la seule preuve visible que le scanner tourne, et le mode boucle la
    # rafraîchit désormais plusieurs fois par heure.
    if not dry_run:
        try:
            from marketlab import fil_alertes
            r = fil_alertes.publier(bilan.get("messages_envoyes", []))
            print(f"Fil du site : {r['publiees']} publiée(s), "
                  f"{r['total_fil']} au fil.")
        except Exception as exc:
            print(f"Fil du site indisponible (non bloquant) : {str(exc)[:80]}")
    return bilan


def boucler(duree_minutes: int, intervalle_minutes: int, universes=None,
            dry_run: bool = False, horloge=time.monotonic, dormir=time.sleep,
            passage=un_passage) -> dict:
    """Balaie en boucle pendant `duree_minutes`, toutes `intervalle_minutes`.

    Le premier passage a lieu immédiatement. Un passage qui échoue n'arrête
    pas la boucle : sur une heure, mieux vaut cinq balayages réussis et un
    raté qu'un arrêt complet au premier incident réseau.

    Renvoie le bilan cumulé.
    """
    debut = horloge()
    fin = debut + duree_minutes * 60
    cumul = {"passages": 0, "echecs": 0, "alertes": 0, "envoyees": 0}
    while True:
        depart_passage = horloge()
        cumul["passages"] += 1
        reste = max(0, int((fin - depart_passage) / 60))
        print(f"\n--- passage {cumul['passages']} "
              f"(encore {reste} min de veille) ---", flush=True)
        try:
            bilan = passage(universes=universes, dry_run=dry_run)
            cumul["alertes"] += bilan.get("alertes", 0)
            cumul["envoyees"] += bilan.get("envoyees", 0)
        except Exception as exc:
            cumul["echecs"] += 1
            print(f"passage en échec (la veille continue) : {str(exc)[:120]}",
                  flush=True)

        # L'intervalle se compte depuis le DÉBUT du passage, pas depuis sa
        # fin : un balayage complet prend plusieurs minutes, et compter depuis
        # la fin ajoutait cette durée au délai, réduisant d'autant le nombre
        # de passages tenus dans l'heure. Si un balayage a débordé
        # l'intervalle, le suivant démarre sans attendre.
        prochain = depart_passage + intervalle_minutes * 60
        if prochain >= fin:
            break
        dormir(max(0, prochain - horloge()))
    print(f"\n=== veille terminée : {cumul['passages']} passage(s), "
          f"{cumul['echecs']} en échec, {cumul['alertes']} alerte(s) générée(s), "
          f"{cumul['envoyees']} envoyée(s) ===")
    return cumul


def main() -> int:
    parser = argparse.ArgumentParser(description="Alertes MarketLab")
    parser.add_argument("--dry-run", action="store_true",
                        help="afficher les alertes sans les envoyer")
    parser.add_argument("--test", action="store_true",
                        help="envoyer un message de test sur les canaux actifs")
    parser.add_argument("--univers", nargs="*", default=None,
                        help="univers à surveiller (défaut : US, EU, Forex, Crypto)")
    parser.add_argument("--boucle", action="store_true",
                        help="balayer en continu au lieu d'un passage unique")
    parser.add_argument("--duree-minutes", type=int, default=DUREE_DEFAUT_MIN,
                        help=f"durée de la veille en mode boucle "
                             f"(défaut {DUREE_DEFAUT_MIN})")
    parser.add_argument("--intervalle-minutes", type=int,
                        default=INTERVALLE_DEFAUT_MIN,
                        help=f"délai entre deux balayages "
                             f"(défaut {INTERVALLE_DEFAUT_MIN})")
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

    if args.boucle:
        boucler(args.duree_minutes, args.intervalle_minutes,
                universes=args.univers, dry_run=args.dry_run)
    else:
        un_passage(universes=args.univers, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
