#!/bin/sh
set -eu

APP_DIR="/opt/piaware-dashboard"
SERVICE_FILE="/etc/systemd/system/piaware-dashboard.service"
SERVICE_USER="piaware-dashboard"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo ./install.sh" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if getent group video >/dev/null 2>&1; then
  usermod -a -G video "$SERVICE_USER"
fi

install -d -o root -g root -m 0755 "$APP_DIR" "$APP_DIR/static"

install -o root -g root -m 0644 server.py "$APP_DIR/server.py"
install -o root -g root -m 0644 README.md "$APP_DIR/README.md"
install -o root -g root -m 0644 piaware-dashboard.service "$APP_DIR/piaware-dashboard.service"
install -o root -g root -m 0644 static/index.html "$APP_DIR/static/index.html"
install -o root -g root -m 0644 static/style.css "$APP_DIR/static/style.css"
install -o root -g root -m 0644 static/app.js "$APP_DIR/static/app.js"
install -o root -g root -m 0644 static/favicon.svg "$APP_DIR/static/favicon.svg"

install -o root -g root -m 0644 piaware-dashboard.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable piaware-dashboard.service
if ! systemctl restart piaware-dashboard.service; then
  systemctl status --no-pager piaware-dashboard.service || true
  exit 1
fi

python3 - <<'PY'
import json
import time
from urllib.error import URLError
from urllib.request import urlopen

last_error = None
for _ in range(20):
    try:
        with urlopen("http://127.0.0.1:8088/healthz", timeout=2) as response:
            payload = json.load(response)
            if response.status == 200 and payload.get("ok") is True:
                break
            last_error = f"unexpected response: HTTP {response.status} {payload!r}"
    except (OSError, URLError, ValueError) as exc:
        last_error = str(exc)
    time.sleep(0.5)
else:
    raise SystemExit(f"Dashboard health check failed: {last_error}")
PY

echo "PiAware Dashboard installed."
echo "Open http://<your-pi-address>:8088/"
