"""Fil des alertes récentes, publié sur le site.

Le passage horaire des alertes pousse ce qu'il vient d'envoyer dans
`donnees/alertes_recentes.json` sur l'hébergement (via FTPS, comme le reste).
Même sans alerte, l'heure du dernier passage est mise à jour : la page du
site peut ainsi dire « le scanner est passé à HH:MM, rien à signaler » —
le silence redevient une information.

Le fichier vit UNIQUEMENT sur l'hébergement : la publication quotidienne du
site ne le régénère pas et ne l'écrase pas (le transfert n'efface jamais les
fichiers distants absents du dossier local).
"""

import io
import json

import pandas as pd

from marketlab import ftps

CHEMIN_DISTANT = "donnees/alertes_recentes.json"
MAX_ENTREES = 120


def publier(nouvelles: list[tuple[str, bool]]) -> dict:
    """Ajoute les alertes envoyées au fil distant et horodate le passage.

    `nouvelles` : couples (texte brut, urgent). Peut être vide — le passage
    est tout de même horodaté.
    """
    cfg = ftps.charger_config()
    session = ftps._connecter(cfg)
    base = cfg["dossier_distant"].rstrip("/")
    try:
        try:
            tampon = io.BytesIO()
            session.retrbinary(f"RETR {base}/{CHEMIN_DISTANT}", tampon.write)
            fil = json.loads(tampon.getvalue().decode("utf-8"))
        except Exception:
            fil = {"alertes": []}

        maintenant = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
        fil["dernier_passage"] = maintenant
        fil["fuseau"] = "heure du serveur d'alertes (UTC)"
        for texte, urgent in nouvelles:
            fil["alertes"].insert(0, {"quand": maintenant, "texte": texte,
                                      "urgent": bool(urgent)})
        fil["alertes"] = fil["alertes"][:MAX_ENTREES]

        contenu = json.dumps(fil, ensure_ascii=False).encode("utf-8")
        session.storbinary(f"STOR {base}/{CHEMIN_DISTANT}",
                           io.BytesIO(contenu))
        return {"publiees": len(nouvelles), "total_fil": len(fil["alertes"])}
    finally:
        try:
            session.quit()
        except Exception:
            session.close()
