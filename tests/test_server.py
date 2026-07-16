import gzip
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class AircraftFormattingTests(unittest.TestCase):
    def test_zero_values_are_preserved(self):
        aircraft = server.format_aircraft(
            {
                "hex": "abc123",
                "flight": "TEST",
                "alt_baro": 0,
                "alt_geom": 125,
                "track": 0,
                "lat": 0.0,
                "lon": 0.0,
            },
            {"lat": 0.0, "lon": 0.0},
        )
        self.assertEqual(aircraft["altitude"], 0)
        self.assertEqual(aircraft["track"], 0)
        self.assertEqual(aircraft["distance_nm"], 0.0)

    def test_haversine_known_distance(self):
        self.assertAlmostEqual(server.haversine_nm(0, 0, 1, 0), 60.04, places=1)

    def test_stale_position_is_not_counted(self):
        self.assertFalse(server.position_is_fresh({"lat": 43.0, "lon": -79.0, "seen_pos": 61}))
        self.assertTrue(server.position_is_fresh({"lat": 43.0, "lon": -79.0, "seen_pos": 60}))


class AdsbStatusTests(unittest.TestCase):
    def setUp(self):
        server._STATUS_CACHE = None
        server._STATUS_CACHE_EXPIRES = 0.0

    def test_missing_receiver_data_is_not_replaced_with_demo_data(self):
        with mock.patch.object(server, "demo_mode_enabled", return_value=False), mock.patch.object(
            server, "dump1090_root", return_value=None
        ):
            status = server.adsb_status()
        self.assertFalse(status["available"])
        self.assertFalse(status["demo"])
        self.assertEqual(status["aircraft"], [])
        self.assertEqual(status["source"], "unavailable")

    def test_demo_data_requires_explicit_demo_mode(self):
        with mock.patch.object(server, "demo_mode_enabled", return_value=True):
            status = server.adsb_status()
        self.assertTrue(status["available"])
        self.assertTrue(status["demo"])
        self.assertEqual(len(status["aircraft"]), 4)

    def test_all_aircraft_are_returned_and_position_rate_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = time.time()
            aircraft = [
                {
                    "hex": f"{index:06x}",
                    "flight": f"T{index:03d}",
                    "lat": 43.0 + index / 1000,
                    "lon": -79.0,
                    "messages": index,
                    "seen_pos": 1,
                }
                for index in range(40)
            ]
            (root / "aircraft.json").write_text(json.dumps({"now": now, "messages": 1000, "aircraft": aircraft}))
            (root / "receiver.json").write_text(json.dumps({"lat": 43.0, "lon": -79.0}))
            (root / "stats.json").write_text(
                json.dumps(
                    {
                        "last1min": {
                            "messages": 600,
                            "local": {"accepted": [500, 100]},
                            "cpr": {"airborne": 180, "global_ok": 60, "local_ok": 30},
                        }
                    }
                )
            )
            with mock.patch.object(server, "demo_mode_enabled", return_value=False), mock.patch.object(
                server, "dump1090_root", return_value=root
            ):
                status = server.adsb_status()
        self.assertEqual(status["totals"]["aircraft"], 40)
        self.assertEqual(status["totals"]["positioned"], 40)
        self.assertEqual(len(status["aircraft"]), 40)
        self.assertEqual(status["totals"]["positions_per_sec"], 1.5)

    def test_readiness_rejects_stale_data_and_required_service_failure(self):
        payload = {
            "generated_at": time.time(),
            "adsb": {"available": True, "stale": True},
            "services": [{"name": "dump1090-fa", "required": True, "state": "inactive"}],
        }
        result, status = server.readiness_payload(payload)
        self.assertEqual(status, 503)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["reasons"]), 2)

    def test_temperature_thresholds_are_model_aware_and_configurable(self):
        self.assertEqual(server.temperature_thresholds("Raspberry Pi 3 Model B Plus Rev 1.3")["warning"], 60)
        self.assertEqual(server.temperature_thresholds("Raspberry Pi 4 Model B Rev 1.5")["warning"], 70)
        with mock.patch.dict(os.environ, {"PIAWARE_DASHBOARD_TEMP_WARNING": "65"}):
            self.assertEqual(server.temperature_thresholds(None)["warning"], 65)


class ServiceStatusTests(unittest.TestCase):
    @mock.patch("server.subprocess.run")
    def test_services_are_checked_in_one_systemctl_call(self, run):
        run.return_value = mock.Mock(stdout="active\nactive\ninactive\nactive\n")

        services = server.service_status()

        run.assert_called_once_with(
            ["systemctl", "is-active", "dump1090-fa", "piaware", "fa-mlat-client", "lighttpd"],
            text=True,
            stdout=server.subprocess.PIPE,
            stderr=server.subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        self.assertEqual([service["state"] for service in services], ["active", "active", "inactive", "active"])


class HttpHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DashboardHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_liveness_and_security_headers(self):
        with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
            payload = json.load(response)
            headers = response.headers
        self.assertTrue(payload["ok"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Python", headers["Server"])

    def test_static_assets_require_revalidation(self):
        with urlopen(f"{self.base_url}/app.js?v=test", timeout=3) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-cache")

    def test_json_gzip(self):
        request = Request(f"{self.base_url}/api/status", headers={"Accept-Encoding": "gzip"})
        with mock.patch.object(server, "status_payload", return_value={"value": "x" * 1000}):
            with urlopen(request, timeout=3) as response:
                body = response.read()
                encoding = response.headers["Content-Encoding"]
        self.assertEqual(encoding, "gzip")
        self.assertEqual(json.loads(gzip.decompress(body))["value"], "x" * 1000)

    def test_ready_endpoint_uses_503_when_receiver_is_not_ready(self):
        readiness = ({"ok": False, "reasons": ["test"]}, 503)
        with mock.patch.object(server, "readiness_payload", return_value=readiness):
            with self.assertRaises(HTTPError) as context:
                urlopen(f"{self.base_url}/readyz", timeout=3)
        self.assertEqual(context.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
