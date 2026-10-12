#!/bin/bash
# Set up the Pi's local ntfy server for Ground Station alerts.
#
#   sudo ./setup-ntfy.sh
#
# Expects the ntfy deb to be installed already (official apt repo,
# archive.ntfy.sh, armhf). Then it:
# - installs server.yml: 127.0.0.1:2586 only, deny-all, no web app
# - creates two accounts the first time it runs:
#     anthony  read-only on the alerts topic (the phone)
#     station  write-only on the alerts topic (the alert engine)
#   with random passwords, a token each, and a random topic name gs-<24>
# - writes them to /etc/ground-station/ntfy-credentials.txt (root, 600) and
#   the engine side to /etc/ground-station/alerts.conf.d/ntfy.conf (root, 600)
# - never prints a secret
#
# Running it again keeps the existing accounts, topic and tokens.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
command -v ntfy >/dev/null || { echo "install the ntfy deb first"; exit 1; }

creds=/etc/ground-station/ntfy-credentials.txt
engine=/etc/ground-station/alerts.conf.d/ntfy.conf
install -d -m 700 /etc/ground-station /etc/ground-station/alerts.conf.d

[ -f /etc/ntfy/server.yml.orig ] || cp -a /etc/ntfy/server.yml /etc/ntfy/server.yml.orig
# keep the public base-url + iOS upstream once they're set (they hold the
# station's host name, so they live only on the Pi, not in this repo).
# First time: NTFY_BASE_URL=https://<host> NTFY_UPSTREAM=https://ntfy.sh sudo -E ./setup-ntfy.sh
keep="$(grep -E '^(base-url|upstream-base-url):' /etc/ntfy/server.yml || true)"
install -m 644 "$here/server.yml" /etc/ntfy/server.yml
if [ -n "$keep" ]; then
    printf '%s\n' "$keep" >> /etc/ntfy/server.yml
else
    if [ -n "${NTFY_BASE_URL:-}" ]; then echo "base-url: \"$NTFY_BASE_URL\"" >> /etc/ntfy/server.yml; fi
    if [ -n "${NTFY_UPSTREAM:-}" ]; then echo "upstream-base-url: \"$NTFY_UPSTREAM\"" >> /etc/ntfy/server.yml; fi
fi
install -d -m 755 /etc/systemd/system/ntfy.service.d
install -m 644 "$here/ntfy-station.conf" /etc/systemd/system/ntfy.service.d/station.conf
install -d -o ntfy -g ntfy -m 700 /var/cache/ntfy /var/lib/ntfy
systemctl daemon-reload
systemctl enable ntfy >/dev/null 2>&1
systemctl restart ntfy
sleep 2
systemctl is-active --quiet ntfy || { echo "ntfy failed to start"; journalctl -u ntfy -n 20 --no-pager; exit 1; }

rand() { python3 -c "import secrets,string; a=string.ascii_letters+string.digits; print(''.join(secrets.choice(a) for _ in range($1)))"; }

if [ ! -s "$creds" ]; then
    umask 077
    topic="gs-$(rand 24)"
    pw_anthony="$(rand 28)"
    pw_station="$(rand 28)"
    NTFY_PASSWORD="$pw_anthony" ntfy user add --role=user anthony >/dev/null
    NTFY_PASSWORD="$pw_station" ntfy user add --role=user station >/dev/null
    ntfy access anthony "$topic" read-only >/dev/null
    ntfy access station "$topic" write-only >/dev/null
    tok_anthony="$(ntfy token add --label phone anthony 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -1)"
    tok_station="$(ntfy token add --label station-alerts station 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -1)"
    [ -n "$tok_anthony" ] && [ -n "$tok_station" ] || { echo "token creation failed"; exit 1; }
    chown -R ntfy:ntfy /var/lib/ntfy
    cat > "$creds" <<EOF
# Ground Station ntfy, created $(date '+%Y-%m-%d %H:%M %Z'). Root only, keep it that way.
# Read with:  sudo cat $creds
topic=$topic
username=anthony
password=$pw_anthony
token=$tok_anthony
# the engine's own account (write-only); you don't need these on the phone
station_user=station
station_password=$pw_station
station_token=$tok_station
EOF
    chmod 600 "$creds"
fi

topic="$(sed -n 's/^topic=//p' "$creds")"
# tap-to-open link: the public map on the same host as ntfy's base-url (Pi only)
base="$(sed -n 's/^base-url:[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' /etc/ntfy/server.yml)"
tok_station="$(sed -n 's/^station_token=//p' "$creds")"
umask 077
cat > "$engine" <<EOF
# written by setup-ntfy.sh; secrets, root only
[notify]
notifier = ntfy
ntfy_url = http://127.0.0.1:2586
ntfy_topic = $topic
ntfy_token = $tok_station
map_url = ${base:+$base/map}
EOF
chmod 600 "$engine"
echo "ntfy: active on 127.0.0.1:2586, deny-all. Credentials in $creds (root, 600)."
