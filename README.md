# PiAware Dashboard

A lightweight local dashboard for a Raspberry Pi running
[FlightAware PiAware](https://github.com/flightaware/piaware). It shows receiver
health, Raspberry Pi system status, network state, service status, ADS-B
activity, and a compact aircraft/range display.

The dashboard is designed for small PiAware stations: no Node.js, no database,
and no build step. It runs as a small Python standard-library web server and
serves static HTML/CSS/JavaScript. Optional map tiles are loaded by the viewing
browser, not by the Raspberry Pi.

![PiAware Dashboard screenshot](assets/dashboard-screenshot.jpg)

## Features

- Pi system health: load, memory, disk, CPU temperature, uptime.
- Network status: local interface, Tailscale address, Wi-Fi signal, and link rate.
- PiAware ADS-B data from `dump1090-fa` runtime JSON.
- Aircraft count, fresh positioned tracks, messages per second, range history,
  signal, noise, gain, peak signal, and an automatically scaled receiver-centered
  map plot.
- Live data age, API latency, model-aware temperature warnings, and Raspberry Pi
  throttling flags when `vcgencmd` is available.
- Systemd status for `dump1090-fa`, `piaware`, `fa-mlat-client`, and `lighttpd`.
- Links to the host device's SkyAware page, PiAware page, and aircraft JSON.
- Palantir-inspired operations-console visual design.

## Requirements

- Raspberry Pi running PiAware / `dump1090-fa`.
- Python 3.
- systemd.
- Existing PiAware web interface, usually available at:

```text
http://<your-pi-address>/
http://<your-pi-address>/skyaware/
```

## Quick Install

On your Raspberry Pi:

```bash
git clone https://github.com/B1ackCat7/PiAware-Dashboard.git
cd PiAware-Dashboard
sudo ./install.sh
```

Then open:

```text
http://<your-pi-address>:8088/
```

The service uses port `8088` by default, so it does not replace or modify the
existing PiAware/SkyAware interface on port `80`.

## Manual Run

For development or testing:

```bash
python3 -B server.py
```

Then open:

```text
http://127.0.0.1:8088/
```

On a non-PiAware machine, production mode reports that receiver data is
unavailable. This prevents a real receiver failure from looking healthy.

To force sanitized demo data for screenshots or local previews:

```bash
PIAWARE_DASHBOARD_DEMO=1 python3 -B server.py
```

Process liveness and receiver readiness are separate endpoints:

```text
http://<your-pi-address>:8088/healthz
http://<your-pi-address>:8088/readyz
```

`/healthz` confirms the dashboard process is serving requests. `/readyz`
returns HTTP 503 when required PiAware services or fresh receiver data are not
available.

## Service Commands

```bash
sudo systemctl status piaware-dashboard.service
sudo systemctl restart piaware-dashboard.service
sudo systemctl stop piaware-dashboard.service
```

Logs:

```bash
journalctl -u piaware-dashboard.service -f
```

## Configuration

The service file sets:

```text
PIAWARE_DASHBOARD_HOST=0.0.0.0
PIAWARE_DASHBOARD_PORT=8088
```

The installer creates a restricted `piaware-dashboard` system user. The
systemd unit runs without root privileges and enables read-only filesystem and
process hardening.

To change the port, edit:

```text
/etc/systemd/system/piaware-dashboard.service
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart piaware-dashboard.service
```

### Map Tiles

The center range display can show a greyscale base map behind the aircraft plot.
It is centered automatically from the receiver latitude/longitude reported by
`dump1090-fa`, so each installation uses that station's own location.

By default, browsers load public OpenStreetMap raster tiles:

```text
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

To use a local or custom tile server, set:

```text
PIAWARE_DASHBOARD_TILE_URL=http://<tile-server>/{z}/{x}/{y}.png
```

To disable the base map and keep only the radar-style plot:

```text
PIAWARE_DASHBOARD_TILE_URL=none
```

Custom tile providers can set visible attribution and its destination:

```text
PIAWARE_DASHBOARD_TILE_ATTRIBUTION=© Example Maps
PIAWARE_DASHBOARD_TILE_ATTRIBUTION_URL=https://example.com/attribution
```

Temperature thresholds can be overridden when the model-aware defaults do not
fit a station's enclosure or cooling setup:

```text
PIAWARE_DASHBOARD_TEMP_WARNING=70
PIAWARE_DASHBOARD_TEMP_CRITICAL=80
```

## Uninstall

From the cloned repo:

```bash
sudo ./uninstall.sh
```

## Data Sources

The server reads:

```text
/run/dump1090-fa/aircraft.json
/run/dump1090-fa/stats.json
/run/dump1090-fa/receiver.json
```

It also reads standard Linux system files and `systemctl` status for local
machine health.

## Tests

Run the standard-library test suite and syntax checks from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py
node --check static/app.js
sh -n install.sh uninstall.sh
```

## Privacy

The dashboard API does not send receiver data anywhere. Everything is served
locally from the Raspberry Pi. If the default base map is enabled, the browser
viewing the dashboard requests map tiles from OpenStreetMap for the receiver's
general area. Set `PIAWARE_DASHBOARD_TILE_URL=none` or point it at a local tile
server for fully offline operation.
