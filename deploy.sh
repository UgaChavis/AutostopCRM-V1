#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="${AUTOSTOP_COMPOSE_SERVICE:-autostopcrm}"
SMOKE_ATTEMPTS="${AUTOSTOP_SMOKE_ATTEMPTS:-20}"
SMOKE_DELAY_SECONDS="${AUTOSTOP_SMOKE_DELAY_SECONDS:-3}"
SMOKE_OPERATOR_USERNAME="${AUTOSTOP_SMOKE_OPERATOR_USERNAME:-${MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME:-admin}}"
SMOKE_OPERATOR_PASSWORD="${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:-${MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD:-admin}}"
DESKTOP_INSTRUCTION_PATH="${AUTOSTOP_DESKTOP_INSTRUCTION_PATH:-/root/Desktop/AUTOSTOPCRM_FULL_INSTRUCTION.txt}"
PUBLIC_SITE_URL="${AUTOSTOP_PUBLIC_SITE_URL:-}"
PUBLIC_MCP_URL="${AUTOSTOP_PUBLIC_MCP_URL:-}"
VERIFY_PUBLIC_HTTPS="${AUTOSTOP_VERIFY_PUBLIC_HTTPS:-0}"

cd "$ROOT_DIR"

if git ls-remote --exit-code origin autostopCRM >/dev/null 2>&1; then
  git fetch origin autostopCRM
  git reset --hard origin/autostopCRM
else
  echo "WARN: git origin is not reachable from this server; skipping git pull and rebuilding current working tree." >&2
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

echo "Deploy complete: container is up and smoke-check passed."
