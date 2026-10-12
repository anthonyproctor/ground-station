#!/usr/bin/env python3
"""Ground Station minute stats.

Runs once a minute from station-stats.timer. Reads readsb's aircraft.json,
keeps a small "today" tally in /var/lib/station-web, and writes the public
summary to /run/station-web/station.json for the home page.

Privacy: the public JSON never contains the receiver location, bearings, or
per-aircraft distances. If /etc/ground-station/location.env exists (root only,
GS_LOCATION="lat,lon,meters", same format as the pass scripts), it is used
here, privately, to work out one number: today's max range in miles.
"""
import glob
import gzip
import json
import math
import os
import re
import time
import urllib.request

AIRCRAFT = "/run/readsb/aircraft.json"
TAR_HTML = "/usr/local/share/tar1090/html"
STATE_DIR = "/var/lib/station-web"
OUT_DIR = "/run/station-web"
LOCATION_FILE = "/etc/ground-station/location.env"
MAX_PLAUSIBLE_NM = 400  # readsb runs with --max-range 450; ignore junk beyond this

# Denver weather: DIA METAR from aviationweather.gov. Their API sends no CORS
# header, so the browser can't fetch it; the Pi fetches it at most every 10
# minutes (well under their rate limits) and passes a small subset through.
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=KDEN&format=json"
METAR_EVERY = 600

