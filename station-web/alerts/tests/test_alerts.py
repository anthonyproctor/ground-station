"""Synthetic tests for station-alerts.py. No live data, no network.

    python3 -m unittest discover -s station-web/alerts/tests -v
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
spec = importlib.util.spec_from_file_location("station_alerts", os.path.join(HERE, "..", "station-alerts.py"))
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)

T0 = 1_800_000_000.0


class FakeDb:
    """Stands in for the tar1090 db: hex -> (reg, type, military)."""
    INFO = {
        "ae0001": ("05-0001", "C17", True),
        "a00003": ("N9FX", "B764", False),
        "a00004": ("N500XX", "GLF6", False),
        "a00020": ("N572UP", "B744", False),
        "a00022": ("N573UP", "B744", False),
        "a00023": ("N574UP", "B744", False),
        "a00024": ("N575UP", "B744", False),
        "a00025": ("N135HX", "EC35", False),
        "ae68f1": ("07-0779", "PC12", True),
    }
    COUNTS = {"C17": 280, "B764": 18, "GLF6": 600, "B738": 7000, "B744": 400, "EC35": 1200}
    DESC = {"EC35": "H2T", "B744": "L4J", "B738": "L2J", "C17": "L4J"}
    OPS = {"UPS": "United Parcel Service"}
    NAMES = {"B764": "BOEING 767-400", "B744": "BOEING 747-400", "PC12": "PILATUS PC-12", "C17": "BOEING C-17 Globemaster 3"}

    def maybe_refresh(self):
        pass

    def info(self, h):
        return self.INFO.get(h, ("", "B738", False))

    def worldwide(self, t):
        return self.COUNTS.get(t, 0)

    def type_name(self, t):
        return self.NAMES.get(t, t)

    def desc(self, t):
        return self.DESC.get(t, "")

    def operator(self, cs):
        return self.OPS.get(cs[:3], "")


class Capture(sa.Notifier):
    name = "capture"

    def __init__(self):
        super().__init__({})
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def fx(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state")
        self.out = os.path.join(self.tmp, "out")
        self.cfg = sa.load_config(os.path.join(self.tmp, "missing.conf"))
        self.home = sa.read_location(os.path.join(FIX, "fake-location.env"))
        self.cap = Capture()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def engine(self, **kw):
        return sa.Engine(self.cfg, db=FakeDb(), home=self.home, state_dir=self.state,
                         out_dir=self.out, notifier=self.cap, now=T0, **kw)

    def public(self):
        with open(os.path.join(self.out, "alerts.json")) as f:
            return f.read()

    def private(self):
        with open(os.path.join(self.state, "alerts.jsonl")) as f:
            return [json.loads(x) for x in f]


class TestEmergency(Base):
    def test_7700_needs_two_polls_then_dedupes(self):
        e = self.engine()
        doc = fx("squawk7700.json")
        out, _ = e.step(doc, T0)
        self.assertEqual([a for a in out if a["kind"] == "emergency"], [])
        out, _ = e.step(doc, T0 + 8)
        em = [a for a in out if a["kind"] == "emergency"]
        self.assertEqual(len(em), 1)
        self.assertEqual(em[0]["hex"], "a1b2c3")
        self.assertIn("7700", em[0]["title"])
        self.assertIn("UAL123", em[0]["title"])
        out, _ = e.step(doc, T0 + 16)
        self.assertEqual([a for a in out if a["kind"] == "emergency"], [])
        self.assertIn('"kind":"emergency"', self.public())
        # after the de-dup window it can alert again
        again = []
        for dt in (20, 28):
            out, _ = e.step(doc, T0 + 4 * 3600 + dt)
            again += [a for a in out if a["kind"] == "emergency"]
        self.assertEqual(len(again), 1)

    def test_one_garbled_poll_does_not_alert(self):
        e = self.engine()
        e.step(fx("squawk7700.json"), T0)
        e.step({"aircraft": []}, T0 + 8)
        out, _ = e.step(fx("squawk7700.json"), T0 + 16)
        self.assertEqual([a for a in out if a["kind"] == "emergency"], [])

    def test_emergency_field_and_lifeguard_ignored(self):
        e = self.engine()
        e.step(fx("emergency_field.json"), T0)
        out, _ = e.step(fx("emergency_field.json"), T0 + 8)
        em = [a for a in out if a["kind"] == "emergency"]
        self.assertEqual([a["hex"] for a in em], ["a1b2c4"])
        self.assertIn("no radio", em[0]["title"])


class TestMilitaryRareHigh(Base):
    def test_military_db_flag_and_dbflags(self):
        out, _ = self.engine().step(fx("military.json"), T0)
        mil = sorted(a["hex"] for a in out if a["kind"] == "military")
        self.assertEqual(mil, ["a77777", "ae0001"])

    def test_rare_and_high(self):
        out, _ = self.engine().step(fx("rare_high.json"), T0)
        kinds = {(a["kind"], a["hex"]) for a in out}
        self.assertIn(("rare", "a00003"), kinds)
        self.assertIn(("high", "a00004"), kinds)
        self.assertNotIn(("rare", "a00004"), kinds)  # 600 worldwide > 50
        rare = [a for a in out if a["kind"] == "rare"][0]
        self.assertEqual(rare["worldwide"], 18)

    def test_rare_once_per_type(self):
        FakeDb.INFO["a00099"] = ("N99FX", "B764", False)
        doc = fx("rare_high.json")
        doc["aircraft"].append(dict(hex="a00099", type="adsb_icao", seen=1, alt_baro=30000))
        out, _ = self.engine().step(doc, T0)
        del FakeDb.INFO["a00099"]
        self.assertEqual(len([a for a in out if a["kind"] == "rare"]), 1)

    def test_rare_threshold_configurable(self):
        self.cfg["rare"]["max_worldwide"] = "10"
        out, _ = self.engine().step(fx("rare_high.json"), T0)
        self.assertFalse([a for a in out if a["kind"] == "rare"])


class TestPlacesAndWaiting(Base):
    def test_nearest_place(self):
        self.assertEqual(sa.nearest_place(39.40, -104.85, 30), "Castle Rock")
        self.assertEqual(sa.nearest_place(39.85, -104.68, 30), "DIA")
        self.assertIsNone(sa.nearest_place(10.0, 20.0, 30))

    def test_military_text_has_callsign_type_alt_place(self):
        out, _ = self.engine().step(fx("military_withpos.json"), T0)
        m = [a for a in out if a["kind"] == "military"][0]
        self.assertEqual(m["title"], "Military: DAGGR44 \u00b7 PILATUS PC-12 (07-0779) \u00b7 8,400 ft near Castle Rock")
        pub = json.loads(self.public())["alerts"][0]
        self.assertIn("near Castle Rock", pub["title"])
        self.assertNotIn("home_rel", pub)
        self.assertNotIn("home", self.public())
        self.assertIn("of home", m["home_rel"])  # phone-only field

    def test_text_has_heading_and_distance_from_town(self):
        out, _ = self.engine().step(fx("military_heading.json"), T0)
        m = [a for a in out if a["kind"] == "military"][0]
        self.assertIn("heading NE", m["title"])
        self.assertRegex(m["title"], r"\d+ mi [NESW]+ of [A-Za-z ]+$")

    def test_place_phrase(self):
        self.assertEqual(sa.place_phrase(39.40, -104.85, 30), "near Castle Rock")
        self.assertIn(" mi ", sa.place_phrase(39.62, -104.62, 30))
        self.assertEqual(sa.place_phrase(10.0, 20.0, 30), "")

    def test_waits_for_position_then_sends(self):
        e = self.engine()
        out, _ = e.step(fx("military_nopos.json"), T0)
        self.assertFalse([a for a in out if a["kind"] == "military"])  # held
        out, _ = e.step(fx("military_nopos.json"), T0 + 60)
        self.assertFalse([a for a in out if a["kind"] == "military"])
        out, _ = e.step(fx("military_withpos.json"), T0 + 120)
        m = [a for a in out if a["kind"] == "military"]
        self.assertEqual(len(m), 1)
        self.assertIn("near Castle Rock", m[0]["title"])
        self.assertNotIn("no_position", m[0])
        out, _ = e.step(fx("military_withpos.json"), T0 + 400)
        self.assertFalse([a for a in out if a["kind"] == "military"])  # de-dup still holds

    def test_never_gets_position_sent_at_end_marked(self):
        e = self.engine()
        e.step(fx("military_nopos.json"), T0)
        out, _ = e.step({"aircraft": []}, T0 + 200)  # gone quiet, still waiting
        self.assertFalse([a for a in out if a["kind"] == "military"])
        out, _ = e.step({"aircraft": []}, T0 + 301)
        m = [a for a in out if a["kind"] == "military"]
        self.assertEqual(len(m), 1)
        self.assertTrue(m[0]["no_position"])
        self.assertIn("no position yet", m[0]["title"])
        self.assertIn("DAGGR44", m[0]["title"])

    def test_emergency_never_waits(self):
        e = self.engine()
        e.step(fx("squawk7700.json"), T0)  # fixture has no position
        out, _ = e.step(fx("squawk7700.json"), T0 + 8)
        em = [a for a in out if a["kind"] == "emergency"]
        self.assertEqual(len(em), 1)
        self.assertIn("no position yet", em[0]["title"])


class TestLowHomePrivate(Base):
    def test_low_near_home_is_private_only(self):
        out, _ = self.engine().step(fx("low_home.json"), T0)
        low = [a for a in out if a["kind"] == "low_home"]
        self.assertEqual([a["hex"] for a in low], ["a00010"])  # far + high ones skipped
        self.assertTrue(low[0]["private"])
        self.assertLess(low[0]["agl_ft"], 2000)
        pub = self.public()
        self.assertNotIn("low_home", pub)
        self.assertNotIn("a00010", pub)
        self.assertIn("low_home", [a["kind"] for a in self.private()])
        self.assertIn("low_home", [a["kind"] for a in self.cap.sent])  # notifier still gets it

    def test_no_home_no_low_alerts(self):
        self.home = None
        out, _ = self.engine().step(fx("low_home.json"), T0)
        self.assertFalse([a for a in out if a["kind"] == "low_home"])

    def test_public_file_has_no_positions(self):
        e = self.engine()
        for name in ("low_home.json", "military.json", "rare_high.json", "gps_good.json"):
            e.step(fx(name), T0)
        e.step(fx("gps_degraded.json"), T0 + 10)
        pub = self.public()
        doc = json.loads(pub)
        for a in doc["alerts"]:
            for k in ("lat", "lon", "dist_nm", "agl_ft", "gpsOkLat", "gpsOkLon", "private"):
                self.assertNotIn(k, a)
        for s in ("10.0", "20.0", "10.01", "20.01", "10.2", "20.2"):
            self.assertNotIn(s, pub)


class TestGps(Base):
    def test_degraded_cluster_raises_one_regional_alert_then_clears(self):
        e = self.engine()
        e.step(fx("gps_good.json"), T0)
        e.step(fx("gps_good.json"), T0 + 8)
        out, st = e.step(fx("gps_degraded.json"), T0 + 16)
        gps = [a for a in out if a["kind"] == "gps"]
        self.assertEqual(len(gps), 1)
        self.assertEqual(gps[0]["degraded"], 5)  # 4 low NACp/NIC + 1 gpsOkBefore; OLDGPS excluded
        self.assertEqual(st["state"], "interference")
        self.assertNotIn("lat", json.dumps(gps[0]))
        out, _ = e.step(fx("gps_degraded.json"), T0 + 24)
        self.assertFalse([a for a in out if a["kind"] == "gps"])
        # recovers: window (300 s) plus clear_after (600 s)
        t = T0 + 24
        cleared = []
        while t < T0 + 1300:
            t += 30
            out, st = e.step(fx("gps_good.json"), t)
            cleared += [a for a in out if a["kind"] == "gps_clear"]
        self.assertEqual(len(cleared), 1)
        self.assertEqual(st["state"], "ok")
        self.assertIn('"kind":"gps"', self.public())

    def test_always_bad_aircraft_never_count(self):
        e = self.engine()
        doc = fx("gps_good.json")
        for i in range(3):
            _, st = e.step(doc, T0 + i * 8)
        self.assertEqual(st["degraded"], 0)
        self.assertEqual(st["state"], "ok")

    def test_series_logged_per_minute(self):
        e = self.engine()
        for i in range(0, 200, 8):
            e.step(fx("gps_good.json"), T0 + i)
        with open(os.path.join(self.state, "gps-health.jsonl")) as f:
            rows = [json.loads(x) for x in f]
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), {"t", "n", "d"})


class TestNotifier(Base):
    def test_default_notifier_is_off(self):
        n = sa.make_notifier(self.cfg)
        self.assertEqual(n.name, "none")

    def test_hourly_cap_holds_back_but_emergency_goes_through(self):
        self.cfg["notify"]["max_per_hour"] = "2"
        e = self.engine()
        doc = {"aircraft": [dict(hex="ae%04x" % i, type="adsb_icao", seen=1, seen_pos=1, alt_baro=20000,
                                 lat=39.7, lon=-104.9) for i in range(5)]}
        FakeDb.INFO.update({"ae%04x" % i: ("", "C17", True) for i in range(5)})
        out, _ = e.step(doc, T0)
        self.assertEqual(len([a for a in out if a["kind"] == "military"]), 5)  # all logged
        self.assertEqual(len(self.cap.sent), 2)                                # 2 notified
        e.step(fx("squawk7700.json"), T0 + 8)
        e.step(fx("squawk7700.json"), T0 + 16)
        self.assertEqual(self.cap.sent[-1]["kind"], "emergency")  # exempt from the cap
        self.assertIn("+3 more held back", self.cap.sent[-1]["detail"])  # carries the count
        for i in range(5):
            del FakeDb.INFO["ae%04x" % i]

    def test_ntfy_message_shape(self):
        sent = {}

        class Resp:
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return b"{}"

        def fake_urlopen(req, timeout=0):
            sent["url"], sent["body"], sent["auth"] = req.full_url, json.loads(req.data), req.get_header("Authorization")
            return Resp()
        import urllib.request
        real, urllib.request.urlopen = urllib.request.urlopen, fake_urlopen
        try:
            n = sa.NtfyNotifier({"ntfy_url": "http://127.0.0.1:2586", "ntfy_topic": "gs-test",
                                 "ntfy_token": "tk_x", "map_url": "https://example.test/map"})
            n.send({"kind": "emergency", "hex": "a1b2c3", "title": "UAL123 squawk 7700", "detail": "x",
                    "home_rel": "12 nmi NE of home"})
        finally:
            urllib.request.urlopen = real
        self.assertEqual(sent["url"], "http://127.0.0.1:2586/")
        self.assertIn("(12 nmi NE of home)", sent["body"]["message"])
        self.assertEqual(sent["body"]["topic"], "gs-test")
        self.assertEqual(sent["body"]["priority"], 5)
        self.assertEqual(sent["body"]["click"], "https://example.test/map/?icao=a1b2c3&zoom=11")
        self.assertEqual(sent["auth"], "Bearer tk_x")

    def test_state_survives_restart(self):
        e = self.engine()
        e.step(fx("military.json"), T0)
        e2 = self.engine()
        out, _ = e2.step(fx("military.json"), T0 + 60)
        self.assertFalse([a for a in out if a["kind"] == "military"])


DAY = T0            # 2027-01-15 08:00 UTC = mid-morning at the fake home (20 E)
NIGHT = T0 + 12 * 3600


class TestLookup(Base):
    def lookups(self, out):
        return {a["hex"]: a for a in out if a["kind"] == "lookup"}

    def test_sun_elevation_sane(self):
        self.assertGreater(sa.sun_elevation(10.0, 20.0, DAY), 20)
        self.assertLess(sa.sun_elevation(10.0, 20.0, NIGHT), -6)
        # Denver solar noon in June: high sun; midnight: far below
        self.assertGreater(sa.sun_elevation(39.74, -104.99, 1781719200), 65)  # 2026-06-17 18:00Z
        self.assertLess(sa.sun_elevation(39.74, -104.99, 1781762400), -20)    # 2026-06-18 06:00Z

    def test_inbound_heavy_in_daylight_fires_with_direction_and_eta(self):
        out, _ = self.engine().step(fx("lookup.json"), DAY)
        lk = self.lookups(out)
        self.assertEqual(sorted(lk), ["a00020", "a00025"])  # heavy + helicopter only
        h = lk["a00020"]
        self.assertTrue(h["private"])
        self.assertEqual(h["look"], "SW")
        self.assertEqual(h["elev_deg"], 12)          # atan(4000 ft / 3 nmi)
        self.assertAlmostEqual(h["eta_min"], 1.0, delta=0.1)  # 3 nmi at 180 kt
        self.assertIn("Look SW, about 12\u00b0 up: BOEING 747-400 (UPS123, United Parcel Service)", h["title"])
        self.assertIn("closest in about 1 min", h["detail"])
        self.assertIn("3.0 nmi", h["detail"])
        heli = lk["a00025"]
        self.assertEqual(heli["why"], "helicopter")
        self.assertIn("overhead now", heli["detail"])
        self.assertIn("lookup", [a["kind"] for a in self.cap.sent])

    def test_same_at_night_does_not_fire(self):
        out, _ = self.engine().step(fx("lookup.json"), NIGHT)
        self.assertEqual(self.lookups(out), {})

    def test_never_in_public_file(self):
        e = self.engine()
        e.step(fx("lookup.json"), DAY)
        pub = self.public()
        self.assertNotIn("lookup", pub)
        self.assertNotIn("Look ", pub)
        for h in ("a00020", "a00025"):
            self.assertNotIn(h, pub)
        self.assertIn("lookup", [a["kind"] for a in self.private()])

    def test_dedupe_two_hours(self):
        e = self.engine()
        e.step(fx("lookup.json"), DAY)
        out, _ = e.step(fx("lookup.json"), DAY + 3600)
        self.assertEqual(self.lookups(out), {})
        out, _ = e.step(fx("lookup.json"), DAY + 2 * 3600 + 10)
        self.assertEqual(sorted(self.lookups(out)), ["a00020", "a00025"])

    def test_counts_toward_cap_but_emergency_still_goes(self):
        self.cfg["notify"]["max_per_hour"] = "1"
        e = self.engine()
        e.step(fx("lookup.json"), DAY)
        self.assertEqual(len(self.cap.sent), 1)          # second lookup held back
        e.step(fx("squawk7700.json"), DAY + 8)
        e.step(fx("squawk7700.json"), DAY + 16)
        self.assertEqual(self.cap.sent[-1]["kind"], "emergency")

    def test_no_home_no_lookup(self):
        self.home = None
        out, _ = self.engine().step(fx("lookup.json"), DAY)
        self.assertEqual(self.lookups(out), {})


class TestSilent(Base):
    def run3(self, name, n=3):
        e = self.engine()
        allout = []
        for i in range(n):
            out, _ = e.step(fx(name), T0 + i * 2)
            allout += out
        return [a for a in allout if a["kind"] == "silent"], e

    def test_close_mode_s_only_is_logged_after_confirmation(self):
        e = self.engine()
        for i in range(2):
            out, _ = e.step(fx("silent_close.json"), T0 + i * 2)
            self.assertFalse([a for a in out if a["kind"] == "silent"])  # not yet
        out, _ = e.step(fx("silent_close.json"), T0 + 4)
        s = [a for a in out if a["kind"] == "silent"]
        self.assertEqual(len(s), 1)
        self.assertTrue(s[0]["private"])
        self.assertIn("Loud and close", s[0]["title"])
        self.assertEqual(s[0]["rssi"], -6.0)

    def test_weak_high_positioned_or_adsb_do_not_trigger(self):
        for name in ("silent_far.json", "silent_high.json", "silent_haspos.json", "silent_adsb.json"):
            s, _ = self.run3(name)
            self.assertFalse(s, name)

    def test_never_public_and_never_notified_by_default(self):
        s, e = self.run3("silent_close.json")
        self.assertEqual(len(s), 1)
        self.assertNotIn("Loud and close", self.public())
        self.assertNotIn("silent", e.notify_kinds)

    def test_deduped_for_the_window(self):
        e = self.engine()
        n = 0
        for i in range(10):
            out, _ = e.step(fx("silent_close.json"), T0 + i * 2)
            n += len([a for a in out if a["kind"] == "silent"])
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
