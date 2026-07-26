#!/usr/bin/env bash
# Installation / mise à jour de MarketLab sur le serveur.
#
#   sudo bash installer.sh            # installation complète
#   sudo bash installer.sh --maj      # simple mise à jour du code
#
# Idempotent : peut être relancé sans risque.
set -euo pipefail

RACINE=/opt/marketlab
DEPOT=https://github.com/David-GNLF/marketlab.git
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
if [ -d "$RACINE/.git" ]; then
    git -C "$RACINE" fetch --quiet origin
    git -C "$RACINE" reset --hard --quiet origin/master
    echo "    mis à jour : $(git -C "$RACINE" log --oneline -1)"
else
    apt-get install -y --no-install-recommends git python3-venv >/dev/null
    git clone --quiet "$DEPOT" "$RACINE"
    echo "    cloné"
fi

echo "==> Environnement Python"
if [ ! -x "$RACINE/.venv/bin/python" ]; then
    python3 -m venv "$RACINE/.venv"
fi
# --no-cache-dir : la machine a peu de RAM et peu de marge disque
"$RACINE/.venv/bin/pip" install --quiet --no-cache-dir --upgrade pip
"$RACINE/.venv/bin/pip" install --quiet --no-cache-dir -r "$RACINE/requirements.txt"
echo "    dépendances à jour"

echo "==> Dossiers de données"
mkdir -p "$RACINE/data_local/logs" "$RACINE/.cache"
chown -R "$UTILISATEUR:$UTILISATEUR" "$RACINE"

if [ "$MAJ_SEULE" = "--maj" ]; then
    echo "==> Redémarrage des services"
    systemctl restart marketlab-api marketlab-dashboard
    sleep 3
    systemctl is-active marketlab-api marketlab-dashboard
    echo "Mise à jour terminée."
    exit 0
fi

echo "==> Services systemd"
cp "$RACINE/deploy/marketlab-api.service" /etc/systemd/system/
cp "$RACINE/deploy/marketlab-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now marketlab-api marketlab-dashboard
sleep 5

echo "==> État"
systemctl is-active marketlab-api marketlab-dashboard || true
echo
echo "Étapes restantes (manuelles) :"
echo "  1. htpasswd -c /etc/nginx/.marketlab_htpasswd <utilisateur>"
echo "  2. cp $RACINE/deploy/marketlab.nginx.conf /etc/nginx/sites-available/marketlab"
echo "     ln -sf /etc/nginx/sites-available/marketlab /etc/nginx/sites-enabled/marketlab"
echo "     nginx -t && systemctl reload nginx"
echo "  3. DNS : marketlab.gnlfconsult.com A -> IP publique de ce serveur"
echo "  4. certbot --nginx -d marketlab.gnlfconsult.com"
