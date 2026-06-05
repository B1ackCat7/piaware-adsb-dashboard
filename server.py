#!/usr/bin/env python3
"""Lightweight PiAware operations dashboard server.

The server intentionally uses only Python's standard library so it can run on a
32-bit Raspberry Pi OS install without npm, pip, or a build step.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DUMP1090_ROOTS = [Path("/run/dump1090-fa"), Path("/var/run/dump1090-fa")]
SERVICES = ["dump1090-fa", "piaware", "fa-mlat-client", "lighttpd"]
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def demo_mode_enabled() -> bool:
    return os.environ.get("PIAWARE_DASHBOARD_DEMO", "").lower() in {"1", "true", "yes", "on"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def dump1090_root() -> Path | None:
    for root in DUMP1090_ROOTS:
        if (root / "aircraft.json").exists():
            return root
    return None


def read_number(path: str) -> float | None:
    try:
        return float(Path(path).read_text(encoding="utf-8").strip())
    except Exception:
        return None


def load_average() -> dict[str, float]:
    try:
        one, five, fifteen, *_ = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        return {"one": float(one), "five": float(five), "fifteen": float(fifteen)}
    except Exception:
        return {"one": 0.18, "five": 0.22, "fifteen": 0.19}


def memory_status() -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(total - available, 0)
        percent = round((used / total) * 100, 1) if total else 0
        return {"total": total, "used": used, "available": available, "percent": percent}
    except Exception:
        return {"total": 1024**3, "used": 318 * 1024**2, "available": 706 * 1024**2, "percent": 31.0}


def disk_status() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    percent = round((usage.used / usage.total) * 100, 1)
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}


def uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except Exception:
        return 417600.0


def temperature_c() -> float | None:
    raw = read_number("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return 51.1
    return round(raw / 1000, 1)


def network_status() -> dict[str, Any]:
    interfaces: list[dict[str, Any]] = []
    try:
        output = subprocess.check_output(["ip", "-brief", "addr"], text=True, timeout=2)
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            state = parts[1]
            addresses = [p for p in parts[2:] if "/" in p]
            interfaces.append({"name": name, "state": state, "addresses": addresses[:3]})
    except Exception:
        interfaces = [
            {"name": "wlan0", "state": "UP", "addresses": ["192.168.1.100/24"]},
            {"name": "tailscale0", "state": "UNKNOWN", "addresses": ["100.64.0.10/32"]},
        ]
    return {"hostname": socket.gethostname(), "interfaces": interfaces}


def service_status() -> list[dict[str, str]]:
    if demo_mode_enabled():
        return [
            {"name": "dump1090-fa", "state": "active"},
            {"name": "piaware", "state": "active"},
            {"name": "fa-mlat-client", "state": "active"},
            {"name": "lighttpd", "state": "active"},
        ]

    statuses: list[dict[str, str]] = []
    for service in SERVICES:
        try:
            state = subprocess.check_output(
                ["systemctl", "is-active", service],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
        except subprocess.CalledProcessError as exc:
            state = (exc.output or "inactive").strip()
        except Exception:
            state = "unknown"
        statuses.append({"name": service, "state": state or "unknown"})
    return statuses


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def format_aircraft(aircraft: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    lat = aircraft.get("lat")
    lon = aircraft.get("lon")
    distance = None
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        rlat = receiver.get("lat")
        rlon = receiver.get("lon")
        if isinstance(rlat, (int, float)) and isinstance(rlon, (int, float)):
            distance = round(haversine_nm(rlat, rlon, lat, lon), 1)

    return {
        "hex": aircraft.get("hex", "").upper(),
        "flight": (aircraft.get("flight") or aircraft.get("hex") or "UNKNOWN").strip(),
        "altitude": aircraft.get("alt_baro") or aircraft.get("alt_geom"),
        "speed": aircraft.get("gs"),
        "track": aircraft.get("track"),
        "rssi": aircraft.get("rssi"),
        "seen": aircraft.get("seen"),
        "seen_pos": aircraft.get("seen_pos"),
        "messages": aircraft.get("messages", 0),
        "lat": lat,
        "lon": lon,
        "distance_nm": distance,
    }


def history_points(root: Path | None, receiver: dict[str, Any]) -> list[dict[str, Any]]:
    if root is None:
        return demo_history()
    points: list[dict[str, Any]] = []
    files = sorted(root.glob("history_*.json"), key=lambda p: p.stat().st_mtime)[-48:]
    for path in files:
        data = read_json(path)
        aircraft = data.get("aircraft", [])
        if not isinstance(aircraft, list):
            aircraft = []
        positioned = [a for a in aircraft if isinstance(a, dict) and "lat" in a and "lon" in a]
        max_range = 0.0
        for item in positioned:
            formatted = format_aircraft(item, receiver)
            max_range = max(max_range, formatted.get("distance_nm") or 0)
        points.append(
            {
                "time": data.get("now") or path.stat().st_mtime,
                "aircraft": len(aircraft),
                "positioned": len(positioned),
                "max_range_nm": round(max_range, 1),
            }
        )
    return points or demo_history()


def adsb_status() -> dict[str, Any]:
    root = None if demo_mode_enabled() else dump1090_root()
    aircraft_json = read_json(root / "aircraft.json") if root else demo_aircraft_raw()
    stats_json = read_json(root / "stats.json") if root else demo_stats()
    receiver_json = read_json(root / "receiver.json") if root else {"lat": 39.0, "lon": -95.0}

    raw_aircraft = aircraft_json.get("aircraft", [])
    if not isinstance(raw_aircraft, list):
        raw_aircraft = []
    aircraft = [format_aircraft(item, receiver_json) for item in raw_aircraft if isinstance(item, dict)]
    aircraft.sort(key=lambda item: (item.get("messages") or 0), reverse=True)
    positioned = [item for item in aircraft if item.get("lat") is not None and item.get("lon") is not None]
    max_range = max((item.get("distance_nm") or 0 for item in positioned), default=0)
    last1 = stats_json.get("last1min", {}) if isinstance(stats_json, dict) else {}
    local = last1.get("local", {}) if isinstance(last1, dict) else {}
    cpr = last1.get("cpr", {}) if isinstance(last1, dict) else {}
    accepted = local.get("accepted", [0, 0]) if isinstance(local, dict) else [0, 0]
    accepted_count = sum(v for v in accepted if isinstance(v, (int, float)))
    messages = last1.get("messages", 0) if isinstance(last1, dict) else 0
    positions = 0
    if isinstance(cpr, dict):
        positions = sum(
            v
            for k, v in cpr.items()
            if k in {"airborne", "global_ok", "local_ok"} and isinstance(v, (int, float))
        )

    return {
        "source": str(root) if root else "demo",
        "now": aircraft_json.get("now", time.time()),
        "receiver": receiver_json,
        "totals": {
            "aircraft": len(aircraft),
            "positioned": len(positioned),
            "messages_total": aircraft_json.get("messages", 0),
            "messages_per_sec": round((messages or accepted_count) / 60, 1),
            "positions_per_sec": round(positions / 60, 2),
            "max_range_nm": round(max_range, 1),
        },
        "signal": {
            "gain_db": local.get("gain_db") if isinstance(local, dict) else None,
            "signal_db": local.get("signal") if isinstance(local, dict) else None,
            "noise_db": local.get("noise") if isinstance(local, dict) else None,
            "peak_signal_db": local.get("peak_signal") if isinstance(local, dict) else None,
        },
        "aircraft": aircraft[:30],
        "selected": aircraft[0] if aircraft else None,
        "history": history_points(root, receiver_json),
    }


def alert_status(system: dict[str, Any], adsb: dict[str, Any], services: list[dict[str, str]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    temp = system.get("temperature_c")
    if isinstance(temp, (int, float)) and temp >= 70:
        alerts.append({"level": "critical", "message": f"CPU temperature elevated at {temp:.1f} C"})
    elif isinstance(temp, (int, float)) and temp >= 60:
        alerts.append({"level": "warning", "message": f"CPU temperature watch at {temp:.1f} C"})

    if system.get("memory", {}).get("percent", 0) >= 85:
        alerts.append({"level": "warning", "message": "Memory utilization above 85%"})
    if system.get("disk", {}).get("percent", 0) >= 85:
        alerts.append({"level": "warning", "message": "Root disk utilization above 85%"})
    for service in services:
        if service["name"] in {"dump1090-fa", "piaware"} and service["state"] != "active":
            alerts.append({"level": "critical", "message": f"{service['name']} is {service['state']}"})
    if adsb.get("totals", {}).get("aircraft", 0) == 0:
        alerts.append({"level": "warning", "message": "No aircraft currently tracked"})
    if not alerts:
        alerts.append({"level": "nominal", "message": "All monitored systems nominal"})
    return alerts[:5]


def status_payload() -> dict[str, Any]:
    services = service_status()
    system = demo_system_status() if demo_mode_enabled() else system_status()
    adsb = adsb_status()
    return {
        "generated_at": time.time(),
        "system": system,
        "adsb": adsb,
        "map": map_config(),
        "services": services,
        "alerts": alert_status(system, adsb, services),
    }


def map_config() -> dict[str, Any]:
    tile_url = os.environ.get("PIAWARE_DASHBOARD_TILE_URL", DEFAULT_TILE_URL).strip()
    enabled = tile_url.lower() not in {"", "0", "false", "none", "off"}
    return {
        "enabled": enabled,
        "tile_url": tile_url if enabled else "",
        "attribution": os.environ.get("PIAWARE_DASHBOARD_TILE_ATTRIBUTION", "OpenStreetMap"),
    }


def system_status() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "load": load_average(),
        "memory": memory_status(),
        "disk": disk_status(),
        "uptime_seconds": uptime_seconds(),
        "temperature_c": temperature_c(),
        "network": network_status(),
    }


def demo_system_status() -> dict[str, Any]:
    return {
        "hostname": "PiAware Demo Station",
        "load": {"one": 0.22, "five": 0.24, "fifteen": 0.21},
        "memory": {
            "total": 1024**3,
            "used": 318 * 1024**2,
            "available": 706 * 1024**2,
            "percent": 31.0,
        },
        "disk": {
            "total": 32 * 1024**3,
            "used": 6 * 1024**3,
            "free": 26 * 1024**3,
            "percent": 18.8,
        },
        "uptime_seconds": 417600.0,
        "temperature_c": 51.1,
        "network": {
            "hostname": "PiAware Demo Station",
            "interfaces": [
                {"name": "wlan0", "state": "UP", "addresses": ["192.168.1.100/24"]},
                {"name": "tailscale0", "state": "UNKNOWN", "addresses": ["100.64.0.10/32"]},
            ],
        },
    }


def demo_aircraft_raw() -> dict[str, Any]:
    return {
        "now": time.time(),
        "messages": 61160,
        "aircraft": [
            {"hex": "A1B2C3", "flight": "DEMO101", "alt_baro": 17025, "gs": 399.7, "track": 86.7, "lat": 39.08, "lon": -94.72, "messages": 60, "seen": 0.1, "seen_pos": 0.2, "rssi": -17.1},
            {"hex": "B2C3D4", "flight": "DEMO202", "alt_baro": 17500, "gs": 440.8, "track": 79.9, "lat": 39.16, "lon": -94.55, "messages": 496, "seen": 24.6, "seen_pos": 26.1, "rssi": -22.8},
            {"hex": "C3D4E5", "flight": "DEMO303", "alt_baro": 33000, "gs": 471.0, "track": 248.1, "lat": 38.86, "lon": -95.35, "messages": 188, "seen": 1.9, "seen_pos": 2.1, "rssi": -19.8},
            {"hex": "D4E5F6", "flight": "DEMO404", "alt_baro": 6250, "gs": 243.4, "track": 301.5, "lat": 38.92, "lon": -95.18, "messages": 104, "seen": 4.2, "seen_pos": 4.6, "rssi": -21.4},
        ],
    }


def demo_stats() -> dict[str, Any]:
    return {
        "last1min": {
            "messages": 384,
            "local": {"accepted": [362, 22], "gain_db": 58.6, "signal": -19.3, "noise": -32.8, "peak_signal": -13.8},
            "cpr": {"airborne": 32, "global_ok": 28, "local_ok": 9},
        }
    }


def demo_history() -> list[dict[str, Any]]:
    now = time.time()
    return [
        {"time": now - (47 - idx) * 30, "aircraft": 3 + (idx % 7), "positioned": 2 + (idx % 5), "max_range_nm": 22 + (idx * 3) % 88}
        for idx in range(48)
    ]


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(status_payload())
            return
        if parsed.path == "/healthz":
            self.send_json({"ok": True})
            return
        super().do_GET()

    def send_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    port = int(os.environ.get("PIAWARE_DASHBOARD_PORT", "8088"))
    host = os.environ.get("PIAWARE_DASHBOARD_HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"PiAware dashboard listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
