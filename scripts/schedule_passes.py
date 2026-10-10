#!/usr/bin/env python3
"""Schedule tonight's and tomorrow's good Meteor-M2-4 passes as one-shot systemd timers on the Pi.

Run once a day (it's idempotent: units are named by pass start time). Each timer calls record_pass.sh
for the pass length plus a minute of margin. Location comes from GS_LOCATION="lat,lon,meters".
"""
import os, subprocess, urllib.request
from datetime import timedelta
from skyfield.api import load, wgs84, EarthSatellite

MIN_EL = float(os.environ.get("GS_MIN_EL", 30))
HERE = os.path.dirname(os.path.abspath(__file__))
tle = urllib.request.urlopen("https://celestrak.org/NORAD/elements/gp.php?CATNR=59051&FORMAT=TLE", timeout=30).read().decode().split("\n")
ts = load.timescale(); sat = EarthSatellite(tle[1], tle[2], tle[0].strip(), ts)
lat, lon, alt = (float(x) for x in os.environ["GS_LOCATION"].split(","))
home = wgs84.latlon(lat, lon, elevation_m=alt)
t0 = ts.now(); t1 = ts.from_datetime(t0.utc_datetime() + timedelta(hours=36))
t, ev = sat.find_events(home, t0, t1, altitude_degrees=5)
p = {}
for ti, e in zip(t, ev):
    if e == 0: p = {"rise": ti}
    elif e == 1 and p: p["el"] = (sat - home).at(ti).altaz()[0].degrees
    elif e == 2 and "el" in p:
        if p["el"] >= MIN_EL:
            start = p["rise"].utc_datetime() - timedelta(seconds=30)
            secs = int((ti - p["rise"]) * 86400) + 60
            unit = "gs-pass-" + start.strftime("%Y%m%d%H%M")
            when = start.strftime("%Y-%m-%d %H:%M:%S UTC")
            subprocess.run(["systemd-run", "--user", "--unit", unit, "--on-calendar", when, "--timer-property=AccuracySec=1s",
                            "--setenv=GS_SAT_SERIAL=" + os.environ.get("GS_SAT_SERIAL", ""),
                            os.path.join(HERE, "record_pass.sh"), str(secs), f"el{p['el']:.0f}"], check=False)
            print(f"scheduled {unit} at {when} for {secs}s (peak {p['el']:.0f} deg)")
        p = {}
