#!/bin/sh
set -eu

APP_DIR="/opt/piaware-dashboard"
SERVICE_FILE="/etc/systemd/system/piaware-dashboard.service"
SERVICE_USER="piaware-dashboard"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo ./uninstall.sh" >&2
  exit 1
fi

systemctl disable --now piaware-dashboard.service 2>/dev/null || true
rm -f "$SERVICE_FILE"
systemctl daemon-reload
rm -rf "$APP_DIR"
if id "$SERVICE_USER" >/dev/null 2>&1; then
  userdel "$SERVICE_USER"
fi

echo "PiAware Dashboard removed."
