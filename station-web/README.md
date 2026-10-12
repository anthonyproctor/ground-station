# station-web

The public front end for the Pi 3 ADS-B station, served by lighttpd on port 8504.

| Path | What |
|---|---|
| `/` | Station home page (`html/index.html`): live counts, best of today, Denver weather |
| `/map/` | tar1090 live map. Old `/?...` links redirect to `/map/?...` |
| `/graphs1090/` | graphs1090 with friendlier captions and a dark, mobile layout |
| `/station-data/station.json` | Minute stats written by `station-stats.timer` |

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
