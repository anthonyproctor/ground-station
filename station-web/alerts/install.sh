#!/bin/bash
# Install the Ground Station alert engine + GPS-interference monitor on the Pi.
#
#   sudo ./install.sh
#
# - /usr/local/lib/station-web/station-alerts.py, run by station-alerts.service
# - /etc/ground-station/alerts.conf (root, 600) from alerts.conf.example, only
#   if it doesn't exist yet (your edits are kept)
# - the home page (../html/index.html) with the Alerts panel and GPS tile
#
# Private output stays in /var/lib/ground-station (0700, never served). The
# only public file is /run/station-web/alerts.json.
# Notifications stay off until alerts.conf(.d) picks a notifier; for the local
# ntfy server run ../ntfy/setup-ntfy.sh.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"

install -d -m 755 /usr/local/lib/station-web
install -m 755 "$here/station-alerts.py" /usr/local/lib/station-web/station-alerts.py
install -d -m 700 /etc/ground-station
[ -f /etc/ground-station/alerts.conf ] || install -m 600 "$here/alerts.conf.example" /etc/ground-station/alerts.conf
install -m 644 "$here/station-alerts.service" /etc/systemd/system/station-alerts.service

# home page (same copy step as ../install.sh, no lighttpd change needed)
install -d -m 755 /usr/local/share/station-web/html
[ -f /usr/local/share/station-web/html/index.html ] && \
    cp -a /usr/local/share/station-web/html/index.html "/var/backups/station-web-index-$(date +%Y%m%d-%H%M%S).html"
install -m 644 "$here/../html/index.html" /usr/local/share/station-web/html/index.html

systemctl daemon-reload
systemctl enable station-alerts.service >/dev/null 2>&1
systemctl restart station-alerts.service
sleep 12
systemctl is-active --quiet station-alerts || { journalctl -u station-alerts -n 30 --no-pager; exit 1; }
[ -s /run/station-web/alerts.json ] || { echo "alerts.json not written yet"; exit 1; }
echo "installed: station-alerts active, /station-data/alerts.json live"
