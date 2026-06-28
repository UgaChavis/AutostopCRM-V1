#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

SERVICE_NAME="${AUTOSTOP_COMPOSE_SERVICE:-autostopcrm}"
SMOKE_ATTEMPTS="${AUTOSTOP_SMOKE_ATTEMPTS:-20}"
SMOKE_DELAY_SECONDS="${AUTOSTOP_SMOKE_DELAY_SECONDS:-3}"
SMOKE_OPERATOR_USERNAME="${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?set smoke username}"
SMOKE_OPERATOR_PASSWORD="${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:?set smoke password}"
DESKTOP_INSTRUCTION_PATH="${AUTOSTOP_DESKTOP_INSTRUCTION_PATH:-/root/Desktop/AUTOSTOPCRM_FULL_INSTRUCTION.txt}"
PUBLIC_SITE_URL="${AUTOSTOP_PUBLIC_SITE_URL:-}"
PUBLIC_MCP_URL="${AUTOSTOP_PUBLIC_MCP_URL:-}"
VERIFY_PUBLIC_HTTPS="${AUTOSTOP_VERIFY_PUBLIC_HTTPS:-0}"
DEPLOY_REMOTE="${AUTOSTOP_DEPLOY_REMOTE:-origin}"
DEPLOY_BRANCH="${AUTOSTOP_DEPLOY_BRANCH:-autostopcrm-v1}"
SKIP_GIT_SYNC="${AUTOSTOP_SKIP_GIT_SYNC:-0}"
INSTALL_WATCHDOG="${AUTOSTOP_INSTALL_WATCHDOG:-1}"

cd "$ROOT_DIR"

DEPLOY_LOCK_PATH="${AUTOSTOP_DEPLOY_LOCK_PATH:-$ROOT_DIR/.autostop-deploy.lock}"
if command -v flock >/dev/null 2>&1; then
  exec {DEPLOY_LOCK_FD}>"$DEPLOY_LOCK_PATH"
  if ! flock -n "$DEPLOY_LOCK_FD"; then
    echo "ERROR: another AutoStop CRM deploy is already running." >&2
    exit 1
  fi
else
  echo "WARN: flock is not available; watchdog deploy coordination is disabled." >&2
fi

if [[ "$SKIP_GIT_SYNC" != "1" ]]; then
  if git ls-remote --exit-code "$DEPLOY_REMOTE" "refs/heads/$DEPLOY_BRANCH" >/dev/null 2>&1; then
    echo "Syncing deployment checkout from $DEPLOY_REMOTE/$DEPLOY_BRANCH..."
    git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
    git reset --hard FETCH_HEAD
  else
    echo "WARN: git remote $DEPLOY_REMOTE branch $DEPLOY_BRANCH is not reachable; rebuilding current working tree." >&2
  fi
else
  echo "Skipping git sync because AUTOSTOP_SKIP_GIT_SYNC=1."
fi

docker compose up -d --build --remove-orphans
docker compose ps

container_id="$(docker compose ps -q "$SERVICE_NAME" 2>/dev/null || true)"
if [[ -n "$container_id" ]]; then
  for attempt in $(seq 1 "$SMOKE_ATTEMPTS"); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    if [[ "$state" == "healthy" || "$state" == "running" ]]; then
      break
    fi
    sleep "$SMOKE_DELAY_SECONDS"
  done
fi

smoke_ok=0
for attempt in $(seq 1 "$SMOKE_ATTEMPTS"); do
  if docker compose exec -T "$SERVICE_NAME" python scripts/check_live_connector.py \
    --strict \
    --skip-public-site \
    --skip-public-write-protection \
    --local-api-url http://127.0.0.1:41731 \
    --mcp-url http://127.0.0.1:41831/mcp \
    --operator-username "$SMOKE_OPERATOR_USERNAME" \
    --operator-password "$SMOKE_OPERATOR_PASSWORD" \
    --expect-admin
  then
    smoke_ok=1
    break
  fi
  sleep "$SMOKE_DELAY_SECONDS"
done

if [[ "$smoke_ok" -ne 1 ]]; then
  echo "ERROR: deploy smoke-check failed." >&2
  docker compose logs --tail=200 "$SERVICE_NAME" >&2 || true
  exit 1
fi

if [[ "$VERIFY_PUBLIC_HTTPS" == "1" ]]; then
  public_site_url="${PUBLIC_SITE_URL:-https://crm.autostopcrm.ru}"
  public_mcp_url="${PUBLIC_MCP_URL:-https://crm.autostopcrm.ru/mcp}"
  docker compose exec -T "$SERVICE_NAME" python scripts/check_live_connector.py \
    --strict \
    --site-url "$public_site_url" \
    --expect-https \
    --local-api-url http://127.0.0.1:41731 \
    --mcp-url "$public_mcp_url" \
    --operator-username "$SMOKE_OPERATOR_USERNAME" \
    --operator-password "$SMOKE_OPERATOR_PASSWORD" \
    --expect-admin
fi

if [[ -n "$DESKTOP_INSTRUCTION_PATH" ]]; then
  install -D -m 644 "$ROOT_DIR/AUTOSTOPCRM_FULL_INSTRUCTION.txt" "$DESKTOP_INSTRUCTION_PATH" 2>/dev/null || true
fi

if [[ "$INSTALL_WATCHDOG" == "1" ]]; then
  if [[ "$(id -u)" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
    bash "$ROOT_DIR/scripts/install_production_watchdog.sh"
  else
    echo "WARN: production watchdog install skipped; root and systemctl are required." >&2
  fi
else
  echo "Skipping production watchdog install because AUTOSTOP_INSTALL_WATCHDOG=0."
fi

echo "Deploy complete: container is up and smoke-check passed."
