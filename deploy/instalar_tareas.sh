#!/usr/bin/env bash
# Instala los temporizadores de monitoreo, respaldo y purga del SGR.
# Uso (en la EC2):  sudo bash /srv/sgr/app/deploy/instalar_tareas.sh
set -euo pipefail

ORIGEN="$(cd "$(dirname "$0")" && pwd)/systemd"

# Directorios privados para respaldos y estado del monitor.
install -d -o sgr -g sgr -m 700 /var/backups/sgr
install -d -o sgr -g sgr -m 700 /var/lib/sgr

install -m 644 "$ORIGEN"/sgr-*.service "$ORIGEN"/sgr-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sgr-monitor.timer sgr-respaldo.timer sgr-purga.timer sgr-coherencia.timer

echo "Temporizadores instalados:"
systemctl list-timers 'sgr-*' --no-pager
