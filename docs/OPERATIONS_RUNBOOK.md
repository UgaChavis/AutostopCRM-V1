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
- in this workspace the GitHub remote for that line is `origin`
- the same commit should be present locally, on GitHub, and on production before and after release work
- do not trust pinned commit notes in documentation; verify with command output

## Standard Sync Check

Run these checks before serious work:

```powershell
git status --short --branch
git rev-parse --short HEAD
git fetch origin autostopcrm-v1 --prune
git rev-parse --short origin/autostopcrm-v1
```

Then verify the server:

```powershell
ssh -i C:\Users\User\.ssh\codex_autostopcrm root@vps26457.mnogoweb.in "cd /opt/autostopcrm && git status --short --branch && git rev-parse --short HEAD && git rev-parse --short origin/autostopcrm-v1"
```

## Local Workflow

1. Use Python 3.12 for the repo venv.
2. Run `scripts\doctor.ps1` before and after larger changes.
3. Run `scripts\run_checks.ps1` for focused Python validation and generated browser-JS syntax validation.
4. Run `python scripts\check_web_assets_js.py` directly when touching `src/minimal_kanban/web_assets.py`.
5. Run `python -m unittest discover -s tests -v` when the change touches shared behavior.
6. Run `python scripts\audit_localization.py` before release if UI/docs text changed.
7. Keep the worktree clean before deployment.

Local production-like connector smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username admin --operator-password admin --expect-admin
```

## Deployment Workflow

1. Commit the intended change.
2. Push to `origin/autostopcrm-v1`.
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
AUTOSTOP_AI_WEB_SEARCH_ENABLED=1
AUTOSTOP_AI_STRONG_REASONING_EFFORT=high
```

Verification:

```bash
docker compose ps
docker compose logs --tail=100 autostopcrm-telegram-ai
```

Current Telegram AI behavior:

- text, voice, photo, CRM tools, audit, rollback basics, and conversation memory are implemented
- voice notes are transcribed locally first with `faster-whisper`; OpenAI transcription is fallback only
- explicit `найди в интернете` / `загугли` commands and the model-planned `internet_search` tool use OpenAI `web_search_preview`
- complex CRM-planning commands can escalate to `AUTOSTOP_AI_STRONG_MODEL`
- hidden card board summaries can be updated through the approved `set_card_board_summary` tool and local API path
- direct internet search intentionally stays on `AUTOSTOP_AI_MODEL` with a low search context and one retry; this avoids the live strong-model web-search timeout/429 failure mode
- `/status` reports whether internet search is enabled in the current runtime

Useful live smoke after deploy:

```bash
docker compose exec -T autostopcrm-telegram-ai sh -lc 'set -a; . /run/telegram-ai.env; cd /app; PYTHONPATH=/app/src python - <<'"'"'PY'"'"'
from minimal_kanban.telegram_ai.config import load_config
from minimal_kanban.telegram_ai.openai_client import TelegramAIOpenAIClient
client = TelegramAIOpenAIClient(load_config())
print(client.internet_search(command_text="Найди в интернете официальный сайт Toyota и ответь одной строкой с источником.", role="owner")[:800])
PY'
```

Full setup notes for the operator are in `docs/AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`.
The desktop copy is `C:\Users\User\Desktop\AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`.
The technical map is `docs/TELEGRAM_AI_BOARD_MANAGER.md`.

## Production Cautions

- do not assume a stale note reflects the current server head
- do not change secrets casually
- do not rotate credentials without a controlled plan
- do not rely on memory for production state; verify it
- `telegram-ai.env` can appear as an untracked server-local environment file; do not remove it during repo sync

## Production Verification

From the local workstation, verify the public stack after deploy:

```powershell
python scripts\check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url https://crm.autostopcrm.ru --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
```

From the server, verify the container-local API path:

```bash
cd /opt/autostopcrm
docker compose ps
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
```

Minimum manual UI smoke after UI changes:

- board loads past `СОЕДИНЕНИЕ С ДОСКОЙ...`
- all topbar modules open
- card open/save and card journal remain readable
- Files grid, upload/paste, drag, download/open and delete work
- clients, repair orders, cashboxes and employees modals open without console errors
- anonymous public writes remain blocked

## Documentation Policy

- canonical operational docs: root `README.md`, `00_START_HERE_AUTOSTOP_CRM.md`, `PROJECT_HANDOFF.md`, this runbook, `API_GUIDE.md`, `MCP_GUIDE.md`
- keep `MASTER-PLAN.md`, `README_SETTINGS.md`, `CHATGPT_CONNECTOR_SETUP.md` and print/Telegram docs while code, scripts, or active workflows reference them
- delete duplicate planning/memory docs after moving still-valid content into the canonical files
- every release should prefer current command output over historical commit IDs in docs

## Useful Files

- `00_START_HERE_AUTOSTOP_CRM.md`
- `MASTER-PLAN.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `scripts/doctor.ps1`
- `scripts/setup_dev.ps1`
- `scripts/run_checks.ps1`
- `scripts/run_quality_pass.ps1`
