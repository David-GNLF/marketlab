"""Résumé quotidien des verdicts, envoyé sur le canal de notification.

    python scripts/resume_quotidien.py [--dry-run]

Recalcule les verdicts du matin (pondérations apprises incluses) et envoie
une synthèse : favorables, défavorables, abstentions, et la conclusion du
mieux noté. Conçu pour tourner chaque matin à 7 h (heure Bénin) depuis
GitHub Actions — le canal est fourni par MARKETLAB_NTFY_TOPIC en CI.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketlab import config, decision, notify, publish


def construire_message() -> str:
    dossiers = [d for d in decision.verdicts(publish.TITRES_DETAILLES)
                if "erreur" not in d]
    favorables = sorted([d for d in dossiers if d["avis"] == "Favorable"],
                        key=lambda d: -d["note_globale"])
    defavorables = sorted([d for d in dossiers if d["avis"] == "Défavorable"],
                          key=lambda d: d["note_globale"])
    abstenir = [d for d in dossiers if d["avis"] == "S'abstenir"]
    neutres = [d for d in dossiers if d["avis"] == "Neutre"]

    def ligne(d):
        nom = config.NOMS_ACTIFS.get(d["symbole"], d["symbole"])
        taille = d.get("taille_multiplicateur")
        extra = f" (taille ×{taille})" if taille not in (None, 1.0) else ""
        return f"• {nom} : {d['note_globale']:+.0f}{extra}"

    import pandas as pd
    heure = (pd.Timestamp.now("UTC") + pd.Timedelta(hours=1)).strftime("%d/%m %H:%M")
    parties = [f"☀️ <b>MarketLab — verdicts du matin ({heure}, Bénin)</b>",
               f"{len(dossiers)} actifs analysés, pondérations apprises"]
    if favorables:
        parties.append(f"\n🟢 <b>Favorable</b> ({len(favorables)})")
        parties += [ligne(d) for d in favorables]
    else:
        parties.append("\n🟢 Favorable : aucun aujourd'hui")
    if defavorables:
        parties.append(f"\n🔴 <b>Défavorable</b> ({len(defavorables)})")
        parties += [ligne(d) for d in defavorables]
        parties.append("  (rappel du bilan : historiquement suivis de hausses "
                       "— abstention plutôt que vente)")
    if abstenir:
        parties.append("\n⛔ S'abstenir : "
                       + ", ".join(d["symbole"] for d in abstenir))
    parties.append(f"⚪ Neutre : {len(neutres)}")

    if favorables and favorables[0].get("conclusion"):
        tete = favorables[0]
        parties.append(f"\n🧭 <b>{tete['symbole']}</b> — "
                       + tete["conclusion"]["texte"].split(" ; ")[0] + ".")
    parties.append("\nDétails : marketlab.gnlfconsult.com — aide à la "
                   "décision, pas un conseil en investissement.")
    return "\n".join(parties)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    message = construire_message()
    print(notify.html_vers_texte(message))
    if args.dry_run:
        print("\n(dry-run : rien envoyé)")
        return 0
    ok = notify.envoyer(message)
    print("\nenvoi :", "OK" if ok else "ECHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
