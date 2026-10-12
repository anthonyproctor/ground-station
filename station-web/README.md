# station-web

The public front end for the Pi 3 ADS-B station, served by lighttpd on port 8504.

| Path | What |
|---|---|
| `/` | Station home page (`html/index.html`): live counts, best of today, Denver weather |
| `/map/` | tar1090 live map. Old `/?...` links redirect to `/map/?...` |
| `/graphs1090/` | graphs1090 with friendlier captions and a dark, mobile layout |
| `/station-data/station.json` | Minute stats written by `station-stats.timer` |
| `/station-data/alerts.json` | Public alerts + GPS health, written by `station-alerts.service` |

Install or re-apply on the Pi:

```
rsync -a station-web/ pi:/tmp/station-web/ && ssh pi 'sudo /tmp/station-web/install.sh'
```

`install.sh` backs up what it replaces to `/var/backups/station-web-<time>/`, runs `lighttpd -tt`, and rolls the lighttpd config back if the test, the restart, or a `/map/` check fails.

## After updates

- **tar1090 update:** nothing to do. tar1090 keeps `config.js`, and `96-station-8504.conf` overrides the `95-tar1090-otherport.conf` it rewrites.
- **graphs1090 update:** it replaces `index.html`, so run `sudo graphs1090/apply-graphs1090.sh` (or `install.sh` again).

## Privacy

The map is public, so the receiver location is never published:
- readsb has no receiver lat/lon, so `aircraft.json` carries no distance or bearing fields.
- tar1090 shows no site marker, rings, or range outline. The default center is downtown Denver.
- `station-stats.py` can read an optional root-only `/etc/ground-station/location.env` (`GS_LOCATION="lat,lon,meters"`, the same format the pass scripts use). It uses it on the Pi only, to compute one number: today's max range in miles. That file is never served and never committed.

## Weather

- Map: NEXRAD radar (Iowa State Mesonet WMS) is on by default. RainViewer radar and NOAA radar and satellite are in the layer switcher.
- Home page: Denver International (KDEN) METAR from aviationweather.gov. Their API sends no CORS header, so the Pi fetches it at most once every 10 minutes and passes a small subset through `station.json`.

## Alerts and GPS health (`alerts/`)

`station-alerts.service` is a small Python service (stdlib only, nice 15, 20% CPU quota, 100 MB cap). Every 8 s it reads `aircraft.json` and raises:

| Kind | Rule (all thresholds in `/etc/ground-station/alerts.conf`) | Public? |
|---|---|---|
| emergency | squawk 7500 / 7600 / 7700, or readsb `emergency` (not `lifeguard`), seen on 2 polls in a row | yes |
| military | military bit in the tar1090 db flags (or readsb `dbFlags`) | yes |
| rare | type with 25 or fewer airframes worldwide (the same type counts as "Rarest type"), once per type | yes |
| high | above 50,000 ft | yes |
| low_home | within 2 nmi of the station and under 2,000 ft above ground | **no, private log + phone only** |
| lookup | military, rare, helicopter or heavy (configurable list) within 5 nmi, under 10,000 ft above ground, in daylight (sun at or above -6°, computed on the Pi), closing or at closest approach. It says which way to look, how high, how far, and minutes to closest approach. Once per aircraft per 2 h | **no, phone only** |
| gps | 3+ aircraft (and 25%+ of those checked) lose GPS integrity in 5 min | yes, "Denver area" only |

One alert per aircraft per kind per 4 h. GPS: an aircraft counts as degraded when readsb reports `gpsOkBefore`, or when its NACp drops below 8 or NIC below 7 **after** it was seen good, so aircraft with permanently poor GPS can't fake an event.

Files:
- `/var/lib/ground-station/alerts.jsonl`: every alert, private (directory 0700, never served)
- `/var/lib/ground-station/gps-health.jsonl`: one point a minute (aircraft checked, degraded) for graphs
- `/run/station-web/alerts.json`: public subset for the home page. It's built from a field whitelist: no positions, distances, bearings or heights above ground.

Install or update: `rsync -a station-web/ pi:/tmp/station-web/ && ssh pi 'sudo /tmp/station-web/alerts/install.sh'`. It keeps an existing `alerts.conf`.

Tests use synthetic fixtures only: `python3 -m unittest discover -s station-web/alerts/tests -v`.

Privacy checks:
- `station-web/alerts/check-repo.sh`: run before every commit. Fails if `.env.local` isn't ignored, or if anything git would commit holds the station lat/lon, a token or password, or a private file.
- `sudo station-web/alerts/check-served.sh` on the Pi: greps every served directory for the lat/lon from `location.env` and prints counts only.

## Phone notifications (`ntfy/`)

The ntfy server ([official apt repo](https://docs.ntfy.sh/install/), armhf) runs on the Pi, listening on `127.0.0.1:2586` only, with `auth-default-access: deny-all`, no sign-up and no web app. `sudo ntfy/setup-ntfy.sh` creates:
- `anthony`: read-only on the alerts topic (the phone)
- `station`: write-only on the alerts topic (the engine)
- the topic, a random `gs-` name

The passwords, tokens and topic go to `/etc/ground-station/ntfy-credentials.txt` (root, 600), and the engine's side goes to `/etc/ground-station/alerts.conf.d/ntfy.conf` (root, 600). Nothing secret is printed or committed.

The engine sends emergency, military, rare, GPS and the private low-over-the-house and look-up alerts. `high` is off (Anthony: not useful on the phone). Look-ups count toward the hourly cap; emergencies never do. Limits are one per aircraft per kind per 4 h, and at most 6 an hour overall. Emergencies skip the hourly cap, and the next sent message says how many were held back. The service can only open sockets to localhost.

**Phone path (approved and live 2026-10-12):** ntfy can't run on a sub-path, so `97-station-ntfy.conf` proxies only ntfy's paths on `:8504`, `/v1/` and `/gs-...` topics, to `127.0.0.1:2586`. The existing Funnel (`/` to `:8504`) carries them, so no Funnel change was needed. `base-url` is the station's public root and `upstream-base-url` is `https://ntfy.sh`. For each message, ntfy.sh gets only a `poll_request` with the message id and a hashed topic. The text stays on the Pi, and the iOS app fetches it from here. Those two lines live only in the Pi's `/etc/ntfy/server.yml`; `setup-ntfy.sh` keeps them when it re-runs.

Phone steps: `ntfy/PHONE-SETUP.md`.
