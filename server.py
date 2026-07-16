#!/usr/bin/env python3
"""Lightweight PiAware operations dashboard server.

The server intentionally uses only Python's standard library so it can run on a
small Raspberry Pi OS install without npm, pip, or a build step.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DUMP1090_ROOTS = [Path("/run/dump1090-fa"), Path("/var/run/dump1090-fa")]
SERVICE_DEFINITIONS = [
    {"name": "dump1090-fa", "required": True},
    {"name": "piaware", "required": True},
    {"name": "fa-mlat-client", "required": False},
    {"name": "lighttpd", "required": False},
]
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_TILE_ATTRIBUTION = "© OpenStreetMap contributors"
DEFAULT_TILE_ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"
STATUS_CACHE_SECONDS = 2.0
DATA_STALE_SECONDS = 15.0

_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict[str, Any] | None = None
_STATUS_CACHE_EXPIRES = 0.0


def demo_mode_enabled() -> bool:
    return os.environ.get("PIAWARE_DASHBOARD_DEMO", "").lower() in {"1", "true", "yes", "on"}


def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}, f"{path.name} did not contain a JSON object"
        return data, None
    except Exception as exc:
        return {}, f"Unable to read {path}: {exc}"


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


def load_average() -> tuple[dict[str, float | None], str | None]:
    try:
        one, five, fifteen, *_ = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        return {"one": float(one), "five": float(five), "fifteen": float(fifteen)}, None
    except Exception as exc:
        return {"one": None, "five": None, "fifteen": None}, f"Load average unavailable: {exc}"


def memory_status() -> tuple[dict[str, Any], str | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        if not total:
            raise ValueError("MemTotal is missing")
        used = max(total - available, 0)
        percent = round((used / total) * 100, 1)
        return {"total": total, "used": used, "available": available, "percent": percent}, None
    except Exception as exc:
        return {"total": None, "used": None, "available": None, "percent": None}, f"Memory status unavailable: {exc}"


def disk_status() -> tuple[dict[str, Any], str | None]:
    try:
        usage = shutil.disk_usage("/")
        percent = round((usage.used / usage.total) * 100, 1) if usage.total else None
        return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}, None
    except Exception as exc:
        return {"total": None, "used": None, "free": None, "percent": None}, f"Disk status unavailable: {exc}"


def uptime_seconds() -> tuple[float | None, str | None]:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]), None
    except Exception as exc:
        return None, f"Uptime unavailable: {exc}"


def temperature_c() -> tuple[float | None, str | None]:
    raw = read_number("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None, "CPU temperature unavailable"
    return round(raw / 1000, 1), None


def raspberry_pi_model() -> str | None:
    for path in (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model")):
        try:
            return path.read_text(encoding="utf-8").rstrip("\x00\n")
        except Exception:
            continue
    return None


def temperature_thresholds(model: str | None) -> dict[str, float]:
    if model and ("3 Model B Plus" in model or "3 Model A Plus" in model):
        defaults = {"warning": 60.0, "critical": 80.0}
    else:
        defaults = {"warning": 70.0, "critical": 80.0}
    for key, env_name in (
        ("warning", "PIAWARE_DASHBOARD_TEMP_WARNING"),
        ("critical", "PIAWARE_DASHBOARD_TEMP_CRITICAL"),
    ):
        try:
            defaults[key] = float(os.environ.get(env_name, defaults[key]))
        except (TypeError, ValueError):
            pass
    return defaults


def throttling_status() -> dict[str, Any]:
    if not shutil.which("vcgencmd"):
        return {"available": False, "raw": None, "current": [], "occurred": []}
    try:
        output = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, timeout=2).strip()
        raw_value = output.split("=", 1)[-1]
        value = int(raw_value, 16)
        current_bits = {
            0: "under-voltage",
            1: "frequency-capped",
            2: "throttled",
            3: "soft-temperature-limit",
        }
        occurred_bits = {
            16: "under-voltage",
            17: "frequency-capped",
            18: "throttled",
            19: "soft-temperature-limit",
        }
        return {
            "available": True,
            "raw": raw_value,
            "current": [label for bit, label in current_bits.items() if value & (1 << bit)],
            "occurred": [label for bit, label in occurred_bits.items() if value & (1 << bit)],
        }
    except Exception:
        return {"available": False, "raw": None, "current": [], "occurred": []}


def wifi_status() -> dict[str, Any] | None:
    if not shutil.which("iw"):
        return None
    candidates: list[str] = []
    try:
        candidates = sorted(path.name for path in Path("/sys/class/net").glob("wl*"))
    except Exception:
        pass
    for interface in candidates or ["wlan0"]:
        try:
            output = subprocess.check_output(["iw", "dev", interface, "link"], text=True, timeout=2)
        except Exception:
            continue
        connected = "Not connected." not in output
        result: dict[str, Any] = {
            "interface": interface,
            "connected": connected,
            "signal_dbm": None,
            "tx_bitrate_mbps": None,
        }
        for line in output.splitlines():
            text = line.strip()
            if text.startswith("SSID:"):
                result["ssid"] = text.split(":", 1)[1].strip()
            elif text.startswith("signal:"):
                try:
                    result["signal_dbm"] = float(text.split()[1])
                except (IndexError, ValueError):
                    pass
            elif text.startswith("tx bitrate:"):
                try:
                    result["tx_bitrate_mbps"] = float(text.split()[2])
                except (IndexError, ValueError):
                    pass
        return result
    return None


def network_status() -> tuple[dict[str, Any], str | None]:
    interfaces: list[dict[str, Any]] = []
    error = None
    try:
        output = subprocess.check_output(["ip", "-brief", "addr"], text=True, timeout=2)
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            state = parts[1]
            addresses = [part for part in parts[2:] if "/" in part]
            interfaces.append({"name": name, "state": state, "addresses": addresses[:4]})
    except Exception as exc:
        error = f"Network interfaces unavailable: {exc}"
    return {"hostname": socket.gethostname(), "interfaces": interfaces, "wifi": wifi_status()}, error


def service_status() -> list[dict[str, Any]]:
    if demo_mode_enabled():
        return [{**service, "state": "active"} for service in SERVICE_DEFINITIONS]

    names = [service["name"] for service in SERVICE_DEFINITIONS]
    states: list[str] = []
    try:
        result = subprocess.run(
            ["systemctl", "is-active", *names],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        states = [line.strip() or "unknown" for line in result.stdout.splitlines()]
    except Exception:
        states = []
    return [
        {**service, "state": states[index] if index < len(states) else "unknown"}
        for index, service in enumerate(SERVICE_DEFINITIONS)
    ]


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    a = min(max(a, 0.0), 1.0)
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def format_aircraft(aircraft: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    lat = aircraft.get("lat")
    lon = aircraft.get("lon")
    distance = None
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        rlat = receiver.get("lat")
        rlon = receiver.get("lon")
        if isinstance(rlat, (int, float)) and isinstance(rlon, (int, float)):
            distance = round(haversine_nm(rlat, rlon, lat, lon), 1)

    hex_code = aircraft.get("hex")
    flight = first_present(aircraft.get("flight"), hex_code, "UNKNOWN")
    return {
        "hex": str(hex_code or "").upper(),
        "flight": str(flight).strip() or "UNKNOWN",
        "altitude": first_present(aircraft.get("alt_baro"), aircraft.get("alt_geom")),
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


def position_is_fresh(aircraft: dict[str, Any]) -> bool:
    if not isinstance(aircraft.get("lat"), (int, float)) or not isinstance(aircraft.get("lon"), (int, float)):
        return False
    seen_pos = aircraft.get("seen_pos")
    return not isinstance(seen_pos, (int, float)) or seen_pos <= 60


def history_points(root: Path | None, receiver: dict[str, Any]) -> list[dict[str, Any]]:
    if root is None:
        return []
    paths: list[Path] = []
    for path in root.glob("history_*.json"):
        try:
            paths.append(path)
        except OSError:
            continue
    try:
        paths.sort(key=lambda path: path.stat().st_mtime)
    except OSError:
        paths = sorted(paths)
    points: list[dict[str, Any]] = []
    for path in paths[-48:]:
        data, error = read_json(path)
        if error:
            continue
        raw_aircraft = data.get("aircraft", [])
        if not isinstance(raw_aircraft, list):
            raw_aircraft = []
        aircraft = [format_aircraft(item, receiver) for item in raw_aircraft if isinstance(item, dict)]
        positioned = [item for item in aircraft if position_is_fresh(item)]
        max_range = max((item.get("distance_nm") or 0 for item in positioned), default=0)
        try:
            fallback_time = path.stat().st_mtime
        except OSError:
            fallback_time = time.time()
        points.append(
            {
                "time": data.get("now") or fallback_time,
                "aircraft": len(aircraft),
                "positioned": len(positioned),
                "max_range_nm": round(max_range, 1),
            }
        )
    return points


def unavailable_adsb_status(message: str) -> dict[str, Any]:
    return {
        "available": False,
        "demo": False,
        "stale": True,
        "error": message,
        "errors": [message],
        "source": "unavailable",
        "now": None,
        "data_age_seconds": None,
        "receiver": {},
        "totals": {
            "aircraft": 0,
            "positioned": 0,
            "messages_total": None,
            "messages_per_sec": None,
            "positions_per_sec": None,
            "max_range_nm": None,
        },
        "signal": {"gain_db": None, "signal_db": None, "noise_db": None, "peak_signal_db": None},
        "aircraft": [],
        "selected": None,
        "history": [],
    }


def adsb_status() -> dict[str, Any]:
    if demo_mode_enabled():
        return demo_adsb_status()

    root = dump1090_root()
    if root is None:
        return unavailable_adsb_status("dump1090-fa runtime data is unavailable")

    aircraft_json, aircraft_error = read_json(root / "aircraft.json")
    if aircraft_error:
        return unavailable_adsb_status(aircraft_error)
    stats_json, stats_error = read_json(root / "stats.json")
    receiver_json, receiver_error = read_json(root / "receiver.json")
    errors = [error for error in (stats_error, receiver_error) if error]

    raw_aircraft = aircraft_json.get("aircraft", [])
    if not isinstance(raw_aircraft, list):
        raw_aircraft = []
        errors.append("aircraft.json did not contain an aircraft list")
    aircraft = [format_aircraft(item, receiver_json) for item in raw_aircraft if isinstance(item, dict)]
    aircraft.sort(key=lambda item: (item.get("messages") or 0), reverse=True)
    positioned = [item for item in aircraft if position_is_fresh(item)]
    max_range = max((item.get("distance_nm") or 0 for item in positioned), default=0)

    last1 = stats_json.get("last1min", {}) if isinstance(stats_json, dict) else {}
    local = last1.get("local", {}) if isinstance(last1, dict) else {}
    cpr = last1.get("cpr", {}) if isinstance(last1, dict) else {}
    accepted = local.get("accepted", [0, 0]) if isinstance(local, dict) else [0, 0]
    accepted_count = sum(value for value in accepted if isinstance(value, (int, float)))
    messages = last1.get("messages") if isinstance(last1, dict) else None
    messages_per_sec = None
    if isinstance(messages, (int, float)):
        messages_per_sec = round(messages / 60, 1)
    elif accepted_count:
        messages_per_sec = round(accepted_count / 60, 1)
    position_decodes = 0
    if isinstance(cpr, dict):
        position_decodes = sum(
            value
            for key, value in cpr.items()
            if key in {"global_ok", "local_ok"} and isinstance(value, (int, float))
        )

    generated = aircraft_json.get("now")
    data_age = round(max(time.time() - generated, 0), 1) if isinstance(generated, (int, float)) else None
    stale = data_age is None or data_age > DATA_STALE_SECONDS
    if stale:
        errors.append("Aircraft data is stale or has no valid timestamp")

    return {
        "available": True,
        "demo": False,
        "stale": stale,
        "error": errors[0] if errors else None,
        "errors": errors,
        "source": str(root),
        "now": generated,
        "data_age_seconds": data_age,
        "receiver": receiver_json,
        "totals": {
            "aircraft": len(aircraft),
            "positioned": len(positioned),
            "messages_total": aircraft_json.get("messages"),
            "messages_per_sec": messages_per_sec,
            "positions_per_sec": round(position_decodes / 60, 2),
            "max_range_nm": round(max_range, 1),
        },
        "signal": {
            "gain_db": local.get("gain_db") if isinstance(local, dict) else None,
            "signal_db": local.get("signal") if isinstance(local, dict) else None,
            "noise_db": local.get("noise") if isinstance(local, dict) else None,
            "peak_signal_db": local.get("peak_signal") if isinstance(local, dict) else None,
        },
        "aircraft": aircraft,
        "selected": aircraft[0] if aircraft else None,
        "history": history_points(root, receiver_json),
    }


def map_config() -> dict[str, Any]:
    tile_url = os.environ.get("PIAWARE_DASHBOARD_TILE_URL", DEFAULT_TILE_URL).strip()
    enabled = tile_url.lower() not in {"", "0", "false", "none", "off"}
    return {
        "enabled": enabled,
        "tile_url": tile_url if enabled else "",
        "attribution": os.environ.get("PIAWARE_DASHBOARD_TILE_ATTRIBUTION", DEFAULT_TILE_ATTRIBUTION),
        "attribution_url": os.environ.get("PIAWARE_DASHBOARD_TILE_ATTRIBUTION_URL", DEFAULT_TILE_ATTRIBUTION_URL),
    }


def system_status() -> dict[str, Any]:
    load, load_error = load_average()
    memory, memory_error = memory_status()
    disk, disk_error = disk_status()
    uptime, uptime_error = uptime_seconds()
    temperature, temperature_error = temperature_c()
    network, network_error = network_status()
    model = raspberry_pi_model()
    errors = [
        error
        for error in (load_error, memory_error, disk_error, uptime_error, temperature_error, network_error)
        if error
    ]
    return {
        "hostname": socket.gethostname(),
        "model": model,
        "cpu_count": os.cpu_count() or 1,
        "load": load,
        "memory": memory,
        "disk": disk,
        "uptime_seconds": uptime,
        "temperature_c": temperature,
        "temperature_thresholds": temperature_thresholds(model),
        "throttling": throttling_status(),
        "network": network,
        "errors": errors,
    }


def demo_system_status() -> dict[str, Any]:
    return {
        "hostname": "PiAware Demo Station",
        "model": "Raspberry Pi demo",
        "cpu_count": 4,
        "load": {"one": 0.22, "five": 0.24, "fifteen": 0.21},
        "memory": {"total": 1024**3, "used": 318 * 1024**2, "available": 706 * 1024**2, "percent": 31.0},
        "disk": {"total": 32 * 1024**3, "used": 6 * 1024**3, "free": 26 * 1024**3, "percent": 18.8},
        "uptime_seconds": 417600.0,
        "temperature_c": 51.1,
        "temperature_thresholds": {"warning": 70.0, "critical": 80.0},
        "throttling": {"available": True, "raw": "0x0", "current": [], "occurred": []},
        "network": {
            "hostname": "PiAware Demo Station",
            "interfaces": [
                {"name": "wlan0", "state": "UP", "addresses": ["192.168.1.100/24"]},
                {"name": "tailscale0", "state": "UNKNOWN", "addresses": ["100.64.0.10/32"]},
            ],
            "wifi": {"interface": "wlan0", "connected": True, "signal_dbm": -58.0, "tx_bitrate_mbps": 72.2},
        },
        "errors": [],
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
        {"time": now - (47 - index) * 30, "aircraft": 3 + (index % 7), "positioned": 2 + (index % 5), "max_range_nm": 22 + (index * 3) % 88}
        for index in range(48)
    ]


def demo_adsb_status() -> dict[str, Any]:
    raw = demo_aircraft_raw()
    stats = demo_stats()
    receiver = {"lat": 39.0, "lon": -95.0}
    aircraft = [format_aircraft(item, receiver) for item in raw["aircraft"]]
    aircraft.sort(key=lambda item: (item.get("messages") or 0), reverse=True)
    positioned = [item for item in aircraft if position_is_fresh(item)]
    local = stats["last1min"]["local"]
    cpr = stats["last1min"]["cpr"]
    return {
        "available": True,
        "demo": True,
        "stale": False,
        "error": None,
        "errors": [],
        "source": "demo",
        "now": raw["now"],
        "data_age_seconds": 0.0,
        "receiver": receiver,
        "totals": {
            "aircraft": len(aircraft),
            "positioned": len(positioned),
            "messages_total": raw["messages"],
            "messages_per_sec": round(stats["last1min"]["messages"] / 60, 1),
            "positions_per_sec": round((cpr["global_ok"] + cpr["local_ok"]) / 60, 2),
            "max_range_nm": round(max((item.get("distance_nm") or 0 for item in positioned), default=0), 1),
        },
        "signal": {
            "gain_db": local["gain_db"],
            "signal_db": local["signal"],
            "noise_db": local["noise"],
            "peak_signal_db": local["peak_signal"],
        },
        "aircraft": aircraft,
        "selected": aircraft[0] if aircraft else None,
        "history": demo_history(),
    }


def alert_status(system: dict[str, Any], adsb: dict[str, Any], services: list[dict[str, Any]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    temp = system.get("temperature_c")
    thresholds = system.get("temperature_thresholds", {})
    critical_temp = thresholds.get("critical", 80)
    warning_temp = thresholds.get("warning", 70)
    if isinstance(temp, (int, float)) and temp >= critical_temp:
        alerts.append({"level": "critical", "message": f"CPU temperature elevated at {temp:.1f} C"})
    elif isinstance(temp, (int, float)) and temp >= warning_temp:
        alerts.append({"level": "warning", "message": f"CPU temperature watch at {temp:.1f} C"})

    throttling = system.get("throttling", {})
    if throttling.get("current"):
        alerts.append({"level": "critical", "message": f"Pi throttling active: {', '.join(throttling['current'])}"})
    memory_percent = system.get("memory", {}).get("percent")
    disk_percent = system.get("disk", {}).get("percent")
    if isinstance(memory_percent, (int, float)) and memory_percent >= 85:
        alerts.append({"level": "warning", "message": "Memory utilization above 85%"})
    if isinstance(disk_percent, (int, float)) and disk_percent >= 85:
        alerts.append({"level": "warning", "message": "Root disk utilization above 85%"})

    wifi = system.get("network", {}).get("wifi")
    if wifi and wifi.get("connected") is False:
        alerts.append({"level": "critical", "message": "Wi-Fi is disconnected"})
    elif wifi and isinstance(wifi.get("signal_dbm"), (int, float)) and wifi["signal_dbm"] <= -75:
        alerts.append({"level": "warning", "message": f"Wi-Fi signal is weak at {wifi['signal_dbm']:.0f} dBm"})

    for service in services:
        if service.get("required") and service.get("state") != "active":
            alerts.append({"level": "critical", "message": f"{service['name']} is {service['state']}"})
        elif service.get("name") == "lighttpd" and service.get("state") not in {"active", "unknown"}:
            alerts.append({"level": "warning", "message": f"SkyAware web service is {service['state']}"})

    if not adsb.get("available"):
        alerts.append({"level": "critical", "message": adsb.get("error") or "ADS-B data unavailable"})
    elif adsb.get("stale"):
        alerts.append({"level": "critical", "message": "ADS-B aircraft data is stale"})
    elif adsb.get("totals", {}).get("aircraft") == 0:
        alerts.append({"level": "warning", "message": "No aircraft currently tracked"})
    if system.get("errors"):
        alerts.append({"level": "warning", "message": system["errors"][0]})
    if not alerts:
        alerts.append({"level": "nominal", "message": "All monitored systems nominal"})
    return alerts[:6]


def build_status_payload() -> dict[str, Any]:
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


def status_payload(use_cache: bool = True) -> dict[str, Any]:
    global _STATUS_CACHE, _STATUS_CACHE_EXPIRES
    now = time.monotonic()
    if use_cache and _STATUS_CACHE is not None and now < _STATUS_CACHE_EXPIRES:
        return _STATUS_CACHE
    with _STATUS_LOCK:
        now = time.monotonic()
        if use_cache and _STATUS_CACHE is not None and now < _STATUS_CACHE_EXPIRES:
            return _STATUS_CACHE
        payload = build_status_payload()
        if use_cache:
            _STATUS_CACHE = payload
            _STATUS_CACHE_EXPIRES = now + STATUS_CACHE_SECONDS
        return payload


def readiness_payload(payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
    current = payload or status_payload()
    reasons: list[str] = []
    adsb = current.get("adsb", {})
    if not adsb.get("available"):
        reasons.append(adsb.get("error") or "ADS-B data unavailable")
    elif adsb.get("stale"):
        reasons.append("ADS-B data is stale")
    for service in current.get("services", []):
        if service.get("required") and service.get("state") != "active":
            reasons.append(f"{service['name']} is {service['state']}")
    ok = not reasons
    return {"ok": ok, "reasons": reasons, "generated_at": current.get("generated_at")}, 200 if ok else 503


def content_security_policy() -> str:
    image_sources = ["'self'", "data:"]
    tile_url = map_config().get("tile_url", "")
    parsed = urlparse(tile_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        image_sources.append(f"{parsed.scheme}://{parsed.netloc}")
    return "; ".join(
        [
            "default-src 'self'",
            f"img-src {' '.join(image_sources)}",
            "script-src 'self'",
            "style-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
        ]
    )


class DashboardHandler(SimpleHTTPRequestHandler):
    server_version = "PiAwareDashboard"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self._static_response = False
        try:
            if parsed.path == "/api/status":
                self.send_json(status_payload())
                return
            if parsed.path == "/healthz":
                self.send_json({"ok": True})
                return
            if parsed.path == "/readyz":
                payload, status = readiness_payload()
                self.send_json(payload, status=status)
                return
            self._static_response = True
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.send_json({"ok": False, "error": f"Dashboard request failed: {exc}"}, status=500)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
        use_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower() and len(encoded) >= 512
        body = gzip.compress(encoded, compresslevel=5) if use_gzip else encoded
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        if getattr(self, "_static_response", False):
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", content_security_policy())
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {fmt % args}")


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    port = int(os.environ.get("PIAWARE_DASHBOARD_PORT", "8088"))
    host = os.environ.get("PIAWARE_DASHBOARD_HOST", "0.0.0.0")
    server = DashboardHTTPServer((host, port), DashboardHandler)
    print(f"PiAware dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
