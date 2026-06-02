# AutoStop Server Map

Last verified: 2026-06-02 UTC.

This file documents active production paths, services, ports, maintenance
automation, and cleanup boundaries for the AutoStop server. It intentionally
does not include secrets, `.env` values, API keys, or compose-expanded
configuration.

## Public Entry Points

| Purpose | Host | Frontend | Upstream |
| --- | --- | --- | --- |
| AutoStop App public site/admin | `https://autostop24.shop` | nginx `80/443` | `127.0.0.1:8010` |
| AutoStop CRM | `https://crm.autostopcrm.ru` | nginx `80/443` | `127.0.0.1:8000` |
| AutoStop CRM MCP | `https://crm.autostopcrm.ru/mcp` | nginx `443` | `127.0.0.1:8001/mcp` |
| AmneziaWG | public UDP `47895` | Docker proxy | `amnezia-awg2` |

DNS note: `crm.autostop24.shop` did not resolve from the server on
2026-06-02. Do not treat it as an active CRM hostname until DNS is added and
verified. The active CRM hostname is `crm.autostopcrm.ru`.

## Docker Projects

| Path | Compose project | Containers | Host ports |
| --- | --- | --- | --- |
| `/opt/autostopcrm` | `autostopcrm` | `autostopcrm` | `127.0.0.1:8000->41731`, `127.0.0.1:8001->41831` |
| `/opt/autostop-app` | `autostop-app` | `autostop-app`, `autostop-db` | `127.0.0.1:8010->8000` |
| Amnezia path managed by Docker | AmneziaWG | `amnezia-awg2` | `0.0.0.0:47895/udp` |

`autostopcrm` uses `/opt/autostopcrm/data` as the host state mount. The
production image installs `requirements-runtime.txt`; desktop/build tools such
as `pyinstaller` are not installed in the container runtime.

`autostop-app` stores Postgres and uploads in Docker volumes declared by its
compose file. Do not delete those volumes during routine cleanup.

## Host Listeners And Firewall

Expected listeners:

- SSH: `22/tcp`.
- nginx: `80/tcp`, `443/tcp`, `8080/tcp`.
- CRM local upstreams: `127.0.0.1:8000`, `127.0.0.1:8001`.
- AutoStop App local upstream: `127.0.0.1:8010`.
- AmneziaWG: `47895/udp`.
- CUPS: no listener expected on `631/tcp`.

UFW is active with default incoming deny, outgoing allow, routed deny. Allowed
inbound ports are SSH, nginx `80/443/8080`, AmneziaWG `47895/udp`, and `443/udp`.

SSH hardening lives in `/etc/ssh/sshd_config.d/99-autostop-hardening.conf` and
cloud-init password login is disabled in
`/etc/ssh/sshd_config.d/50-cloud-init.conf`. Root key login remains allowed;
password and keyboard-interactive login are disabled. `fail2ban` protects the
`sshd` jail.

## Systemd Automation

| Unit | Cadence | Purpose |
| --- | --- | --- |
| `autostopcrm-watchdog.timer` | every 1 minute after boot delay | Checks CRM container, local API, local MCP, and public CRM |
| `autostop-app-watchdog.timer` | every 2 minutes after boot delay | Checks App/Postgres health, local root/admin, and public app |
| `autostop-server-maintenance.timer` | weekly Sunday around 03:30 UTC | Safe cleanup, retention, and audit-compaction dry-run |

Useful commands:

```bash
systemctl list-timers --all 'autostop*.timer' 'autostop-server-maintenance.timer'
journalctl -u autostopcrm-watchdog.service -n 100 --no-pager
journalctl -u autostop-app-watchdog.service -n 100 --no-pager
journalctl -u autostop-server-maintenance.service -n 100 --no-pager
```

## Cleanup And Retention

Automated cleanup:

- `/etc/tmpfiles.d/autostop-temp-artifacts.conf` removes stale AutoStop/Codex
  temp artifacts.
- `/usr/local/sbin/autostop-server-maintenance.sh` supports `--dry-run` and
  `--apply`; the timer runs `--apply`.
- The maintenance script cleans apt and snap caches, prunes old Docker build
  cache, applies limited `/opt/autostop-app-backups` retention, checks CRM audit
  compaction status, then verifies nginx and local endpoints.

Backup boundaries:

- Keep `.env` files and secret backups locked down; do not archive or print
  their contents in logs.
- Keep `/root/autostopcrm-backups` unless an owner explicitly reviews it.
- Keep production state, audit archives, Postgres/upload volumes, operator
  activity, active nginx/systemd/VPN configs, and dirty checkouts.
- `/opt/autostop-app-backups` retains recent app tar and DB dump copies; the
  maintenance script keeps the newest 3 tar files and newest 2 dump files.

CRM audit events:

- Production state: `/opt/autostopcrm/data/state.json`.
- Archive: `/opt/autostopcrm/data/audit-archive`.
- Check with:

```bash
/opt/autostopcrm/.venv/bin/python /opt/autostopcrm/scripts/compact_audit_events.py \
  --dry-run \
  --json \
  --state-file /opt/autostopcrm/data/state.json \
  --archive-dir /opt/autostopcrm/data/audit-archive
```

Run `--apply --backup` only after reviewing a non-zero dry-run result.

## Verification Checklist

Use this short checklist after server maintenance:

```bash
df -hT /
du -sh /tmp /opt/autostop-app-backups /var/lib/snapd/cache /opt/autostopcrm/data
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
nginx -t
ufw status verbose
fail2ban-client status sshd
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8010/ >/dev/null
curl -fsS http://127.0.0.1:8010/admin/ >/dev/null
```

Public smoke:

```bash
curl -4 -k -s -o /dev/null -w 'autostop24.shop=%{http_code}\n' https://autostop24.shop/
curl -4 -k -s -o /dev/null -w 'crm.autostopcrm.ru=%{http_code}\n' https://crm.autostopcrm.ru/
```
