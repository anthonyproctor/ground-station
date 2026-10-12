#!/bin/bash
# Install the Ground Station web front end on the Pi.
#
#   sudo ./install.sh
#
# - home page at "/" on :8504, tar1090 moved to "/map/", graphs at "/graphs1090/"
# - tar1090 config.js (dark, Denver city center, no receiver marker or rings)
# - graphs1090 captions, dark graphs, mobile layout
# - station-stats.timer: writes /run/station-web/station.json once a minute
#
# Backs up everything it replaces to /var/backups/station-web-<time>/, tests
# the lighttpd config, and rolls back if the test or restart fails.
#
# Optional, private: /etc/ground-station/location.env (root, mode 600) with
# GS_LOCATION="lat,lon,meters" lets station-stats compute today's max range.
# It is never served and never goes in this repo.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"

bk=/var/backups/station-web-$(date +%Y%m%d-%H%M%S)
mkdir -p "$bk"
cp -a /etc/lighttpd "$bk/lighttpd"
cp -a /usr/local/share/tar1090/html/config.js "$bk/tar1090-config.js"
cp -a /usr/share/graphs1090/html/index.html "$bk/graphs1090-index.html"
cp -a /etc/default/graphs1090 "$bk/graphs1090.default"
echo "backup: $bk"

# home page
install -d -m 755 /usr/local/share/station-web/html
install -m 644 "$here/html/index.html" /usr/local/share/station-web/html/index.html

# tar1090 settings + nav buttons
install -m 644 "$here/tar1090/config.js" /usr/local/share/tar1090/html/config.js

# stats timer
install -d -m 755 /usr/local/lib/station-web
install -m 755 "$here/bin/station-stats.py" /usr/local/lib/station-web/station-stats.py
install -m 644 "$here/systemd/station-stats.service" "$here/systemd/station-stats.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now station-stats.timer
systemctl start station-stats.service || true

# graphs1090
"$here/graphs1090/apply-graphs1090.sh"

# lighttpd: home at "/", map at "/map/"
install -m 644 "$here/lighttpd/96-station-8504.conf" /etc/lighttpd/conf-available/96-station-8504.conf
ln -sf /etc/lighttpd/conf-available/96-station-8504.conf /etc/lighttpd/conf-enabled/96-station-8504.conf

rollback() {
    echo "lighttpd failed, rolling back to $bk"
    rm -rf /etc/lighttpd
    cp -a "$bk/lighttpd" /etc/lighttpd
    systemctl restart lighttpd
    exit 1
}
lighttpd -tt -f /etc/lighttpd/lighttpd.conf || rollback
systemctl restart lighttpd || rollback
sleep 1
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8504/map/) || true
[ "$code" = 200 ] || rollback
echo "installed: http://<pi>:8504/  (map at /map/, graphs at /graphs1090/)"
