# Ground Station

A home weather-satellite and aircraft ground station.

- **Planes:** an ADS-B receiver (1090 MHz) on a Raspberry Pi 3 decodes aircraft broadcasts into a live map, using readsb and tar1090.
- **Satellites:** an RTL-SDR plus a 137 MHz filtered LNA on a Raspberry Pi 5 receives Meteor-M2-4 LRPT passes. SatDump decodes them into images.
- **Pass prediction:** `scripts/passes.py` reads the current TLE from CelesTrak and lists the next week of passes above 30 degrees. It uses Skyfield.

Status: build in progress, October 2026. The NOAA-15, -18 and -19 APT satellites were decommissioned in 2025, so this station targets Meteor LRPT.

Location comes from the `GS_LOCATION` environment variable ("lat,lon,meters") and is never committed.

## Station layout (Pi 3, two radios)

| Radio | Job | Software |
|---|---|---|
| AirNav FlightStick (1090 MHz, filtered) | Aircraft, 24/7 | readsb + tar1090 |
| RTL-SDR Blog V3 + SAWbird+ NOAA LNA | Meteor-M2-4 passes | `schedule_passes.py` sets systemd timers, `record_pass.sh` saves raw IQ, SatDump decodes on a Mac |

The Pi 3 records satellite passes but doesn't decode them live, because it's too slow for real-time LRPT. Raw recordings are about 1 GB per 10-minute pass.
