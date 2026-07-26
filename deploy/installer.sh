#!/usr/bin/env bash
# Installation / mise à jour de MarketLab sur le serveur.
#
#   sudo bash installer.sh            # installation complète
#   sudo bash installer.sh --maj      # simple mise à jour (redémarre les services)
#
# Le code doit déjà être présent dans /opt/marketlab : il y est déposé par
# archive depuis le poste de travail (le dépôt est privé, on évite ainsi de
# placer le moindre secret d'accès sur ce serveur).
#
# Idempotent : peut être relancé sans risque.
set -euo pipefail

RACINE=/opt/marketlab
UTILISATEUR=marketlab
MAJ_SEULE=${1:-}

echo "==> Utilisateur de service"
if ! id "$UTILISATEUR" &>/dev/null; then
    useradd --system --home-dir "$RACINE" --shell /usr/sbin/nologin "$UTILISATEUR"
    echo "    créé"
else
    echo "    déjà présent"
fi

echo "==> Code source"
if [ ! -f "$RACINE/requirements.txt" ]; then
    echo "ERREUR : aucun code applicatif dans $RACINE." >&2
    echo "Déposer d'abord l'archive du projet." >&2
    exit 1
fi
echo "    présent"

echo "==> Environnement Python"
if [ ! -x "$RACINE/.venv/bin/python" ]; then
    python3 -m venv "$RACINE/.venv"
fi
# --no-cache-dir : la machine dispose de peu de RAM et de peu de marge disque
"$RACINE/.venv/bin/pip" install --quiet --no-cache-dir --upgrade pip
"$RACINE/.venv/bin/pip" install --quiet --no-cache-dir -r "$RACINE/requirements.txt"
echo "    dépendances à jour"

echo "==> Dossiers de données"
mkdir -p "$RACINE/data_local/logs" "$RACINE/.cache"
chown -R "$UTILISATEUR:$UTILISATEUR" "$RACINE"

echo "==> Services systemd"
cp "$RACINE/deploy/marketlab-api.service" /etc/systemd/system/
cp "$RACINE/deploy/marketlab-dashboard.service" /etc/systemd/system/
systemctl daemon-reload

if [ "$MAJ_SEULE" = "--maj" ]; then
    systemctl restart marketlab-api marketlab-dashboard
else
    systemctl enable --now marketlab-api marketlab-dashboard
fi
sleep 5

echo "==> État des services"
systemctl is-active marketlab-api marketlab-dashboard || true
echo
echo "Étapes restantes :"
echo "  1. htpasswd -c /etc/nginx/.marketlab_htpasswd <utilisateur>"
echo "  2. cp $RACINE/deploy/marketlab.nginx.conf /etc/nginx/sites-available/marketlab"
echo "     ln -sf /etc/nginx/sites-available/marketlab /etc/nginx/sites-enabled/marketlab"
echo "     nginx -t && systemctl reload nginx"
echo "  3. DNS : marketlab.gnlfconsult.com A -> IP publique de ce serveur"
echo "  4. certbot --nginx -d marketlab.gnlfconsult.com"
