#!/bin/bash
# Pre-commit privacy check for this repo (run from anywhere in it):
#
#   station-web/alerts/check-repo.sh
#
# Fails if .env.local isn't ignored, or if any file git would commit (tracked
# or untracked-not-ignored) contains the station lat/lon from .env.local, an
# ntfy token (tk_...), a filled-in password/token line, or a private file.
# Prints counts and file names only, never the values.
set -uo pipefail
root="$(git rev-parse --show-toplevel)"; cd "$root"
fail=0
git check-ignore -q .env.local || { echo "FAIL: .env.local is not gitignored"; fail=1; }
v="$(sed -n 's/^GS_LOCATION=//p' .env.local 2>/dev/null | tr -d "\"'")"
files="$(git ls-files -co --exclude-standard)"
if [ -n "$v" ]; then
    lat="${v%%,*}"; rest="${v#*,}"; lon="${rest%%,*}"
    n=0
    for x in "$lat" "$lon"; do
        p="(?<![0-9.])$(printf '%s' "$x" | sed 's/[.]/\\./g; s/-/\\-/g')(?![0-9])"
        hits="$(printf '%s\n' "$files" | xargs grep -lP -- "$p" 2>/dev/null)"
        [ -n "$hits" ] && { echo "FAIL: station coordinate found in: $hits"; n=$((n + 1)); }
    done
    echo "coordinates: $n matches"; [ $n = 0 ] || fail=1
else
    echo "WARN: no GS_LOCATION in .env.local, coordinate check skipped"
fi
sec="$(printf '%s\n' "$files" | xargs grep -lE 'tk_[A-Za-z0-9]{20,}|^[[:space:]]*(password|token|ntfy_token|pushover_token|smtp_password)[[:space:]]*=[[:space:]]*[^[:space:]$]' 2>/dev/null)"
[ -n "$sec" ] && { echo "FAIL: secret-looking value in: $sec"; fail=1; } || echo "secrets: 0 matches"
priv="$(printf '%s\n' "$files" | grep -E '(^|/)(location\.env|alerts\.conf|ntfy-credentials[^/]*|alerts-state\.json|[^/]*\.jsonl)$')"
[ -n "$priv" ] && { echo "FAIL: private file would be committed: $priv"; fail=1; } || echo "private files: 0"
exit $fail
