#!/bin/bash
# Check that no served file on the Pi contains the station coordinates.
#
#   sudo ./check-served.sh
#
# Reads lat/lon from /etc/ground-station/location.env and greps every
# directory lighttpd serves on :8504. Prints only COUNTS, never the values.
#
#   leak      files with BOTH the lat and the lon as whole numbers (not part of
#             a longer number). Must be 0; the script fails otherwise.
#   lat-only / lon-only   files with just one of them, as a whole number.
#             Usually coincidence (a map boundary file, an aircraft position).
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
v="$(sed -n 's/^GS_LOCATION=//p' /etc/ground-station/location.env | tr -d "\"'")"
lat="${v%%,*}"; rest="${v#*,}"; lon="${rest%%,*}"
pat() { printf '(?<![0-9.])%s(?![0-9])' "$(printf '%s' "$1" | sed 's/[.]/\\./g; s/-/\\-/g')"; }
plat="$(pat "$lat")"; plon="$(pat "$lon")"
dirs="/usr/local/share/station-web/html /run/station-web /run/readsb /usr/local/share/tar1090/html
/usr/share/graphs1090/html /run/graphs1090 /run/tar1090 /usr/share/javascript /var/globe_history"
leak=0; latonly=0; lononly=0
for d in $dirs; do
    [ -e "$d" ] || continue
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        a=0; b=0
        grep -qP -- "$plat" "$f" 2>/dev/null && a=1
        grep -qP -- "$plon" "$f" 2>/dev/null && b=1
        if [ $a = 1 ] && [ $b = 1 ]; then leak=$((leak + 1)); echo "LEAK: $f"
        elif [ $a = 1 ]; then latonly=$((latonly + 1))
        elif [ $b = 1 ]; then lononly=$((lononly + 1)); fi
    done < <(grep -rlP -e "$plat" -e "$plon" "$d" 2>/dev/null)
done
echo "leak (lat and lon together): $leak matches"
echo "lat-only: $latonly, lon-only: $lononly (coincidental single values)"
[ "$leak" = 0 ]
