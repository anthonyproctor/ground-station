#!/usr/bin/env python3
"""Ground Station alert engine and GPS-interference monitor.

A small long-running service (station-alerts.service). Every few seconds it
reads readsb's aircraft.json and raises alerts:

  emergency   squawk 7500 / 7600 / 7700, or readsb's `emergency` field
  military    tar1090 db flags (military bit), or readsb `dbFlags` & 1
  rare        aircraft type with few airframes worldwide (same tar1090 db
              type counts station-stats uses for "rarest type")
  high        above 50,000 ft
  low_home    low and close to the station            (PRIVATE, never served)
  gps         several aircraft lose GPS integrity together (regional)
  lookup      an interesting aircraft (military, rare, helicopter, heavy) about
              to pass close and low enough to see, in daylight: which way to
              look, how high, how far, when                (PRIVATE, phone only)

Outputs:
  /var/lib/ground-station/alerts.jsonl      every alert, private (dir is 0700)
  /var/lib/ground-station/gps-health.jsonl  one GPS health point per minute
  /var/lib/ground-station/alerts-state.json de-dup memory across restarts
  /run/station-web/alerts.json              public subset for the home page

Privacy: the station location (/etc/ground-station/location.env) is read
only to decide `low_home`. Those alerts are marked private and only go to the
private log. The public file carries no coordinates, distances or bearings,
and GPS alerts name a region, not a position.

Notifications: pluggable, OFF by default (notifier = none). The Pi's install
sets notifier = ntfy, posting to the local ntfy server only. De-dup is one
alert per aircraft per kind per few hours, plus a global hourly cap. The unit
allows sockets to localhost only.

Python 3 standard library only.
"""
import argparse
import configparser
import glob
import gzip
import json
import math
import os
import sys
import time
from collections import OrderedDict, deque

AIRCRAFT = "/run/readsb/aircraft.json"
CONFIG = "/etc/ground-station/alerts.conf"
LOCATION_FILE = "/etc/ground-station/location.env"
TAR_HTML = "/usr/local/share/tar1090/html"
TYPECOUNT_DIR = "/var/lib/station-web"  # written by station-stats.py
STATE_DIR = "/var/lib/ground-station"
OUT_DIR = "/run/station-web"

DEFAULTS = {
    "engine": {
        "poll_seconds": "8",
        "dedupe_hours": "4",
        "public_max": "30",
        "public_hours": "48",
        "max_seen_s": "15",
        # military / rare wait up to this long for a fresh position (so the
        # alert can say where it is and the map can follow it); after that
        # they go out marked "no position yet". Emergencies never wait.
        "wait_kinds": "military,rare",
        "wait_for_position_s": "300",
        "fresh_position_s": "30",
        # radius for "near <town>" in alert text (nmi)
        "place_radius_nm": "30",
    },
    "emergency": {
        "enabled": "yes",
        "squawks": "7500,7600,7700",
        # readsb emergency values that do NOT alert. lifeguard = air ambulance
        # priority, common around Denver and not an emergency.
        "ignore": "none,reserved,lifeguard",
        "confirm_polls": "2",
    },
    "military": {"enabled": "yes"},
    "rare": {"enabled": "yes", "max_worldwide": "25"},
    "high": {"enabled": "yes", "min_alt_ft": "50000"},
    "low_home": {
        "enabled": "yes",
        "radius_nm": "2",
        "max_agl_ft": "2000",
        # blank = use the height in location.env, else 5400 ft (Denver)
        "field_elev_ft": "",
    },
    "lookup": {
        "enabled": "yes",
        "radius_nm": "5",
        "max_agl_ft": "10000",
        # sun at or above this many degrees = daylight. -6 = end of civil twilight
        "sun_min_deg": "-6",
        # don't announce more than this far ahead of closest approach
        "max_lead_min": "10",
        # this close (nmi) = "overhead now"
        "overhead_nm": "0.5",
        "dedupe_hours": "2",
        # ICAO type designators; a trailing * matches a family (B74* = all 747s)
        "heavy_types": "B74*,B77*,A38*,A124,A225,AN22,C17,C5,C5M,KC46,K46,K35*,C135,R135,"
                       "E3*,E6,E4,B52,B1,B2,C130,C30J,L100,A400",
        "military": "yes",
        "rare": "yes",
        "helicopters": "yes",
    },
    "gps": {
        "enabled": "yes",
        "window_s": "300",
        "nacp_min": "8",
        "nic_min": "7",
        "min_tracked": "6",
        "min_degraded": "3",
        "min_share": "0.25",
        "clear_after_s": "600",
        "cooldown_hours": "2",
        "region": "Denver area",
    },
    "notify": {
        # none | log | ntfy | pushover | email   (Anthony picks; none = off)
        "notifier": "none",
        "kinds": "emergency,military,rare,low_home,gps,lookup",
        # global cap on notifications per rolling hour; these kinds skip it
        "max_per_hour": "6",
        "cap_exempt": "emergency",
        "ntfy_url": "http://127.0.0.1:2586",
        "ntfy_topic": "",
        "ntfy_token": "",
        # public map link for the notification tap, e.g. https://<host>/map
        "map_url": "",
        "map_zoom": "11",
        "pushover_token": "",
        "pushover_user": "",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "email_from": "",
        "email_to": "",
    },
}

