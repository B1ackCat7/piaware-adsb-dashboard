import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectContractTests(unittest.TestCase):
    def test_frontend_uses_non_overlapping_refresh_scheduler(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("setInterval(refresh", script)
        self.assertIn("refreshInFlight", script)
        self.assertIn("AbortController", script)

    def test_accessibility_and_map_attribution_are_present(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="map-attribution"', html)
        self.assertIn('aria-label="Open SkyAware"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn(":focus-visible", css)

    def test_service_is_unprivileged_and_hardened(self):
        unit = (ROOT / "piaware-dashboard.service").read_text(encoding="utf-8")
        self.assertIn("User=piaware-dashboard", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("CapabilityBoundingSet=", unit)
        self.assertIn("SupplementaryGroups=video", unit)
        self.assertNotIn("ProtectClock=true", unit)

    def test_installer_restarts_and_copies_all_assets(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl restart piaware-dashboard.service", installer)
        self.assertIn("static/favicon.svg", installer)
        self.assertIn("for _ in range(20)", installer)
        self.assertIn("time.sleep(0.5)", installer)


if __name__ == "__main__":
    unittest.main()
