#!/bin/sh
set -eu

APP_DIR="/opt/piaware-dashboard"
SERVICE_FILE="/etc/systemd/system/piaware-dashboard.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo ./install.sh" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

mkdir -p "$APP_DIR/static"

cp server.py "$APP_DIR/server.py"
cp README.md "$APP_DIR/README.md"
cp piaware-dashboard.service "$APP_DIR/piaware-dashboard.service"
cp static/index.html "$APP_DIR/static/index.html"
cp static/style.css "$APP_DIR/static/style.css"
cp static/app.js "$APP_DIR/static/app.js"

cp piaware-dashboard.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable --now piaware-dashboard.service

echo "PiAware Dashboard installed."
echo "Open http://<your-pi-address>:8088/"
