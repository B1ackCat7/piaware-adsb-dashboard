# PiAware ADS-B Dashboard for Raspberry Pi

**A lightweight, self-hosted operations dashboard for FlightAware PiAware and
`dump1090-fa` receivers.**

Monitor live aircraft, receiver range, ADS-B message rates, signal quality,
Wi-Fi health, Raspberry Pi resources, and essential services from one
responsive local web interface.

[![Python 3](https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-PiAware-C51A4A?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.com/)
[![ADS--B](https://img.shields.io/badge/ADS--B-receiver%20monitoring-2563EB)](https://en.wikipedia.org/wiki/Automatic_Dependent_Surveillance%E2%80%93Broadcast)
[![Dependencies](https://img.shields.io/badge/third--party%20dependencies-none-2EA44F)](#how-it-works)

![PiAware ADS-B receiver dashboard showing aircraft, range, signal, network, and Raspberry Pi health](assets/dashboard-screenshot.jpg)

## Why use this dashboard?

PiAware and SkyAware are excellent for aircraft tracking. This companion
dashboard focuses on the receiver itself: whether data is fresh, services are
healthy, the Pi is running safely, and the network is reliable.

- **Fast and lightweight:** Python standard library plus static HTML, CSS, and
  JavaScript—no Node.js, database, containers, or build step.
- **Receiver-focused:** aircraft activity, positioned tracks, message and
  position rates, reception range, gain, signal, noise, and peak signal.
- **Pi health at a glance:** CPU temperature, throttling, load, memory, disk,
  uptime, Wi-Fi signal, and link rate.
- **Honest failure states:** production errors are shown as unavailable or
  stale instead of being replaced with demo data.
- **Safe service design:** runs as a restricted system user with systemd
  hardening and separate liveness/readiness endpoints.
- **Works alongside PiAware:** uses port `8088` and does not replace the
  standard PiAware or SkyAware interface.

## Features

### ADS-B receiver monitoring

- Live data from PiAware / `dump1090-fa` runtime JSON.
- Aircraft count, fresh positioned tracks, message rate, and position rate.
- Automatically scaled receiver-centered aircraft plot and range history.
- Aircraft flight, hex code, altitude, track, RSSI, distance, and last-seen
  details.
- Gain, signal, noise, and peak-signal monitoring.
- Data-age and API-latency indicators for detecting a slow or stale feed.

### Raspberry Pi monitoring

- CPU load, memory, disk usage, uptime, and temperature.
- Model-aware temperature warnings with optional custom thresholds.
- Raspberry Pi under-voltage and throttling flags through `vcgencmd`.
- Local network and Tailscale addresses.
- Wi-Fi connection, signal strength, and transmit link rate.

### Service and operations monitoring

- Required service state for `dump1090-fa` and `piaware`.
- Optional service state for `fa-mlat-client` and `lighttpd`.
- Links to SkyAware, PiAware, and the local aircraft JSON feed.
- `/healthz` process-liveness and `/readyz` receiver-readiness endpoints.
- Timeout, non-overlapping refresh scheduling, and automatic retry backoff.
- Compressed API responses and an accessible, responsive interface.

## Quick start

### Requirements

- Raspberry Pi running FlightAware PiAware and `dump1090-fa`.
- Python 3.
- systemd.
- Git for installation from GitHub.

The standard PiAware interfaces are usually available at:

```text
http://<your-pi-address>/
http://<your-pi-address>/skyaware/
```

### Install

Run on the Raspberry Pi:

```bash
git clone https://github.com/B1ackCat7/PiAware-Dashboard.git
cd PiAware-Dashboard
sudo ./install.sh
```

Open the dashboard:

```text
http://<your-pi-address>:8088/
```

The installer:

1. creates a restricted `piaware-dashboard` system account;
2. installs the application under `/opt/piaware-dashboard`;
3. installs and enables `piaware-dashboard.service`;
4. starts the dashboard and verifies its health endpoint.

## How it works

The server reads PiAware receiver data directly from:

```text
/run/dump1090-fa/aircraft.json
/run/dump1090-fa/stats.json
/run/dump1090-fa/receiver.json
```

It also reads standard Linux system information, checks local services with
`systemctl`, and serves a small JSON API plus static frontend files. The
browser refreshes the dashboard every five seconds.

```text
PiAware / dump1090-fa JSON
            │
            ▼
    Python dashboard server
       localhost:8088
            │
            ▼
  HTML + CSS + JavaScript UI
```

## Service management

```bash
sudo systemctl status piaware-dashboard.service
sudo systemctl restart piaware-dashboard.service
sudo systemctl stop piaware-dashboard.service
```

Follow service logs:

```bash
journalctl -u piaware-dashboard.service -f
```

Check process health and receiver readiness:

```text
http://<your-pi-address>:8088/healthz
http://<your-pi-address>:8088/readyz
```

- `/healthz` returns success when the dashboard process is serving requests.
- `/readyz` returns HTTP 503 when required PiAware services are unavailable or
  receiver data is stale.

## Configuration

The default systemd service configuration is:

```text
PIAWARE_DASHBOARD_HOST=0.0.0.0
PIAWARE_DASHBOARD_PORT=8088
```

To change the port, edit
`/etc/systemd/system/piaware-dashboard.service`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart piaware-dashboard.service
```

### Temperature thresholds

The dashboard selects model-aware defaults. Override them when needed for a
particular enclosure or cooling setup:

```text
PIAWARE_DASHBOARD_TEMP_WARNING=70
PIAWARE_DASHBOARD_TEMP_CRITICAL=80
```

### Map tiles

The receiver-centered range display can show a greyscale base map. By default,
the viewing browser loads OpenStreetMap raster tiles:

```text
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

Use a local or custom tile provider:

```text
PIAWARE_DASHBOARD_TILE_URL=http://<tile-server>/{z}/{x}/{y}.png
```

Disable map tiles for fully offline operation:

```text
PIAWARE_DASHBOARD_TILE_URL=none
```

Custom providers can supply visible attribution:

```text
PIAWARE_DASHBOARD_TILE_ATTRIBUTION=© Example Maps
PIAWARE_DASHBOARD_TILE_ATTRIBUTION_URL=https://example.com/attribution
```

## Development and demo mode

Run locally:

```bash
python3 -B server.py
```

Then open `http://127.0.0.1:8088/`.

On a machine without PiAware, production mode reports receiver data as
unavailable. Use sanitized demo data for development or screenshots:

```bash
PIAWARE_DASHBOARD_DEMO=1 python3 -B server.py
```

## Testing

Run the standard-library test suite and syntax checks:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile server.py
node --check static/app.js
sh -n install.sh uninstall.sh
```

## Privacy and security

- Receiver and system data stay on the Raspberry Pi and are not sent to an
  external application server.
- The dashboard service runs without root privileges and uses systemd sandbox
  protections.
- The API does not silently substitute demo values for failed production data.
- When the default map is enabled, the viewing browser requests map tiles for
  the receiver's general area from OpenStreetMap.
- Set `PIAWARE_DASHBOARD_TILE_URL=none` or use a local tile server for fully
  offline operation.
- Because the dashboard displays local receiver and network information, expose
  port `8088` only to networks and users you trust.

## Uninstall

From the cloned repository:

```bash
sudo ./uninstall.sh
```

## Project scope

This is an independent community dashboard for local PiAware installations. It
is not an official FlightAware product and is not affiliated with or endorsed
by FlightAware.

Related technologies: ADS-B, PiAware, dump1090-fa, SkyAware, FlightAware,
Raspberry Pi, RTL-SDR, aircraft tracking, receiver monitoring, and self-hosted
aviation software.
