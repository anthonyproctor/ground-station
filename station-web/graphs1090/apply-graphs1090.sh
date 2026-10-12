#!/bin/bash
# Re-apply the Ground Station tweaks to graphs1090.
# graphs1090's installer replaces html/index.html on update, so run this
# again after any graphs1090 update (install.sh runs it too).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
html=/usr/share/graphs1090/html
cfg=/etc/default/graphs1090

install -m 644 "$here/station-graphs.css" "$here/station-graphs.js" "$html/"

if ! grep -q 'station-graphs.css' "$html/index.html"; then
    sed -i 's#</head>#\t\t<link rel="stylesheet" href="station-graphs.css">\n\t</head>#' "$html/index.html"
fi
if ! grep -q 'station-graphs.js' "$html/index.html"; then
    sed -i 's#</body>#\t\t<script src="station-graphs.js"></script>\n\t</body>#' "$html/index.html"
fi

# supported graphs1090 options: dark graphs, range in statute miles
changed=0
grep -q '^colorscheme=dark' "$cfg" || { sed -i 's/^colorscheme=.*/colorscheme=dark/' "$cfg"; changed=1; }
grep -q '^range=statute' "$cfg" || { sed -i 's/^range=.*/range=statute/' "$cfg"; changed=1; }
if [ "$changed" = 1 ] || ! grep -q 'bootstrap.custom.dark.css' "$html/index.html"; then
    systemctl restart graphs1090
fi
echo "graphs1090 tweaks applied"
