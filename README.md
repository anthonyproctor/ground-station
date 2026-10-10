# Ground Station

A home weather-satellite and aircraft ground station.

- **Planes:** an ADS-B receiver (1090 MHz) on a Raspberry Pi 3 decodes aircraft broadcasts into a live map, using readsb and tar1090.
- **Satellites:** an RTL-SDR plus a 137 MHz filtered LNA on a Raspberry Pi 5 receives Meteor-M2-4 LRPT passes. SatDump decodes them into images.
- **Pass prediction:** `scripts/passes.py` reads the current TLE from CelesTrak and lists the next week of passes above 30 degrees. It uses Skyfield.

Status: build in progress, October 2026. The NOAA-15, -18 and -19 APT satellites were decommissioned in 2025, so this station targets Meteor LRPT.

Location comes from the `GS_LOCATION` environment variable ("lat,lon,meters") and is never committed.