PUBLIC_KINDS = {"emergency", "military", "rare", "high", "gps", "gps_clear"}
SQUAWK_NAMES = {"7500": "hijack code", "7600": "radio failure", "7700": "general emergency"}
EMERGENCY_NAMES = {
    "general": "general emergency",
    "lifeguard": "lifeguard / medical",
    "minfuel": "minimum fuel",
    "nordo": "no radio",
    "unlawful": "unlawful interference",
    "downed": "downed aircraft",
}
LOG_MAX_BYTES = 5 * 1024 * 1024
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
FT_PER_NM = 6076.12

# Coarse, well-known Front Range places for "near <town>" in alert text. Town
# centres to 2 decimals (public map knowledge). Used on the AIRCRAFT's
# position only, never relative to the station.
PLACES = (
    ("Denver", 39.74, -104.99), ("Aurora", 39.73, -104.83), ("DIA", 39.86, -104.67),
    ("Buckley SFB", 39.70, -104.75), ("Boulder", 40.01, -105.27), ("Golden", 39.76, -105.22),
    ("Lakewood", 39.70, -105.08), ("Littleton", 39.61, -105.02), ("Centennial", 39.58, -104.88),
    ("Highlands Ranch", 39.55, -104.97), ("Parker", 39.52, -104.76), ("Castle Rock", 39.37, -104.86),
    ("Brighton", 39.99, -104.82), ("Thornton", 39.87, -104.97), ("Westminster", 39.84, -105.04),
    ("Broomfield", 39.92, -105.07), ("Arvada", 39.80, -105.09), ("Commerce City", 39.81, -104.93),
    ("Erie", 40.05, -105.05), ("Longmont", 40.17, -105.10), ("Loveland", 40.40, -105.07),
    ("Fort Collins", 40.59, -105.08), ("Greeley", 40.42, -104.71), ("Fort Lupton", 40.08, -104.81),
    ("Hudson", 40.07, -104.64), ("Bennett", 39.76, -104.43), ("Byers", 39.71, -104.23),
    ("Elizabeth", 39.36, -104.60), ("Kiowa", 39.35, -104.46), ("Sedalia", 39.44, -104.96),
    ("Evergreen", 39.63, -105.32), ("Conifer", 39.52, -105.30), ("Idaho Springs", 39.74, -105.51),
    ("Nederland", 39.96, -105.51), ("Estes Park", 40.38, -105.52), ("Winter Park", 39.89, -105.76),
    ("Monument", 39.09, -104.87), ("Air Force Academy", 38.99, -104.86), ("Woodland Park", 38.99, -105.06),
    ("Black Forest", 39.01, -104.70), ("Colorado Springs", 38.83, -104.82), ("Peterson SFB", 38.82, -104.70),
    ("Pueblo", 38.25, -104.61), ("Limon", 39.26, -103.69), ("Fort Morgan", 40.25, -103.80),
    ("Cheyenne", 41.14, -104.82),
)


def nearest_place(lat, lon, radius_nm):
    best = None
    for name, plat, plon in PLACES:
        d = dist_nm(lat, lon, plat, plon)
        if d <= radius_nm and (best is None or d < best[1]):
            best = (name, d)
    return best[0] if best else None


# ---------------------------------------------------------------- helpers


