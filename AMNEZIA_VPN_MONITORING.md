# Amnezia VPN Monitoring

## Scope

This adds a low-risk monitoring layer around the existing Amnezia/WireGuard VPN without rebuilding the VPN container or changing its peer configuration.

## Current Server Layout

- VPN runtime: Docker container `amnezia-awg2`
- VPN implementation: AmneziaWG on interface `awg0`
- Public listener: UDP `47895`
- Host source path for the image build context: `/opt/amnezia/amnezia-awg2`
- Live config inside the container: `/opt/amnezia/awg/awg0.conf`
- Existing telemetry data dir: `/var/lib/amnezia-traffic`
- Existing managed application repo on server: `/opt/autostopcrm`

## Why The VPN Container Is Not Updated

The live VPN config is stored inside the container filesystem, not on a bind-mounted host path. Because of that, rebuilding or replacing `amnezia-awg2` is not currently safe: it risks losing or desynchronizing peer configuration. The safe change boundary is the external monitoring layer only.

## Installed Files

- `scripts/amnezia_traffic_collector.py`
- `scripts/amnezia-traffic-collector.service`
- `scripts/amnezia-traffic-collector.timer`
- `scripts/amnezia-dashboard.service`
- `scripts/amnezia_server_info.json`
- `scripts/open_amnezia_dashboard.ps1`
- `scripts/open_amnezia_dashboard.cmd`

## What The Collector Produces

- `totals.json`: accumulated per-peer traffic
- `daily/YYYY-MM-DD.json`: daily traffic deltas
- `summary.json`: current server, ping and VPN summary
- `server_info.json`: editable provider/server/payment card for the dashboard
- `reports/current_users.csv`: machine-readable peer report
- `reports/current_users.md`: text report
- `web/dashboard.json`: dashboard JSON
- `web/index.html`: lightweight local dashboard

## Metrics

- traffic per peer
- sortable peer table by name, IP, handshake, current rate, daily rate and total traffic
- explicit traffic periods for current day and full accounting interval
- current speeds based on the latest sample window
- average speed for the current day
- active peer count based on recent handshake age
- server load, memory, disk and uptime
- ping latency and packet loss
- warning list for degraded conditions

## Safe Deployment Outline

1. Create a timestamped backup directory on the server.
2. Copy the current collector binary, systemd units and `/var/lib/amnezia-traffic` into that backup.
3. Copy the updated collector and unit files from this repo to the server.
4. Copy `scripts/amnezia_server_info.json` to `/var/lib/amnezia-traffic/server_info.json` and adjust provider or billing notes if needed.
5. Run one manual collector execution.
6. Reload `systemd`, restart the collector timer, enable the localhost dashboard service and verify `127.0.0.1:18080`.
7. Open the page through an SSH tunnel from Windows.

## Rollback

1. Stop `amnezia-dashboard.service`.
2. Restore the previous `/usr/local/bin/amnezia_traffic_collector.py`.
3. Restore the previous systemd unit files.
4. Restore `/var/lib/amnezia-traffic` from backup if needed.
5. Run `systemctl daemon-reload`.
6. Restart `amnezia-traffic-collector.timer`.

Rollback does not touch the VPN container itself.

## Windows Launcher

Use `scripts/open_amnezia_dashboard.cmd`.

It:

- opens an SSH tunnel from `127.0.0.1:18765` to `127.0.0.1:18080` on the server
- reuses an existing tunnel when possible
- opens the dashboard in the default browser

No public dashboard port is exposed to the internet.
