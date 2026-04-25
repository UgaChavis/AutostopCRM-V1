# AutoStop CRM Operations Runbook

This is the compact operational guide for local work, GitHub sync, and production verification.

## Canonical Endpoints

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- production server: `vps26457.mnogoweb.in`
- production repo path: `/opt/autostopcrm`
- this deploy path covers the CRM repo and the in-repo Telegram AI worker service; VPN helpers are separate deploy targets

## Branch Rule

- active branch: `autostopcrm-v1`
- in this workspace the GitHub remote for that line is `autostop-v1`
- the same commit should be present locally, on GitHub, and on production before and after release work

## Standard Sync Check

Run these checks before serious work:

```powershell
git status --short --branch
git rev-parse --short HEAD
git fetch autostop-v1 --prune
git rev-parse --short autostop-v1/autostopcrm-v1
```

Then verify the server:

```powershell
ssh -i C:\Users\User\.ssh\codex_autostopcrm root@vps26457.mnogoweb.in "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1"
```

## Local Workflow

1. Use Python 3.12 for the repo venv.
2. Run `scripts\doctor.ps1` before and after larger changes.
3. Run `scripts\run_checks.ps1` for focused Python-file validation.
4. Run the unit suite when the change touches shared behavior.
5. Keep the worktree clean before deployment.

## Deployment Workflow

1. Commit the intended change.
2. Push to `autostop-v1/autostopcrm-v1`.
3. On the server, fetch and reset to `origin/autostopcrm-v1`.
4. Run `./deploy.sh`; by default it syncs `origin/autostopcrm-v1` before rebuilding.
5. Confirm the smoke check passes.

`deploy.sh` can be overridden with `AUTOSTOP_DEPLOY_REMOTE` and
`AUTOSTOP_DEPLOY_BRANCH`, but the normal production path must stay on
`autostopcrm-v1`.

## Telegram AI Worker

The Telegram AI Board Manager runs in Docker service `autostopcrm-telegram-ai`.

It uses long polling and opens no public port. The worker talks to the CRM API at:

```text
http://autostopcrm:41731
```

Required production `.env` values when enabled:

```env
AUTOSTOP_TELEGRAM_AI_ENABLED=1
AUTOSTOP_TELEGRAM_BOT_TOKEN=...
AUTOSTOP_TELEGRAM_OWNER_IDS=123456789
OPENAI_API_KEY=...
AUTOSTOP_AI_MODEL=gpt-5.4-mini
AUTOSTOP_AI_STRONG_MODEL=gpt-5.4
AUTOSTOP_AI_STRONG_REASONING_EFFORT=high
```

Verification:

```bash
docker compose ps
docker compose logs --tail=100 autostopcrm-telegram-ai
```

Full setup notes for the operator are in `C:\Users\User\Desktop\AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`; the technical map is `docs/TELEGRAM_AI_BOARD_MANAGER.md`.

## Production Cautions

- do not assume a stale note reflects the current server head
- do not change secrets casually
- do not rotate credentials without a controlled plan
- do not rely on memory for production state; verify it

## Useful Files

- `00_START_HERE_AUTOSTOP_CRM.md`
- `MASTER-PLAN.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
- `scripts/doctor.ps1`
- `scripts/setup_dev.ps1`
- `scripts/run_checks.ps1`
- `scripts/run_quality_pass.ps1`
