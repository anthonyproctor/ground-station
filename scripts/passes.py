from skyfield.api import load, wgs84, EarthSatellite
from datetime import timedelta
import urllib.request, zoneinfo
tz=zoneinfo.ZoneInfo("America/Denver")
tle=urllib.request.urlopen("https://celestrak.org/NORAD/elements/gp.php?CATNR=59051&FORMAT=TLE",timeout=20).read().decode().strip().splitlines()
ts=load.timescale(); sat=EarthSatellite(tle[1],tle[2],tle[0].strip(),ts)
import os
lat,lon,alt=(float(x) for x in os.environ["GS_LOCATION"].split(","))  # "lat,lon,meters", kept out of the repo
home=wgs84.latlon(lat,lon,elevation_m=alt)
t0=ts.now(); t1=ts.from_datetime(t0.utc_datetime()+timedelta(days=7))
t,ev=sat.find_events(home,t0,t1,altitude_degrees=10)
rows=[];cur={}
for ti,e in zip(t,ev):
    if e==0: cur={"rise":ti}
    elif e==1 and cur: cur["max"]=ti; alt,az,_=(sat-home).at(ti).altaz(); cur["el"]=alt.degrees
    elif e==2 and "max" in cur:
        cur["set"]=ti; rows.append(cur); cur={}
print(f"# {tle[0].strip()}  TLE epoch {sat.epoch.utc_strftime('%Y-%m-%d')}")
for r in rows:
    q="GREAT" if r["el"]>=50 else "good" if r["el"]>=30 else "low"
    if q=="low": continue
    a=r["rise"].astimezone(tz); m=r["max"].astimezone(tz); s=r["set"].astimezone(tz)
    d=(r["set"]-r["rise"])*24*60
    print(f"{a:%a %m/%d}  {a:%-I:%M %p} to {s:%-I:%M %p}  peak {m:%-I:%M %p} at {r['el']:.0f}°  ({d:.0f} min)  {q}")
