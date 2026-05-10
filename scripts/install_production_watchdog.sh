#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${AUTOSTOP_WATCHDOG_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
INTERVAL="${AUTOSTOP_WATCHDOG_INTERVAL:-1min}"
SERVICE_FILE="/etc/systemd/system/autostopcrm-watchdog.service"
TIMER_FILE="/etc/systemd/system/autostopcrm-watchdog.timer"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: production watchdog installation requires root." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: systemctl is required to install the production watchdog." >&2
  exit 1
fi

install -d -m 755 /etc/systemd/system

cat >"$SERVICE_FILE" <<UNIT
[Unit]
Description=AutoStop CRM production watchdog
Wants=docker.service nginx.service
After=docker.service nginx.service

[Service]
Type=oneshot
WorkingDirectory=$ROOT_DIR
Environment=AUTOSTOP_WATCHDOG_ROOT=$ROOT_DIR
ExecStart=/usr/bin/env python3 $ROOT_DIR/scripts/production_watchdog.py
UNIT

cat >"$TIMER_FILE" <<UNIT
[Unit]
Description=Run AutoStop CRM production watchdog

[Timer]
OnBootSec=2min
OnUnitActiveSec=$INTERVAL
AccuracySec=15s
Persistent=true
Unit=autostopcrm-watchdog.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now autostopcrm-watchdog.timer
systemctl start autostopcrm-watchdog.service || true
systemctl --no-pager --plain status autostopcrm-watchdog.timer