AIRLINE_RE = re.compile(r"^([A-Z]{3})\d")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def gz_json(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def db_dir():
    dirs = sorted(glob.glob(os.path.join(TAR_HTML, "db-*")), key=os.path.getmtime)
    return dirs[-1] if dirs else None


class AircraftDb:
    """Offline lookups in tar1090's own aircraft database (no outside APIs)."""

    def __init__(self, path):
        self.path = path
        self.shards = {}

    def _shard(self, key):
        if key not in self.shards:
            try:
                self.shards[key] = gz_json(os.path.join(self.path, key + ".js"))
            except (OSError, ValueError):
                self.shards[key] = {}
        return self.shards[key]

    def lookup(self, hexid):
        hexid = hexid.upper()
        n = 1
        while n < len(hexid):
            data = self._shard(hexid[:n])
            rec = data.get(hexid[n:])
            if rec:
                return rec  # [registration, type, flags, description]
            children = data.get("children") or []
            if hexid[: n + 1] in children:
                n += 1
                continue
            return None
        return None


def type_counts(path):
    """Airframes per type across the whole db, cached per db version."""
    cache = os.path.join(STATE_DIR, "typecounts-%s.json" % os.path.basename(path))
    counts = load_json(cache, None)
    if counts is not None:
        return counts
    counts = {}
    for shard in glob.glob(os.path.join(path, "*.js")):
        name = os.path.basename(shard)[:-3]
        if not re.fullmatch(r"[0-9A-F]+", name):
            continue
        try:
            data = gz_json(shard)
        except (OSError, ValueError):
            continue
        for k, rec in data.items():
            if k == "children" or not isinstance(rec, list) or len(rec) < 2:
                continue
            t = rec[1]
            if t:
                counts[t] = counts.get(t, 0) + 1
    for old in glob.glob(os.path.join(STATE_DIR, "typecounts-*.json")):
        os.remove(old)
    write_json(cache, counts)
    return counts


def read_location():
    try:
        with open(LOCATION_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GS_LOCATION="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    lat, lon = (float(x) for x in val.split(",")[:2])
                    return lat, lon
    except (OSError, ValueError):
        pass
    return None


def dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def pi_health():
    out = {}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            out["temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/uptime") as f:
            out["uptime_s"] = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        pass
    return out


def denver_weather():
    cache = os.path.join(STATE_DIR, "metar.json")
    wx = load_json(cache, {})
    if time.time() - wx.get("fetched", 0) < METAR_EVERY:
        return wx.get("data")
    data = wx.get("data")
    try:
        req = urllib.request.Request(METAR_URL, headers={"User-Agent": "ground-station-hobby-page/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            m = json.load(r)[0]
        data = {
            "station": "KDEN",
            "name": "Denver International",
            "obs_time": m.get("obsTime"),
            "temp_c": m.get("temp"),
            "wind_dir": m.get("wdir"),
            "wind_kt": m.get("wspd"),
            "gust_kt": m.get("wgst"),
            "visib": m.get("visib"),
            "cover": m.get("cover"),
            "flt_cat": m.get("fltCat"),
            "wx": m.get("wxString"),
            "raw": m.get("rawOb"),
        }
    except Exception:
        pass  # keep the last good report
    write_json(cache, {"fetched": int(time.time()), "data": data})
    return data


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    state_path = os.path.join(STATE_DIR, "today.json")
    st = load_json(state_path, {})
    if st.get("date") != today:
        st = {"date": today, "seen": {}, "max_range_nm": 0, "highest": None}
    seen = st["seen"]  # hex -> [callsign, type]

    ac = load_json(AIRCRAFT, {}).get("aircraft", [])
    dbp = db_dir()
    db = AircraftDb(dbp) if dbp else None
    home = read_location()
    changed = False

    for a in ac:
        hexid = a.get("hex", "")
        if not hexid or hexid.startswith("~") or a.get("seen", 999) > 60:
            continue
        cs = (a.get("flight") or "").strip()
        rec = seen.get(hexid)
        if rec is None:
            t = a.get("t") or ""
            mil = False
            if db and not t:
                r = db.lookup(hexid)
                if r:
                    t = r[1] or ""
                    mil = (r[2] or "0")[:1] == "1"
            seen[hexid] = [cs, t, 1 if mil else 0]
            changed = True
        elif cs and not rec[0]:
            rec[0] = cs
            changed = True
        alt = a.get("alt_baro")
        if isinstance(alt, (int, float)) and a.get("seen", 999) < 30:
            hi = st.get("highest")
            if not hi or alt > hi[1]:
                st["highest"] = [cs or hexid.upper(), int(alt)]
                changed = True
        if home and "lat" in a and "lon" in a and a.get("seen_pos", 999) < 60:
            d = dist_nm(home[0], home[1], a["lat"], a["lon"])
            if st["max_range_nm"] < d <= MAX_PLAUSIBLE_NM:
                st["max_range_nm"] = round(d, 1)
                changed = True

    if changed or not os.path.exists(state_path):
        write_json(state_path, st)

    # summary
    airlines = {}
    types_today = {}
    mil = 0
    for cs, t, m in seen.values():
        mt = AIRLINE_RE.match(cs or "")
        if mt:
            airlines[mt.group(1)] = airlines.get(mt.group(1), 0) + 1
        if t:
            types_today[t] = types_today.get(t, 0) + 1
        mil += m

    top_airline = None
    if airlines:
        code, n = max(airlines.items(), key=lambda kv: kv[1])
        name = code
        if dbp:
            try:
                name = gz_json(os.path.join(dbp, "operators.js")).get(code, {}).get("n", code)
            except (OSError, ValueError):
                pass
        top_airline = {"code": code, "name": name, "count": n}

    rarest = None
    if types_today and dbp:
        counts = type_counts(dbp)
        known = [(counts.get(t, 0), t) for t in types_today if counts.get(t, 0) > 0]
        if known:
            n, t = min(known)
            desc = t
            try:
                tinfo = gz_json(os.path.join(dbp, "icao_aircraft_types2.js")).get(t)
                if isinstance(tinfo, list) and tinfo:
                    desc = tinfo[0]
            except (OSError, ValueError):
                pass
            rarest = {"type": t, "name": desc, "worldwide": n}

    out = {
        "updated": int(time.time()),
        "date": today,
        "seen_today": len(seen),
        "military_today": mil,
        "top_airline": top_airline,
        "rarest_type": rarest,
        "highest_today": {"callsign": st["highest"][0], "alt_ft": st["highest"][1]} if st.get("highest") else None,
        "max_range_mi": round(st["max_range_nm"] * 1.15078) if home and st["max_range_nm"] else None,
        "weather": denver_weather(),
        "pi": pi_health(),
    }
    write_json(os.path.join(OUT_DIR, "station.json"), out)


if __name__ == "__main__":
    main()
