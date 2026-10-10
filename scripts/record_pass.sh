#!/usr/bin/env bash
# Record one Meteor-M2-4 LRPT pass as raw 8-bit IQ, to be decoded later in SatDump (on the Mac).
# usage: record_pass.sh <seconds> [label]
# Env: GS_SAT_SERIAL (rtl-sdr serial of the satellite dongle), GS_FREQ (default 137.9e6), GS_GAIN (default 40)
set -euo pipefail
secs=${1:?seconds}; label=${2:-pass}
out=${GS_OUT:-$HOME/passes}; mkdir -p "$out"
f=${GS_FREQ:-137.9e6}; rate=1024000; gain=${GS_GAIN:-40}
dev=${GS_SAT_SERIAL:+-d $GS_SAT_SERIAL}
file="$out/$(date -u +%Y%m%dT%H%M%SZ)_${label}_${f}_${rate}.u8"
command -v rtl_biast >/dev/null && rtl_biast ${GS_SAT_SERIAL:+-d $GS_SAT_SERIAL} -b 1 || true   # power the SAWbird LNA
timeout "$secs" rtl_sdr $dev -f "$f" -s "$rate" -g "$gain" "$file" || true
command -v rtl_biast >/dev/null && rtl_biast ${GS_SAT_SERIAL:+-d $GS_SAT_SERIAL} -b 0 || true
ls -lh "$file"