def place_phrase(lat, lon, radius_nm):
    """'near Parker' when within 4 nmi of a town, else '12 mi E of Parker'
    (direction is from the town to the AIRCRAFT). Empty if no town in range."""
    best = None
    for name, plat, plon in PLACES:
        d = dist_nm(lat, lon, plat, plon)
        if d <= radius_nm and (best is None or d < best[1]):
            best = (name, d, plat, plon)
    if not best:
        return ""
    name, d, plat, plon = best
    if d <= 4:
        return "near " + name
    e, n = local_nm(plat, plon, lat, lon)
    brg = math.degrees(math.atan2(e, n)) % 360
    return "%d mi %s of %s" % (round(d * 1.15078), COMPASS[int((brg + 22.5) // 45) % 8], name)

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj, mode=0o644):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def append_jsonl(path, obj):
    try:
        if os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")


def gz_json(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def sun_elevation(lat, lon, t):
    """Sun elevation in degrees at lat/lon and unix time t (low-precision
    almanac formulas, good to well under a degree; stdlib only)."""
    d = t / 86400.0 + 2440587.5 - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians(gmst * 15 + lon - ra)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def local_nm(home_lat, home_lon, lat, lon):
    """(east, north) of a point from home in nmi, flat earth (fine inside ~20 nmi)."""
    return ((lon - home_lon) * 60.0 * math.cos(math.radians(home_lat)), (lat - home_lat) * 60.0)


def read_location(path):
    """(lat, lon, meters or None) from GS_LOCATION="lat,lon,meters", or None."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GS_LOCATION="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    parts = [float(x) for x in val.split(",")]
                    return parts[0], parts[1], (parts[2] if len(parts) > 2 else None)
    except (OSError, ValueError, IndexError):
        pass
    return None


def load_config(path):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict(DEFAULTS)
    # missing files = defaults; alerts.conf.d/*.conf (e.g. ntfy.conf, written
    # by setup-ntfy.sh with the topic and token) override alerts.conf
    cp.read([path] + sorted(glob.glob(path + ".d/*.conf")))
    return cp


def csv_set(s):
    return {x.strip() for x in s.split(",") if x.strip()}


class LRU(OrderedDict):
    def __init__(self, cap):
        super().__init__()
        self.cap = cap

    def get_or(self, key, make):
        if key in self:
            self.move_to_end(key)
            return self[key]
        val = make()
        self[key] = val
        if len(self) > self.cap:
            self.popitem(last=False)
        return val


class TarDb:
    """Offline lookups in tar1090's aircraft db, same layout station-stats reads.

    Shards are a few MB each once parsed, so only a handful stay in memory;
    per-aircraft results are cached by hex instead.
    """

    def __init__(self, html=TAR_HTML, typecount_dir=TYPECOUNT_DIR):
        self.html = html
        self.typecount_dir = typecount_dir
        self.path = None
        self.checked = 0
        self._refresh()

    def _refresh(self):
        self.checked = time.time()
        dirs = sorted(glob.glob(os.path.join(self.html, "db-*")), key=os.path.getmtime)
        path = dirs[-1] if dirs else None
        if path == self.path:
            return
        self.path = path
        self.shards = LRU(6)
        self.hexes = LRU(4000)
        self.counts = None
        self.typenames = None

    def maybe_refresh(self):
        if time.time() - self.checked > 3600:
            self._refresh()

    def _shard(self, key):
        def load():
            try:
                return gz_json(os.path.join(self.path, key + ".js"))
            except (OSError, ValueError):
                return {}
        return self.shards.get_or(key, load)

    def _lookup(self, hexid):
        if not self.path:
            return None
        hexid = hexid.upper()
        n = 1
        while n < len(hexid):
            data = self._shard(hexid[:n])
            rec = data.get(hexid[n:])
            if rec:
                return rec
            if hexid[: n + 1] in (data.get("children") or []):
                n += 1
                continue
            return None
        return None

    def info(self, hexid):
        """(registration, type, military) for a hex."""
        def make():
            r = self._lookup(hexid)
            if not r:
                return ("", "", False)
            return (r[0] or "", r[1] or "", (r[2] or "0")[:1] == "1")
        return self.hexes.get_or(hexid, make)

    def worldwide(self, t):
        if self.counts is None:
            name = os.path.basename(self.path) if self.path else ""
            self.counts = load_json(os.path.join(self.typecount_dir, "typecounts-%s.json" % name), {})
        return self.counts.get(t, 0)

    def desc(self, t):
        """ICAO description code, e.g. L4J (landplane, 4 jets), H2T (helicopter)."""
        self.type_name(t)
        v = self.typenames.get(t)
        return v[1] if isinstance(v, list) and len(v) > 1 else ""

    def operator(self, callsign):
        if getattr(self, "operators", None) is None:
            self.operators = {}
            if self.path:
                try:
                    self.operators = gz_json(os.path.join(self.path, "operators.js"))
                except (OSError, ValueError):
                    pass
        code = callsign[:3] if len(callsign) > 3 and callsign[:3].isalpha() and callsign[3:4].isdigit() else ""
        return (self.operators.get(code) or {}).get("n", "") if code else ""

    def type_name(self, t):
        if self.typenames is None:
            self.typenames = {}
            if self.path:
                try:
                    self.typenames = gz_json(os.path.join(self.path, "icao_aircraft_types2.js"))
                except (OSError, ValueError):
                    pass
        v = self.typenames.get(t)
        return v[0] if isinstance(v, list) and v else t


# -------------------------------------------------------------- notifiers

class Notifier:
    """Base: does nothing. Subclasses send one alert somewhere."""
    name = "none"

    def __init__(self, cfg):
        self.cfg = cfg

    def send(self, alert):
        pass


class LogNotifier(Notifier):
    name = "log"

    def send(self, alert):
        print("ALERT %s %s" % (alert["kind"], alert["title"]), flush=True)


class NtfyNotifier(Notifier):
    """Posts to a ntfy server, normally the one on the Pi at 127.0.0.1:2586.

    Message text goes only to that server. With upstream-base-url set, ntfy
    sends ntfy.sh just a poll_request (message id) so iOS can wake up.
    """
    name = "ntfy"
    PRIORITY = {"emergency": 5, "gps": 4, "lookup": 4, "low_home": 3, "military": 3,
                "rare": 3, "high": 2, "gps_clear": 2}
    TAGS = {"emergency": "rotating_light", "gps": "satellite_antenna", "gps_clear": "white_check_mark",
            "low_home": "house", "lookup": "eyes", "military": "airplane", "rare": "star", "high": "arrow_up"}

    def send(self, alert):
        import urllib.request
        body = {
            "topic": self.cfg["ntfy_topic"],
            "title": "Ground Station: " + alert["kind"].replace("_", " "),
            "message": alert["title"] + ((". " + alert["detail"]) if alert.get("detail") else "")
                       + ((" (" + alert["home_rel"] + ")") if alert.get("home_rel") else ""),
            "priority": self.PRIORITY.get(alert["kind"], 3),
            "tags": [self.TAGS.get(alert["kind"], "airplane")],
        }
        if alert.get("hex") and self.cfg.get("map_url"):
            # tar1090: ?icao= selects, isolates and follows the plane
            body["click"] = "%s/?icao=%s&zoom=%s" % (self.cfg["map_url"].rstrip("/"), alert["hex"],
                                                     self.cfg.get("map_zoom") or "11")
        req = urllib.request.Request(self.cfg["ntfy_url"].rstrip("/") + "/",
                                     data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        if self.cfg.get("ntfy_token"):
            req.add_header("Authorization", "Bearer " + self.cfg["ntfy_token"])
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()


class PushoverNotifier(Notifier):
    name = "pushover"

    def send(self, alert):
        import urllib.parse
        import urllib.request
        body = urllib.parse.urlencode({
            "token": self.cfg["pushover_token"], "user": self.cfg["pushover_user"],
            "title": "Ground Station: " + alert["kind"], "message": alert["title"],
        }).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=body, timeout=8).read()


class EmailNotifier(Notifier):
    name = "email"

    def send(self, alert):
        import smtplib
        from email.message import EmailMessage
        m = EmailMessage()
        m["Subject"] = "Ground Station: " + alert["title"]
        m["From"] = self.cfg["email_from"]
        m["To"] = self.cfg["email_to"]
        m.set_content(json.dumps({k: v for k, v in alert.items() if k != "private"}, indent=1))
        with smtplib.SMTP(self.cfg["smtp_host"], int(self.cfg["smtp_port"] or 587), timeout=10) as s:
            s.starttls()
            if self.cfg.get("smtp_user"):
                s.login(self.cfg["smtp_user"], self.cfg["smtp_password"])
            s.send_message(m)


NOTIFIERS = {c.name: c for c in (Notifier, LogNotifier, NtfyNotifier, PushoverNotifier, EmailNotifier)}


def make_notifier(cfg):
    sec = dict(cfg["notify"])
    cls = NOTIFIERS.get(sec.get("notifier", "none").strip().lower(), Notifier)
    return cls(sec)


# ------------------------------------------------------------ GPS monitor

class GpsMonitor:
    """Share of ADS-B aircraft whose navigation integrity just went bad.

    An aircraft counts as degraded when readsb reports `gpsOkBefore` (it had
    good GPS and lost it), or when its NACp / NIC drops below the thresholds
    AFTER we saw it good. Aircraft that are always poor (old or uncertified
    GPS) never count, so they can't fake an event.
    """

    def __init__(self, sec, now=None):
        self.window = float(sec["window_s"])
        self.nacp_min = int(sec["nacp_min"])
        self.nic_min = int(sec["nic_min"])
        self.min_tracked = int(sec["min_tracked"])
        self.min_degraded = int(sec["min_degraded"])
        self.min_share = float(sec["min_share"])
        self.clear_after = float(sec["clear_after_s"])
        self.cooldown = float(sec["cooldown_hours"]) * 3600
        self.region = sec["region"]
        self.ac = {}  # hex -> [last_seen, last_degraded, ever_good]
        self.active = False
        self.active_since = 0
        self.quiet_since = 0
        self.last_alert = 0
        self.peak = 0
        self.minute = {}  # current minute bucket
        self.series = deque(maxlen=1440)

    def _bad(self, a):
        nacp, nic = a.get("nac_p"), a.get("nic")
        return (isinstance(nacp, int) and nacp < self.nacp_min) or (isinstance(nic, int) and nic < self.nic_min)

    def observe(self, aircraft, now, max_seen):
        for a in aircraft:
            hexid = a.get("hex", "")
            if not hexid or hexid.startswith("~"):
                continue
            if not str(a.get("type", "")).startswith("adsb"):
                continue  # mlat / tisb / mode_s have no own GPS integrity
            if a.get("seen", 999) > max_seen or a.get("alt_baro") == "ground":
                continue
            if a.get("nac_p") is None and a.get("nic") is None and "gpsOkBefore" not in a:
                continue
            st = self.ac.setdefault(hexid, [0, 0, False])
            st[0] = now
            bad = self._bad(a)
            if "gpsOkBefore" in a or (bad and st[2]):
                st[1] = now
            elif not bad:
                st[2] = True
        cut = now - self.window
        for h in [h for h, s in self.ac.items() if s[0] < now - 3600]:
            del self.ac[h]
        tracked = sum(1 for s in self.ac.values() if s[0] >= cut)
        degraded = sum(1 for s in self.ac.values() if s[0] >= cut and s[1] >= cut)
        return tracked, degraded

    def evaluate(self, tracked, degraded, now):
        """Returns a list of new events (dicts) and updates status."""
        events = []
        share = degraded / tracked if tracked else 0.0
        hot = tracked >= self.min_tracked and degraded >= self.min_degraded and share >= self.min_share
        if hot:
            self.quiet_since = 0
            self.peak = max(self.peak, degraded)
            if not self.active:
                self.active = True
                self.active_since = now
                self.peak = degraded
                if now - self.last_alert >= self.cooldown:
                    self.last_alert = now
                    events.append({
                        "kind": "gps",
                        "title": "Possible GPS interference, %s" % self.region,
                        "detail": "%d of %d aircraft lost GPS integrity in the last %d min"
                        % (degraded, tracked, round(self.window / 60)),
                        "tracked": tracked, "degraded": degraded,
                    })
        elif self.active:
            calm = degraded < max(1, self.min_degraded / 2) or share < self.min_share / 2
            if calm:
                self.quiet_since = self.quiet_since or now
                if now - self.quiet_since >= self.clear_after:
                    self.active = False
                    events.append({
                        "kind": "gps_clear",
                        "title": "GPS back to normal, %s" % self.region,
                        "detail": "interference lasted about %d min, up to %d aircraft"
                        % (max(1, round((self.quiet_since - self.active_since) / 60)), self.peak),
                    })
            else:
                self.quiet_since = 0
        # per-minute series (max degraded, max tracked within the minute)
        m = int(now // 60) * 60
        if self.minute.get("t") != m:
            if self.minute:
                self.series.append([self.minute["t"], self.minute["n"], self.minute["d"]])
                events.append({"_series": dict(self.minute)})
            self.minute = {"t": m, "n": tracked, "d": degraded}
        else:
            self.minute["n"] = max(self.minute["n"], tracked)
            self.minute["d"] = max(self.minute["d"], degraded)
        return events, share

    def status(self, tracked, degraded, share):
        state = "interference" if self.active else ("some" if degraded else "ok")
        # 24 h at 10-minute resolution for the page sparkline
        buckets = OrderedDict()
        for t, n, d in self.series:
            b = int(t // 600) * 600
            cur = buckets.get(b)
            buckets[b] = [max(cur[0], n), max(cur[1], d)] if cur else [n, d]
        return {
            "state": state,
            "region": self.region,
            "window_min": round(self.window / 60),
            "tracked": tracked,
            "degraded": degraded,
            "share": round(share, 3),
            "series": [[t, v[0], v[1]] for t, v in buckets.items()],
        }

    def load_series(self, rows, now):
        for r in rows:
            if r.get("t", 0) >= now - 86400:
                self.series.append([r["t"], r.get("n", 0), r.get("d", 0)])


# ------------------------------------------------------------------ engine

class Engine:
    def __init__(self, cfg, db=None, home=None, state_dir=STATE_DIR, out_dir=OUT_DIR,
                 notifier=None, now=None):
        self.cfg = cfg
        self.db = db
        self.state_dir = state_dir
        self.out_dir = out_dir
        now = now or time.time()
        e = cfg["engine"]
        self.dedupe = float(e["dedupe_hours"]) * 3600
        self.public_max = int(e["public_max"])
        self.public_age = float(e["public_hours"]) * 3600
        self.max_seen = float(e["max_seen_s"])

        em = cfg["emergency"]
        self.squawks = csv_set(em["squawks"])
        self.em_ignore = csv_set(em["ignore"])
        self.confirm = max(1, int(em["confirm_polls"]))
        self.pending = {}  # (hex, reason) -> consecutive polls

        self.rare_max = int(cfg["rare"]["max_worldwide"])
        self.high_min = float(cfg["high"]["min_alt_ft"])

        lh = cfg["low_home"]
        self.home_loc = home  # private: low_home, lookup, phone-only "from home" text
        self.home = home if cfg.getboolean("low_home", "enabled") else None
        self.home_r = float(lh["radius_nm"])
        self.home_agl = float(lh["max_agl_ft"])
        if lh["field_elev_ft"].strip():
            self.field = float(lh["field_elev_ft"])
        elif home and home[2] is not None:
            self.field = home[2] * 3.28084
        else:
            self.field = 5400.0

        lk = cfg["lookup"]
        self.lookup_on = bool(home) and cfg.getboolean("lookup", "enabled")
        self.lk_r = float(lk["radius_nm"])
        self.lk_agl = float(lk["max_agl_ft"])
        self.lk_sun = float(lk["sun_min_deg"])
        self.lk_lead = float(lk["max_lead_min"])
        self.lk_over = float(lk["overhead_nm"])
        self.lk_window = float(lk["dedupe_hours"]) * 3600
        heavy = [x.upper() for x in csv_set(lk["heavy_types"])]
        self.lk_heavy = {x for x in heavy if not x.endswith("*")}
        self.lk_heavy_fam = tuple(x[:-1] for x in heavy if x.endswith("*"))
        self.lk_mil = cfg.getboolean("lookup", "military")
        self.lk_rare = cfg.getboolean("lookup", "rare")
        self.lk_heli = cfg.getboolean("lookup", "helicopters")
        self.lk_sun_cache = (0, -90.0)

        self.wait_kinds = csv_set(e["wait_kinds"])
        self.wait_s = float(e["wait_for_position_s"])
        self.fresh_s = float(e["fresh_position_s"])
        self.place_r = float(e["place_radius_nm"])
        self.held = {}  # dedupe key -> waiting alert (memory only)

        self.gps = GpsMonitor(cfg["gps"]) if cfg.getboolean("gps", "enabled") else None
        self.notifier = notifier if notifier is not None else make_notifier(cfg)
        kinds = csv_set(cfg["notify"]["kinds"])
        self.notify_kinds = kinds | ({"gps_clear"} if "gps" in kinds else set())
        self.max_per_hour = int(cfg["notify"]["max_per_hour"])
        self.cap_exempt = csv_set(cfg["notify"]["cap_exempt"])
        self.notified = deque()  # send times in the last hour
        self.suppressed = 0

        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        self.state_path = os.path.join(state_dir, "alerts-state.json")
        self.log_path = os.path.join(state_dir, "alerts.jsonl")
        self.gps_path = os.path.join(state_dir, "gps-health.jsonl")
        st = load_json(self.state_path, {})
        self.sent = {k: v for k, v in st.get("sent", {}).items() if now - v < self.dedupe}
        self.public = [a for a in st.get("public", []) if now - a["t"] < self.public_age]
        if self.gps:
            self.gps.last_alert = st.get("gps_last_alert", 0)
            self.gps.load_series(self._tail_jsonl(self.gps_path, 1500), now)
        self.last_public_write = 0
        self.dirty = True

    @staticmethod
    def _tail_jsonl(path, n):
        rows = deque(maxlen=n)
        try:
            with open(path) as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        return rows

    def _on(self, sec):
        return self.cfg.getboolean(sec, "enabled")

    def _ident(self, a):
        hexid = a["hex"].lower()
        reg, t, mil = self.db.info(hexid) if self.db else ("", "", False)
        t = a.get("t") or t
        reg = a.get("r") or reg
        flags = a.get("dbFlags")
        if isinstance(flags, int) and flags & 1:
            mil = True
        cs = (a.get("flight") or "").strip()
        return hexid, cs, reg, t, mil

    def _alt(self, a):
        alt = a.get("alt_baro")
        return alt if isinstance(alt, (int, float)) else None

    def _fresh(self, a):
        return "lat" in a and "lon" in a and a.get("seen_pos", 999) <= self.fresh_s

    def _label(self, a, ident):
        """'DAGGR44 · PILATUS PC-12 (07-0779) · heading NE · 8,400 ft near Castle Rock'.
        Place = nearest well-known town to the AIRCRAFT; nothing about home."""
        hexid, cs, reg, t, _ = ident
        tname = (self.db.type_name(t) if (self.db and t) else t) or "unknown type"
        alt = a.get("alt_baro")
        parts = [cs or reg or hexid.upper(), tname + ((" (%s)" % reg) if (cs and reg) else "")]
        trk, gs = a.get("track"), a.get("gs")
        if alt != "ground" and isinstance(trk, (int, float)) and isinstance(gs, (int, float)) and gs > 30:
            parts.append("heading " + COMPASS[int((trk % 360 + 22.5) // 45) % 8])
        where = ""
        if alt == "ground":
            where = "on the ground"
        elif isinstance(alt, (int, float)):
            where = "%s ft" % format(int(round(alt, -2)), ",")
        if self._fresh(a):
            place = place_phrase(a["lat"], a["lon"], self.place_r)
            if place:
                where = (where + " " + place).strip()
        else:
            where = (where + (", " if where else "") + "no position yet")
        if where:
            parts.append(where)
        return " \u00b7 ".join(parts)

    def _gate(self, out, now, kind, a, ident, make, key=None, window=None, private=False):
        """Emit, or hold a wait_kinds alert until the aircraft has a fresh
        position (up to wait_for_position_s). make(a, ident) -> (title, detail, extra)."""
        key = key or "%s:%s" % (kind, ident[0])
        if now - self.sent.get(key, 0) < (self.dedupe if window is None else window):
            return
        if kind in self.wait_kinds and not self._fresh(a):
            h = self.held.setdefault(key, {"first": now})
            h.update(kind=kind, a=dict(a), ident=ident, make=make, window=window, private=private)
            return
        self.held.pop(key, None)
        title, detail, extra = make(a, ident)
        self._emit(out, now, kind, a, ident, title, detail, private=private, extra=extra,
                   key=key, window=window)

    def _release_held(self, out, now):
        for key, h in list(self.held.items()):
            if now - h["first"] >= self.wait_s:
                del self.held[key]
                title, detail, extra = h["make"](h["a"], h["ident"])
                self._emit(out, now, h["kind"], h["a"], h["ident"], title, detail, private=h["private"],
                           extra=dict(extra or {}, no_position=True), key=key, window=h["window"])

    def _emit(self, out, now, kind, a, ident, title, detail, private=False, extra=None, key=None,
              window=None):
        hexid, cs, reg, t, _ = ident
        key = key or "%s:%s" % (kind, hexid)
        if now - self.sent.get(key, 0) < (self.dedupe if window is None else window):
            return
        self.sent[key] = now
        alert = {
            "t": int(now), "kind": kind, "hex": hexid, "callsign": cs, "reg": reg,
            "type": t, "type_name": (self.db.type_name(t) if (self.db and t) else t),
            "squawk": a.get("squawk"), "alt_ft": self._alt(a),
            "title": title, "detail": detail, "private": private,
        }
        if extra:
            alert.update(extra)
        # phone-only: where it is from home. Not in PUBLIC_FIELDS, never served.
        if self.home_loc and "lat" in a and "lon" in a and "dist_nm" not in alert:
            e, n = local_nm(self.home_loc[0], self.home_loc[1], a["lat"], a["lon"])
            alert["home_rel"] = "%.0f nmi %s of home" % (math.hypot(e, n),
                                                        COMPASS[int((math.degrees(math.atan2(e, n)) % 360 + 22.5) // 45) % 8])
        out.append(alert)

    def step(self, doc, now=None):
        now = now or time.time()
        if self.db:
            self.db.maybe_refresh()
        ac = [a for a in doc.get("aircraft", []) if a.get("hex") and not a["hex"].startswith("~")
              and a.get("seen", 999) <= self.max_seen]
        out = []
        live_pending = set()
        for a in ac:
            ident = None
            name = lambda i: i[1] or i[2] or i[0].upper()

            # emergency: squawk or readsb emergency field, confirmed over N polls
            if self._on("emergency"):
                sq = str(a.get("squawk") or "")
                em = a.get("emergency")
                reason = None
                if sq in self.squawks:
                    reason = "squawk %s (%s)" % (sq, SQUAWK_NAMES.get(sq, "emergency"))
                elif em and em not in self.em_ignore:
                    reason = "emergency: %s" % EMERGENCY_NAMES.get(em, em)
                if reason:
                    pk = (a["hex"], reason)
                    live_pending.add(pk)
                    self.pending[pk] = self.pending.get(pk, 0) + 1
                    if self.pending[pk] >= self.confirm:
                        ident = ident or self._ident(a)
                        self._emit(out, now, "emergency", a, ident,
                                   "%s \u00b7 %s" % (self._label(a, ident), reason), reason)

            # military
            if self._on("military"):
                ident = ident or self._ident(a)
                if ident[4]:
                    self._gate(out, now, "military", a, ident, lambda a2, i2: (
                        "Military: " + self._label(a2, i2),
                        "military flag in the tar1090 aircraft database", None))

            # rare type
            if self._on("rare") and self.db:
                ident = ident or self._ident(a)
                t = ident[3]
                n = self.db.worldwide(t) if t else 0
                if 0 < n <= self.rare_max:
                    self._gate(out, now, "rare", a, ident, lambda a2, i2, n=n: (
                        "Rare: " + self._label(a2, i2),
                        "%d of this type in the world" % n, {"worldwide": n}),
                        key="rare:type:%s" % t)  # once per type, not per airframe

            alt = self._alt(a)
            # very high
            if self._on("high") and alt is not None and alt > self.high_min and a.get("seen", 999) < 30:
                ident = ident or self._ident(a)
                self._emit(out, now, "high", a, ident,
                           "Very high: %s at %s ft" % (name(ident), format(int(alt), ",")),
                           "above %s ft" % format(int(self.high_min), ","))

            # low near the station: PRIVATE
            if self.home and "lat" in a and "lon" in a and a.get("seen_pos", 999) <= self.max_seen:
                h = a.get("alt_geom")
                h = h if isinstance(h, (int, float)) else alt
                if h is not None:
                    agl = h - self.field
                    if agl < self.home_agl:
                        d = dist_nm(self.home[0], self.home[1], a["lat"], a["lon"])
                        if d <= self.home_r:
                            ident = ident or self._ident(a)
                            self._emit(out, now, "low_home", a, ident,
                                       "Low near home: %s about %s ft above ground" % (name(ident), format(int(agl), ",")),
                                       "within %g nmi of the station" % self.home_r, private=True,
                                       extra={"agl_ft": int(agl), "dist_nm": round(d, 2)})

        if self.lookup_on and self._daylight(now):
            for a in ac:
                self._lookup(out, now, a)
        self._release_held(out, now)

        for pk in [k for k in self.pending if k not in live_pending]:
            del self.pending[pk]

        gps_status = None
        if self.gps:
            tracked, degraded = self.gps.observe(ac, now, self.max_seen)
            events, share = self.gps.evaluate(tracked, degraded, now)
            for ev in events:
                if "_series" in ev:
                    append_jsonl(self.gps_path, ev["_series"])
                    self.dirty = True
                    continue
                alert = {"t": int(now), "private": False}
                alert.update(ev)
                out.append(alert)
            gps_status = self.gps.status(tracked, degraded, share)

        for alert in out:
            self._record(alert)
        self._write_public(now, gps_status, force=bool(out))
        return out, gps_status

    def _daylight(self, now):
        t, el = self.lk_sun_cache
        if now - t > 60:
            el = sun_elevation(self.home_loc[0], self.home_loc[1], now)
            self.lk_sun_cache = (now, el)
        return el >= self.lk_sun

    def _interesting(self, a, ident):
        """Why this aircraft is worth stepping outside for, or None."""
        t = (ident[3] or "").upper()
        desc = self.db.desc(t) if (self.db and t and hasattr(self.db, "desc")) else ""
        if self.lk_mil and ident[4]:
            return "military"
        if self.lk_heli and (a.get("category") == "A7" or desc[:1] in ("H", "G", "R")):
            return "helicopter"
        if t and (t in self.lk_heavy or (self.lk_heavy_fam and t.startswith(self.lk_heavy_fam))):
            return "heavy"
        if self.lk_rare and self.db and t and 0 < self.db.worldwide(t) <= self.rare_max:
            return "rare"
        return None

    def _lookup(self, out, now, a):
        """PRIVATE look-up alert: direction, elevation, distance, time to closest."""
        if not self._fresh(a):
            return
        h = a.get("alt_geom")
        h = h if isinstance(h, (int, float)) else self._alt(a)
        if h is None:
            return
        agl = h - self.field
        if agl > self.lk_agl:
            return
        e, n = local_nm(self.home_loc[0], self.home_loc[1], a["lat"], a["lon"])
        dist = math.hypot(e, n)
        if dist > self.lk_r:
            return
        gs, trk = a.get("gs"), a.get("track")
        if dist <= self.lk_over:
            when, tmin = "overhead now", 0.0
        elif isinstance(gs, (int, float)) and isinstance(trk, (int, float)) and gs > 30:
            ve, vn = gs * math.sin(math.radians(trk)), gs * math.cos(math.radians(trk))
            tcpa = -(e * ve + n * vn) / (ve * ve + vn * vn) * 60.0  # minutes
            cpa = math.hypot(e + ve * tcpa / 60.0, n + vn * tcpa / 60.0)
            if tcpa < -0.5 or tcpa > self.lk_lead:
                return  # already past, or too far ahead to be useful
            if tcpa <= 0.5:
                when, tmin = "closest now", 0.0
            else:
                when, tmin = "closest in about %d min (%.1f nmi)" % (max(1, round(tcpa)), cpa), tcpa
        else:
            return  # no track/speed: can't tell if it's coming
        ident = self._ident(a)
        why = self._interesting(a, ident)
        if not why:
            return
        brg = math.degrees(math.atan2(e, n)) % 360
        look = COMPASS[int((brg + 22.5) // 45) % 8]
        elev = math.degrees(math.atan2(max(agl, 0), dist * FT_PER_NM))
        hexid, cs, reg, t, _ = ident
        tname = self.db.type_name(t) if (self.db and t) else (t or "aircraft")
        op = self.db.operator(cs) if (self.db and cs and hasattr(self.db, "operator")) else ""
        who = ", ".join(x for x in (cs or reg or hexid.upper(), op) if x)
        up = "straight up" if elev >= 75 else "about %d\u00b0 up" % round(elev)
        title = "Look %s, %s: %s (%s)" % (look, up, tname, who)
        detail = "%s, %.1f nmi away, %s ft above ground, %s" % (why, dist, format(int(agl), ","), when)
        self._emit(out, now, "lookup", a, ident, title, detail, private=True, window=self.lk_window,
                   extra={"look": look, "elev_deg": round(elev), "dist_nm": round(dist, 1),
                          "eta_min": round(tmin, 1), "why": why, "agl_ft": int(agl)})

    def _record(self, alert):
        append_jsonl(self.log_path, alert)
        if not alert["private"] and alert["kind"] in PUBLIC_KINDS:
            self.public.insert(0, public_view(alert))
            del self.public[self.public_max:]
        if alert["kind"] in self.notify_kinds:
            self._notify(alert)
        self.dirty = True

    def _notify(self, alert):
        """De-dup already happened in _emit; this adds the global hourly cap."""
        now = alert["t"]
        while self.notified and now - self.notified[0] >= 3600:
            self.notified.popleft()
        if alert["kind"] not in self.cap_exempt and len(self.notified) >= self.max_per_hour:
            self.suppressed += 1
            return
        if self.suppressed:
            alert = dict(alert, detail=(alert.get("detail") or "") +
                         " (+%d more held back by the hourly cap, see the log)" % self.suppressed)
            self.suppressed = 0
        self.notified.append(now)
        try:
            self.notifier.send(alert)
        except Exception as ex:  # never let delivery break the engine
            print("notifier %s failed: %s" % (self.notifier.name, ex), file=sys.stderr, flush=True)

    def _write_public(self, now, gps_status, force=False):
        if not (force or self.dirty or now - self.last_public_write >= 30):
            return
        self.public = [a for a in self.public if now - a["t"] < self.public_age]
        doc = {"updated": int(now), "alerts": self.public, "gps": gps_status}
        os.makedirs(self.out_dir, exist_ok=True)
        write_json(os.path.join(self.out_dir, "alerts.json"), doc)
        if self.dirty:
            for k in [k for k, v in self.sent.items() if now - v >= self.dedupe]:
                del self.sent[k]
            write_json(self.state_path, {
                "sent": self.sent, "public": self.public,
                "gps_last_alert": self.gps.last_alert if self.gps else 0,
            }, mode=0o600)
        self.last_public_write = now
        self.dirty = False


PUBLIC_FIELDS = ("t", "kind", "hex", "callsign", "reg", "type", "type_name",
                 "squawk", "alt_ft", "title", "detail", "worldwide")


def public_view(alert):
    """Whitelist only. Never positions, distances, bearings, or AGL."""
    return {k: alert[k] for k in PUBLIC_FIELDS if alert.get(k) not in (None, "")}


# -------------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--config", default=CONFIG)
    p.add_argument("--aircraft", default=AIRCRAFT)
    p.add_argument("--location-file", default=LOCATION_FILE)
    p.add_argument("--state-dir", default=STATE_DIR)
    p.add_argument("--out-dir", default=OUT_DIR)
    p.add_argument("--tar-html", default=TAR_HTML)
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    args = p.parse_args(argv)

    os.umask(0o022)
    cfg = load_config(args.config)
    home = read_location(args.location_file)
    db = TarDb(args.tar_html)
    eng = Engine(cfg, db=db, home=home, state_dir=args.state_dir, out_dir=args.out_dir)
    poll = max(2.0, float(cfg["engine"]["poll_seconds"]))
    print("station-alerts: poll %ss, notifier %s, home %s, db %s" % (
        poll, eng.notifier.name, "set" if home else "not set",
        os.path.basename(db.path) if db.path else "none"), flush=True)
    last_mtime = 0
    while True:
        t0 = time.time()
        try:
            mt = os.path.getmtime(args.aircraft)
            if mt != last_mtime:
                last_mtime = mt
                doc = load_json(args.aircraft, None)
                if doc:
                    eng.step(doc, t0)
        except OSError:
            pass
        except Exception as ex:
            print("step failed: %r" % ex, file=sys.stderr, flush=True)
        if args.once:
            return 0
        time.sleep(max(1.0, poll - (time.time() - t0)))


if __name__ == "__main__":
    sys.exit(main())
