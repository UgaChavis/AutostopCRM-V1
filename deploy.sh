#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
fi

SERVICE_NAME="${AUTOSTOP_COMPOSE_SERVICE:-autostopcrm}"
CRM_DEPLOY_REMOTE="origin"
CRM_DEPLOY_BRANCH="autostopcrm-v1"
BUILD_RELEASE_IMAGE="${AUTOSTOP_BUILD_RELEASE_IMAGE:-1}"
STABLE_IMAGE="${AUTOSTOP_STABLE_IMAGE:-autostopcrm-autostopcrm:latest}"
MIN_FREE_DISK_BYTES="${AUTOSTOP_MIN_FREE_DISK_BYTES:-2147483648}"
BUILD_DISK_RESERVE_BYTES="${AUTOSTOP_BUILD_DISK_RESERVE_BYTES:-1073741824}"
MAX_DISK_BUDGET_BYTES=1099511627776
MAINTENANCE_BUDGET_SECONDS="${AUTOSTOP_MAINTENANCE_BUDGET_SECONDS:-600}"
ROLLBACK_RESERVE_SECONDS="${AUTOSTOP_ROLLBACK_RESERVE_SECONDS:-120}"
SMOKE_ATTEMPTS="${AUTOSTOP_SMOKE_ATTEMPTS:-20}"
SMOKE_DELAY_SECONDS="${AUTOSTOP_SMOKE_DELAY_SECONDS:-3}"
BACKUP_ROOT="${AUTOSTOP_RELEASE_BACKUP_ROOT:-/root/autostopcrm-backups/agent-gateway-v2}"
CRM_DATA_DIR="${AUTOSTOP_DATA_DIR:-$ROOT_DIR/data}"
MANAGER_DB="${AUTOSTOP_MANAGER_DB:-/opt/AutostopManager/data/autostop_manager.sqlite3}"
RUNTIME_UID="${AUTOSTOP_RUNTIME_UID:-10001}"
RUNTIME_GID="${AUTOSTOP_RUNTIME_GID:-10001}"
SEARXNG_RUNTIME_UID="${AUTOSTOP_SEARXNG_RUNTIME_UID:-977}"
SEARXNG_RUNTIME_GID="${AUTOSTOP_SEARXNG_RUNTIME_GID:-977}"
SEARXNG_CONFIG_DIR="${AUTOSTOP_SEARXNG_CONFIG_DIR:-$CRM_DATA_DIR/searxng/config}"
SEARXNG_CACHE_DIR="${AUTOSTOP_SEARXNG_CACHE_DIR:-$CRM_DATA_DIR/searxng/cache}"
MANAGER_SOURCE_DIR="${AUTOSTOP_MANAGER_SOURCE_DIR:-/opt/AutostopManager}"
MANAGER_DEPLOY_REMOTE="${AUTOSTOP_MANAGER_DEPLOY_REMOTE:-origin}"
MANAGER_DEPLOY_BRANCH="AutostopManager"
MANAGER_RELEASE_ROOT="${AUTOSTOP_MANAGER_RELEASE_ROOT:-/opt/autostop-manager-releases}"
MANAGER_CURRENT_LINK="${AUTOSTOP_MANAGER_CURRENT_LINK:-$MANAGER_RELEASE_ROOT/current}"
MANAGER_CONTAINER_DIR="${AUTOSTOP_MANAGER_CONTAINER_DIR:-/opt/AutostopManager}"
MANAGER_RELEASE_PYTHON="${AUTOSTOP_MANAGER_RELEASE_PYTHON:-$MANAGER_SOURCE_DIR/.venv/bin/python}"
MANAGER_CRM_MCP_ENV="/opt/AutostopManager/.crm-mcp.env"
MANAGER_MCP_ACTIVATE_ON_DEPLOY="${AUTOSTOP_MANAGER_MCP_ACTIVATE_ON_DEPLOY:-1}"
AUTOMATION_SERVICE_NAME="autostop-manager-scheduler.service"
AUTOMATION_STATE_DIR="/var/lib/autostop-manager-scheduler"
AUTOMATION_DB="$AUTOMATION_STATE_DIR/registry.sqlite3"
AUTOMATION_SOCKET="/run/autostop-manager-automation/control.sock"
WORK_TELEGRAM_RELEASE_LINK="/opt/autostop-work-telegram-releases/current"
WORK_TELEGRAM_STATE_DIR="/var/lib/autostop-work-telegram"
WORK_TELEGRAM_RUNTIME_DIR="/run/autostop-work-telegram"
WORK_TELEGRAM_OWNER_CONFIG="/etc/autostop-work-telegram/owner.json"
J1_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_ACTIVATE_ON_DEPLOY:-0}"
J1_UNIT_NAME="autostop-j1.service"
J1_UNIT_PATH="/etc/systemd/system/$J1_UNIT_NAME"
J1_BROWSER_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_BROWSER_ACTIVATE_ON_DEPLOY:-0}"
J1_BROWSER_UNIT_NAME="autostop-j1-browser.service"
J1_BROWSER_UNIT_PATH="/etc/systemd/system/$J1_BROWSER_UNIT_NAME"
J1_BROWSER_MARKER_PATH="/run/autostop-j1-browser-attestation/isolation-ready"
J1_BROWSER_MIN_MEM_AVAILABLE_KIB=2097152
J1_BROWSER_MIN_SWAP_FREE_KIB=1048576
MAINTENANCE_MARKER_HOST="${AUTOSTOP_MAINTENANCE_MARKER_HOST:-$CRM_DATA_DIR/.agent-gateway-maintenance}"
PUBLIC_SITE_URL="${AUTOSTOP_PUBLIC_SITE_URL:-https://crm.autostopcrm.ru}"
PUBLIC_MCP_URL="${AUTOSTOP_PUBLIC_MCP_URL:-https://crm.autostopcrm.ru/mcp}"
INSTALL_WATCHDOG="${AUTOSTOP_INSTALL_WATCHDOG:-0}"
RELEASE_BACKUP_RETENTION_COUNT="${AUTOSTOP_RELEASE_BACKUP_RETENTION_COUNT:-8}"
MANAGER_RELEASE_RETENTION_COUNT="${AUTOSTOP_MANAGER_RELEASE_RETENTION_COUNT:-6}"
RELEASE_IMAGE_RETENTION_COUNT="${AUTOSTOP_RELEASE_IMAGE_RETENTION_COUNT:-6}"
ROLLBACK_IMAGE_RETENTION_COUNT="${AUTOSTOP_ROLLBACK_IMAGE_RETENTION_COUNT:-4}"
PYTHON_BIN="${AUTOSTOP_RELEASE_PYTHON:-python3}"
STORE_NETWORK="${AUTOSTOP_STORE_NETWORK:-autostop-store-agent}"
STORE_APP_CONTAINER="${AUTOSTOP_STORE_APP_CONTAINER:-autostop-app}"
STORE_DB_CONTAINER="${AUTOSTOP_STORE_DB_CONTAINER:-autostop-db}"
CRM_CONTAINER="${AUTOSTOP_CRM_CONTAINER:-autostopcrm}"

if ! [[ "$MAINTENANCE_BUDGET_SECONDS" =~ ^[0-9]+$ ]] \
  || (( MAINTENANCE_BUDGET_SECONDS < 60 || MAINTENANCE_BUDGET_SECONDS > 600 )); then
  echo "ERROR: AUTOSTOP_MAINTENANCE_BUDGET_SECONDS must be between 60 and 600." >&2
  exit 2
fi
if ! [[ "$ROLLBACK_RESERVE_SECONDS" =~ ^[0-9]+$ ]] \
  || (( ROLLBACK_RESERVE_SECONDS < 60 \
      || ROLLBACK_RESERVE_SECONDS >= MAINTENANCE_BUDGET_SECONDS )); then
  echo "ERROR: AUTOSTOP_ROLLBACK_RESERVE_SECONDS must be at least 60 and below the maintenance budget." >&2
  exit 2
fi
if ! [[ "$SMOKE_ATTEMPTS" =~ ^[0-9]+$ ]] || (( SMOKE_ATTEMPTS < 1 || SMOKE_ATTEMPTS > 100 )); then
  echo "ERROR: AUTOSTOP_SMOKE_ATTEMPTS must be between 1 and 100." >&2
  exit 2
fi
if ! [[ "$SMOKE_DELAY_SECONDS" =~ ^[0-9]+$ ]] || (( SMOKE_DELAY_SECONDS > 30 )); then
  echo "ERROR: AUTOSTOP_SMOKE_DELAY_SECONDS must be between 0 and 30." >&2
  exit 2
fi
if ! [[ "$MIN_FREE_DISK_BYTES" =~ ^[0-9]+$ ]] \
  || (( MIN_FREE_DISK_BYTES < 1073741824 \
      || MIN_FREE_DISK_BYTES > MAX_DISK_BUDGET_BYTES )); then
  echo "ERROR: AUTOSTOP_MIN_FREE_DISK_BYTES must be between 1073741824 and $MAX_DISK_BUDGET_BYTES." >&2
  exit 2
fi
if ! [[ "$BUILD_DISK_RESERVE_BYTES" =~ ^[0-9]+$ ]] \
  || (( BUILD_DISK_RESERVE_BYTES < 536870912 \
      || BUILD_DISK_RESERVE_BYTES > MAX_DISK_BUDGET_BYTES )); then
  echo "ERROR: AUTOSTOP_BUILD_DISK_RESERVE_BYTES must be between 536870912 and $MAX_DISK_BUDGET_BYTES." >&2
  exit 2
fi
if [[ "$BUILD_RELEASE_IMAGE" != "1" ]]; then
  echo "ERROR: production deploy must build the CRM image from the verified commit." >&2
  exit 2
fi
if ! [[ "$RELEASE_BACKUP_RETENTION_COUNT" =~ ^[0-9]+$ ]] \
  || (( RELEASE_BACKUP_RETENTION_COUNT < 2 || RELEASE_BACKUP_RETENTION_COUNT > 100 )); then
  echo "ERROR: AUTOSTOP_RELEASE_BACKUP_RETENTION_COUNT must be between 2 and 100." >&2
  exit 2
fi
if ! [[ "$MANAGER_RELEASE_RETENTION_COUNT" =~ ^[0-9]+$ ]] \
  || (( MANAGER_RELEASE_RETENTION_COUNT < 2 || MANAGER_RELEASE_RETENTION_COUNT > 100 )); then
  echo "ERROR: AUTOSTOP_MANAGER_RELEASE_RETENTION_COUNT must be between 2 and 100." >&2
  exit 2
fi
if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" != "0" \
  && "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" != "1" ]]; then
  echo "ERROR: AUTOSTOP_MANAGER_MCP_ACTIVATE_ON_DEPLOY must be 0 or 1." >&2
  exit 2
fi
if [[ "$J1_ACTIVATE_ON_DEPLOY" != "0" && "$J1_ACTIVATE_ON_DEPLOY" != "1" ]]; then
  echo "ERROR: AUTOSTOP_J1_ACTIVATE_ON_DEPLOY must be 0 or 1." >&2
  exit 2
fi
if [[ "$J1_BROWSER_ACTIVATE_ON_DEPLOY" != "0" \
  && "$J1_BROWSER_ACTIVATE_ON_DEPLOY" != "1" ]]; then
  echo "ERROR: AUTOSTOP_J1_BROWSER_ACTIVATE_ON_DEPLOY must be 0 or 1." >&2
  exit 2
fi
for retention_count in "$RELEASE_IMAGE_RETENTION_COUNT" "$ROLLBACK_IMAGE_RETENTION_COUNT"; do
  if ! [[ "$retention_count" =~ ^[0-9]+$ ]] \
    || (( retention_count < 1 || retention_count > 100 )); then
    echo "ERROR: image retention counts must be between 1 and 100." >&2
    exit 2
  fi
done

export AUTOSTOP_DEPLOYMENT_ENV="production"
export AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED="0"
export AUTOSTOP_MCP_OAUTH_ENABLED="1"
export AUTOSTOP_AGENT_SERVICE_IDENTITY="${AUTOSTOP_AGENT_SERVICE_IDENTITY:-codex-owner-agent}"
validate_gateway_switches() {
  local switch_name switch_value
  for switch_name in \
    AUTOSTOP_AGENT_GATEWAY_ENABLED \
    AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED \
    AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED \
    AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED \
    AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED \
    AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED; do
    switch_value="${!switch_name-}"
    if [[ "$switch_value" != "0" && "$switch_value" != "1" ]]; then
      echo "ERROR: $switch_name must be explicitly provisioned as 0 or 1." >&2
      return 2
    fi
    export "${switch_name?}"
  done
}

validate_crawl4ai_credentials() {
  local api_token="${AUTOSTOP_CRAWL4AI_API_TOKEN-}"
  local secret_key="${AUTOSTOP_CRAWL4AI_SECRET_KEY-}"
  if [[ -z "$api_token" || -z "$secret_key" ]]; then
    echo "ERROR: AUTOSTOP_CRAWL4AI_API_TOKEN and AUTOSTOP_CRAWL4AI_SECRET_KEY must be provisioned." >&2
    return 2
  fi
  if [[ "$api_token" == "$secret_key" ]]; then
    echo "ERROR: AUTOSTOP_CRAWL4AI_API_TOKEN and AUTOSTOP_CRAWL4AI_SECRET_KEY must be distinct." >&2
    return 2
  fi
  export AUTOSTOP_CRAWL4AI_API_TOKEN AUTOSTOP_CRAWL4AI_SECRET_KEY
}

validate_gateway_switches
validate_crawl4ai_credentials
export AUTOSTOP_MAINTENANCE_MARKER="/home/autostop/.minimal-kanban/.agent-gateway-maintenance"
export MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL="$PUBLIC_SITE_URL"
export MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL="$PUBLIC_MCP_URL"
export AUTOSTOP_MANAGER_HOST_DIR="$MANAGER_CURRENT_LINK"

validate_store_network() {
  local require_crm="${1:-0}"
  local runner="${2:-}"
  local internal network_members member
  local -a inspect_command=(docker network inspect)
  if [[ -n "$runner" ]]; then
    inspect_command=("$runner" docker network inspect)
  fi
  if ! "${inspect_command[@]}" "$STORE_NETWORK" >/dev/null 2>&1; then
    echo "ERROR: precreated internal Docker network is unavailable: $STORE_NETWORK" >&2
    return 2
  fi
  internal="$("${inspect_command[@]}" --format '{{.Internal}}' "$STORE_NETWORK")"
  if [[ "$internal" != "true" ]]; then
    echo "ERROR: $STORE_NETWORK must be created with Docker internal=true." >&2
    return 2
  fi
  network_members="$(
    "${inspect_command[@]}" \
      --format '{{range .Containers}}{{println .Name}}{{end}}' "$STORE_NETWORK"
  )"
  if ! grep -Fxq "$STORE_APP_CONTAINER" <<<"$network_members"; then
    echo "ERROR: AutoStop App is not attached to $STORE_NETWORK." >&2
    return 2
  fi
  if grep -Fxq "$STORE_DB_CONTAINER" <<<"$network_members"; then
    echo "ERROR: the store database must never be attached to $STORE_NETWORK." >&2
    return 2
  fi
  while IFS= read -r member; do
    if [[ -n "$member" && "$member" != "$STORE_APP_CONTAINER" && "$member" != "$CRM_CONTAINER" ]]; then
      echo "ERROR: unexpected container is attached to the isolated Store agent network." >&2
      return 2
    fi
  done <<<"$network_members"
  if [[ "$require_crm" == "1" ]] && ! grep -Fxq "$CRM_CONTAINER" <<<"$network_members"; then
    echo "ERROR: AutoStop CRM is not attached to $STORE_NETWORK after replacement." >&2
    return 2
  fi
}

disk_available_bytes() {
  local target_path="${1:-$ROOT_DIR}"
  local available
  available="$(df --output=avail -B1 "$target_path" | tail -n 1 | tr -d '[:space:]')"
  if ! [[ "$available" =~ ^[0-9]+$ ]]; then
    echo "ERROR: could not determine available disk bytes." >&2
    return 2
  fi
  printf '%s\n' "$available"
}

require_disk_headroom() {
  local stage="$1"
  local required_bytes="$2"
  local target_path="${3:-$ROOT_DIR}"
  local available
  if ! [[ "$required_bytes" =~ ^[0-9]+$ ]] \
    || (( required_bytes < 1 || required_bytes > MAX_DISK_BUDGET_BYTES * 3 )); then
    echo "ERROR: invalid $stage disk headroom estimate." >&2
    return 2
  fi
  available="$(disk_available_bytes "$target_path")" || return $?
  if (( available < required_bytes )); then
    echo "ERROR: insufficient $stage disk headroom at $target_path; need $required_bytes bytes, have $available bytes." >&2
    return 2
  fi
}

protected_backup_source_bytes() {
  local total=0
  local candidate size
  for candidate in \
    "$CRM_DATA_DIR/state.json" \
    "$CRM_DATA_DIR/change_feed.sqlite3" \
    "$CRM_DATA_DIR/change_feed.sqlite3-wal" \
    "$CRM_DATA_DIR/change_feed.sqlite3-shm" \
    "$CRM_DATA_DIR/printing/completion_act_forms.json" \
    "$MANAGER_DB" \
    "$MANAGER_DB-wal" \
    "$MANAGER_DB-shm"; do
    if [[ -f "$candidate" ]]; then
      size="$(stat -c '%s' "$candidate")"
      [[ "$size" =~ ^[0-9]+$ ]] || return 2
      total=$(( total + size ))
    fi
  done
  if [[ -d "$CRM_DATA_DIR/printing/completion_act_forms" ]]; then
    size="$(du -sb "$CRM_DATA_DIR/printing/completion_act_forms" | awk '{print $1}')"
    [[ "$size" =~ ^[0-9]+$ ]] || return 2
    total=$(( total + size ))
  fi
  if [[ -d "$CRM_DATA_DIR/audit-archive" ]]; then
    size="$(du -sb "$CRM_DATA_DIR/audit-archive" | awk '{print $1}')"
    [[ "$size" =~ ^[0-9]+$ ]] || return 2
    total=$(( total + size ))
  fi
  if (( total < 1 || total > MAX_DISK_BUDGET_BYTES )); then
    return 2
  fi
  printf '%s\n' "$total"
}

DEPLOY_LOCK_PATH="${AUTOSTOP_DEPLOY_LOCK_PATH:-$ROOT_DIR/.autostop-deploy.lock}"
if ! command -v flock >/dev/null 2>&1; then
  echo "ERROR: flock is required for coordinated production replacement." >&2
  exit 2
fi
if ! command -v timeout >/dev/null 2>&1; then
  echo "ERROR: GNU timeout is required to enforce the maintenance deadline." >&2
  exit 2
fi
if ! command -v fuser >/dev/null 2>&1; then
  echo "ERROR: fuser is required for safe manager SQLite rollback." >&2
  exit 2
fi
exec {DEPLOY_LOCK_FD}>"$DEPLOY_LOCK_PATH"
if ! flock -n "$DEPLOY_LOCK_FD"; then
  echo "ERROR: another AutoStop CRM deploy is already running." >&2
  exit 1
fi

# shellcheck source=scripts/release_git_preflight.sh
. "$ROOT_DIR/scripts/release_git_preflight.sh"
crm_revision="$(
  release_git_verify_fetched_checkout \
    "AutoStop CRM" "$ROOT_DIR" "$CRM_DEPLOY_BRANCH" \
    "$CRM_DEPLOY_REMOTE" "$CRM_DEPLOY_BRANCH"
)"
manager_revision="$(
  release_git_verify_fetched_checkout \
    "AutoStopManager" "$MANAGER_SOURCE_DIR" "$MANAGER_DEPLOY_BRANCH" \
    "$MANAGER_DEPLOY_REMOTE" "$MANAGER_DEPLOY_BRANCH"
)"
CRM_APP_VERSION="$(
  PYTHONPATH="$ROOT_DIR/src" "$PYTHON_BIN" -c \
    'from minimal_kanban import __version__; print(__version__)'
)"
if [[ -z "$CRM_APP_VERSION" || ${#CRM_APP_VERSION} -gt 64 ]]; then
  echo "ERROR: CRM application version is unavailable or invalid." >&2
  exit 2
fi

"$PYTHON_BIN" scripts/configure_mcp_oauth.py ensure --env-file "$ROOT_DIR/.env"
set -a
# shellcheck disable=SC1091
. "$ROOT_DIR/.env"
set +a

: "${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?set smoke username}"
: "${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:?set smoke password}"
: "${AUTOSTOP_STORE_READ_TOKEN:?provision store read service token}"
: "${AUTOSTOP_STORE_QUOTE_TOKEN:?provision store quote service token}"
: "${AUTOSTOP_STORE_MANAGE_TOKEN:?provision store manage service token}"
: "${AUTOSTOP_STORE_OWNER_TOKEN:?provision store owner service token}"
export AUTOSTOP_SMOKE_OPERATOR_USERNAME AUTOSTOP_SMOKE_OPERATOR_PASSWORD
export AUTOSTOP_STORE_API_URL="${AUTOSTOP_STORE_API_URL:-http://autostop-app:8000}"
export AUTOSTOP_STORE_READ_TOKEN AUTOSTOP_STORE_QUOTE_TOKEN AUTOSTOP_STORE_MANAGE_TOKEN
export AUTOSTOP_STORE_OWNER_TOKEN

validate_store_network 0
docker compose config --quiet

release_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
release_revision="${crm_revision:0:12}"
release_id="${release_timestamp}-${release_revision}-$$"
automation_release_attempt_key="automation:${release_id}:${manager_revision:0:12}"
release_image_tag="${AUTOSTOP_RELEASE_IMAGE:-autostopcrm:${release_revision}}"
release_image=""
release_image_tag_previous_id=""
release_image_tag_cleanup_authorized=0
rollback_image="autostopcrm-rollback:${release_id}"
rollback_image_cleanup_authorized=0
maintenance_started=0
deployment_succeeded=0
maintenance_started_at=0
backup_dir=""
auth_rotated=0
rollback_active=0
previous_manager_dir=""
auth_backup_dir="$BACKUP_ROOT/.auth-rollback-$release_id"
manager_crm_mcp_backup_dir="$BACKUP_ROOT/.manager-crm-mcp-rollback-$release_id"
manager_release_dir="$MANAGER_RELEASE_ROOT/${release_id}-manager-${manager_revision:0:12}"
manager_release_staging_dir="${manager_release_dir}.partial-$$"
manager_attempt_cleanup_authorized=0
premaintenance_cleanup_done=0
manager_crm_mcp_snapshot_created=0
manager_crm_mcp_synced=0
manager_mcp_activation_attempted=0
j1_worker_activation_attempted=0
j1_worker_previous_unit_present=0
j1_worker_previous_active=0
j1_worker_previous_enabled=0
j1_worker_backup_dir="$BACKUP_ROOT/.j1-worker-rollback-$release_id"
j1_browser_activation_attempted=0
j1_browser_previous_unit_present=0
j1_browser_previous_active=0
j1_browser_previous_enabled=0
j1_browser_previous_marker_present=0
j1_browser_backup_dir="$BACKUP_ROOT/.j1-browser-rollback-$release_id"
automation_snapshot_root="$BACKUP_ROOT/.automation-rollback-$release_id"
automation_snapshot_dir="$automation_snapshot_root/state"
automation_registry_backup="$automation_snapshot_dir/registry-held.sqlite3"
automation_hold_readback="$automation_snapshot_dir/hold.json"
automation_manager_status="$automation_snapshot_dir/manager-status.json"
automation_telegram_status="$automation_snapshot_dir/telegram-status.json"
automation_telegram_effects="$automation_snapshot_dir/telegram-effects.json"
automation_feed_baseline=""
automation_snapshot_captured=0
automation_registry_preexisting=0
automation_hold_acquired=0
automation_scheduler_activation_attempted=0
work_telegram_inbound_before=0
work_telegram_duty_paused=0
work_telegram_release_attempted=0
automation_registry_backup_ready=0

cleanup_owned_premaintenance_artifacts() {
  if (( maintenance_started != 0 || premaintenance_cleanup_done != 0 )); then
    return 0
  fi
  premaintenance_cleanup_done=1
  local current_image_id=""
  local has_owned_artifacts=0
  local -a cleanup_command=(
    "$PYTHON_BIN" scripts/agent_release_retention.py cleanup-attempt
    --manager-release-root "$MANAGER_RELEASE_ROOT"
    --release-id "$release_id"
    --manager-revision "$manager_revision"
    --protected-image-tag "$STABLE_IMAGE"
  )
  if [[ -n "${previous_manager_dir:-}" && -e "$previous_manager_dir" ]]; then
    cleanup_command+=(--protected-manager-path "$previous_manager_dir")
  fi
  if (( manager_attempt_cleanup_authorized == 1 )); then
    for owned_manager_path in "$manager_release_staging_dir" "$manager_release_dir"; do
      if [[ -e "$owned_manager_path" || -L "$owned_manager_path" ]]; then
        cleanup_command+=(--owned-manager-path "$owned_manager_path")
        has_owned_artifacts=1
      fi
    done
  fi
  if (( release_image_tag_cleanup_authorized == 1 )); then
    current_image_id="$(
      docker image inspect --format '{{.Id}}' "$release_image_tag" 2>/dev/null || true
    )"
    if [[ -z "$release_image_tag_previous_id" ]]; then
      if [[ "$current_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        cleanup_command+=(--owned-image-tag "$release_image_tag" "$current_image_id")
        has_owned_artifacts=1
      fi
    elif [[ "$current_image_id" != "$release_image_tag_previous_id" ]]; then
      if [[ ! "$current_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
        current_image_id="absent"
      fi
      cleanup_command+=(
        --restore-image-tag "$release_image_tag" "$current_image_id"
        "$release_image_tag_previous_id"
      )
      has_owned_artifacts=1
    fi
  fi
  if (( rollback_image_cleanup_authorized == 1 )); then
    current_image_id="$(
      docker image inspect --format '{{.Id}}' "$rollback_image" 2>/dev/null || true
    )"
    if [[ -n "$current_image_id" ]]; then
      cleanup_command+=(--owned-image-tag "$rollback_image" "$previous_image_id")
      has_owned_artifacts=1
    fi
  fi
  if (( has_owned_artifacts == 0 )); then
    return 0
  fi
  "${cleanup_command[@]}"
}

premaintenance_on_exit() {
  local status="$?"
  trap - EXIT
  if (( status != 0 )); then
    cleanup_owned_premaintenance_artifacts || {
      echo "WARN: exact pre-maintenance artifact cleanup requires manual review." >&2
    }
  fi
  exit "$status"
}

verify_manager_snapshot_artifact() {
  local snapshot_dir="$1"
  local expected_revision="$2"
  local revision_path="$snapshot_dir/REVISION"
  local tree_path="$snapshot_dir/MANIFEST.files0"
  local manifest_path="$snapshot_dir/MANIFEST.sha256"
  if [[ ! -d "$snapshot_dir" ]]; then
    echo "ERROR: Manager snapshot artifact metadata is incomplete." >&2
    return 2
  fi
  if find "$snapshot_dir" -xdev -type l -print -quit | grep -q .; then
    echo "ERROR: Manager snapshot contains unsupported symbolic links." >&2
    return 2
  fi
  if [[ ! -f "$revision_path" ]] \
    || [[ ! -f "$tree_path" ]] \
    || [[ ! -f "$manifest_path" ]]; then
    echo "ERROR: Manager snapshot artifact metadata is incomplete." >&2
    return 2
  fi
  if [[ "$(cat "$revision_path")" != "$expected_revision" ]]; then
    echo "ERROR: Manager snapshot revision does not match the verified commit." >&2
    return 2
  fi
  if ! (
    cd "$snapshot_dir"
    cmp --silent MANIFEST.files0 <(
      find . -xdev -type f \
        ! -path "./MANIFEST.sha256" \
        ! -path "./MANIFEST.files0" \
        -print0 | LC_ALL=C sort -z
    ) && sha256sum --strict --check --status MANIFEST.sha256
  ); then
    echo "ERROR: Manager snapshot tree or checksum manifest does not verify." >&2
    return 2
  fi
  if find "$snapshot_dir" -xdev -perm /022 -print -quit | grep -q .; then
    echo "ERROR: Manager snapshot remains group- or other-writable." >&2
    return 2
  fi
}

validate_manager_snapshot_source_tree() {
  local snapshot_dir="$1"
  local reserved_path
  if [[ ! -d "$snapshot_dir" ]]; then
    echo "ERROR: Manager snapshot source directory is unavailable." >&2
    return 2
  fi
  if find "$snapshot_dir" -xdev -type l -print -quit | grep -q .; then
    echo "ERROR: Manager snapshot source contains unsupported symbolic links." >&2
    return 2
  fi
  if find "$snapshot_dir" -xdev ! -type d ! -type f -print -quit | grep -q .; then
    echo "ERROR: Manager snapshot source contains unsupported special files." >&2
    return 2
  fi
  for reserved_path in REVISION MANIFEST.files0 MANIFEST.sha256; do
    if [[ -e "$snapshot_dir/$reserved_path" || -L "$snapshot_dir/$reserved_path" ]]; then
      echo "ERROR: Manager snapshot source reserves $reserved_path for release metadata." >&2
      return 2
    fi
  done
}

snapshot_manager_commit() {
  local source_dir="$1"
  local target_dir="$2"
  local expected_revision="$3"
  local staging_dir="$manager_release_staging_dir"
  if [[ "$target_dir" != "$manager_release_dir" ]] \
    || [[ -e "$target_dir" || -L "$target_dir" ]] \
    || [[ -e "$staging_dir" || -L "$staging_dir" ]]; then
    echo "ERROR: Manager attempt artifact identity is not clean." >&2
    return 2
  fi
  manager_attempt_cleanup_authorized=1
  mkdir "$staging_dir"
  release_git_assert_exact_state \
    "AutoStopManager" "$source_dir" "$MANAGER_DEPLOY_BRANCH" \
    "$expected_revision" >/dev/null
  git -C "$source_dir" archive HEAD | tar -x -C "$staging_dir"
  release_git_assert_exact_state \
    "AutoStopManager" "$source_dir" "$MANAGER_DEPLOY_BRANCH" \
    "$expected_revision" >/dev/null
  validate_manager_snapshot_source_tree "$staging_dir"
  # Docker can overlay the live manager SQLite data only when the nested
  # mountpoint already exists inside the immutable read-only source snapshot.
  mkdir -p "$staging_dir/data"
  printf '%s\n' "$expected_revision" > "$staging_dir/REVISION"
  (
    cd "$staging_dir"
    find . -xdev -type f \
      ! -path "./MANIFEST.sha256" \
      ! -path "./MANIFEST.files0" \
      -print0 | LC_ALL=C sort -z > MANIFEST.files0
    xargs -0 -r sha256sum < MANIFEST.files0 > MANIFEST.sha256
  )
  # The mounted source must stay readable/traversable to the non-root CRM
  # process, while the release artifact itself remains root-owned and sealed.
  chmod -R u=rwX,go=rX "$staging_dir"
  verify_manager_snapshot_artifact "$staging_dir" "$expected_revision"
  mv "$staging_dir" "$target_dir"
}
activate_manager_snapshot() {
  local target_dir="$1"
  local next_link="${MANAGER_CURRENT_LINK}.next-$$"
  if (( maintenance_started == 1 )); then
    if (( rollback_active == 1 )); then
      run_maintenance ln -s "$target_dir" "$next_link"
      run_maintenance mv -Tf "$next_link" "$MANAGER_CURRENT_LINK"
    else
      run_release ln -s "$target_dir" "$next_link"
      run_release mv -Tf "$next_link" "$MANAGER_CURRENT_LINK"
    fi
  else
    ln -s "$target_dir" "$next_link"
    mv -Tf "$next_link" "$MANAGER_CURRENT_LINK"
  fi
}

run_isolated_manager_knowledge_preflight() (
  set -Eeuo pipefail
  local manager_knowledge_gate_dir=""
  cleanup_isolated_manager_knowledge_preflight() {
    if [[ "$manager_knowledge_gate_dir" != /tmp/autostopcrm-manager-knowledge.* ]] \
      || [[ ! -d "$manager_knowledge_gate_dir" ]]; then
      echo "ERROR: refusing an unsafe Manager knowledge preflight cleanup target." >&2
      return 2
    fi
    rm -rf -- "$manager_knowledge_gate_dir"
  }
  on_isolated_manager_knowledge_preflight_exit() {
    local original_status="$?"
    local cleanup_status=0
    trap - EXIT
    cleanup_isolated_manager_knowledge_preflight || cleanup_status="$?"
    if (( original_status == 0 && cleanup_status != 0 )); then
      exit "$cleanup_status"
    fi
    exit "$original_status"
  }

  # The snapshot was sealed from the verified Manager commit. Recheck it before
  # importing, then keep all pre-maintenance index writes in a disposable DB.
  verify_manager_snapshot_artifact "$manager_release_dir" "$manager_revision"
  manager_knowledge_gate_dir="$(mktemp -d /tmp/autostopcrm-manager-knowledge.XXXXXX)"
  trap on_isolated_manager_knowledge_preflight_exit EXIT
  # The host venv supplies only dependencies. The exact candidate snapshot,
  # not the mutable source checkout, supplies the Manager code and knowledge map.
  env \
    PYTHONPATH="$manager_release_dir" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_DB="$manager_knowledge_gate_dir/preflight.sqlite3" \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-sync
  env \
    PYTHONPATH="$manager_release_dir" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_DB="$manager_knowledge_gate_dir/preflight.sqlite3" \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-audit
)

sync_current_manager_knowledge() {
  local active_manager_dir expected_manager_dir
  active_manager_dir="$(run_release readlink -f "$MANAGER_CURRENT_LINK")"
  expected_manager_dir="$(run_release readlink -f "$manager_release_dir")"
  if [[ "$active_manager_dir" != "$expected_manager_dir" ]]; then
    echo "ERROR: current Manager release does not match the candidate knowledge index source." >&2
    return 2
  fi
  # The venv supplies only dependencies. PYTHONPATH pins the code and local
  # knowledge map to the immutable current snapshot, while the explicit DB
  # target is the protected persistent Manager SQLite captured for rollback.
  run_release env \
    PYTHONPATH="$MANAGER_CURRENT_LINK" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_DB="$MANAGER_DB" \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-sync
  run_release env \
    PYTHONPATH="$MANAGER_CURRENT_LINK" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_DB="$MANAGER_DB" \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-audit
}

if [[ ! -d "$MANAGER_SOURCE_DIR/autostop_manager" ]]; then
  echo "ERROR: AutoStopManager source is unavailable: $MANAGER_SOURCE_DIR" >&2
  exit 2
fi
if [[ ! -x "$MANAGER_RELEASE_PYTHON" ]]; then
  echo "ERROR: AutoStopManager release venv is unavailable: $MANAGER_RELEASE_PYTHON" >&2
  exit 2
fi
container_id="$(docker compose ps -q "$SERVICE_NAME" 2>/dev/null || true)"
if [[ -z "$container_id" ]]; then
  echo "ERROR: current CRM container is not running; refusing replacement without rollback source." >&2
  exit 2
fi
previous_image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
if [[ ! "$previous_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: current CRM image id is unavailable for rollback." >&2
  exit 2
fi
previous_image_size="$(docker image inspect --format '{{.Size}}' "$previous_image_id" 2>/dev/null)"
if ! [[ "$previous_image_size" =~ ^[0-9]+$ ]] \
  || (( previous_image_size < 1 || previous_image_size > MAX_DISK_BUDGET_BYTES )); then
  echo "ERROR: current CRM image size is invalid for the pre-build estimate." >&2
  exit 2
fi
manager_mount_source="$({
  docker inspect \
    --format '{{range .Mounts}}{{if eq .Destination "'"$MANAGER_CONTAINER_DIR"'"}}{{println .Source}}{{end}}{{end}}' \
    "$container_id"
} | sed -n '1p')"
if [[ -z "$manager_mount_source" ]] || [[ ! -d "$manager_mount_source/autostop_manager" ]]; then
  echo "ERROR: running CRM Manager mount source is unavailable; refusing an unverified code rollback." >&2
  exit 2
fi
previous_manager_dir="$(readlink -f "$manager_mount_source")"
if [[ -z "$previous_manager_dir" ]] || [[ ! -d "$previous_manager_dir/autostop_manager" ]]; then
  echo "ERROR: running CRM Manager mount source cannot be resolved." >&2
  exit 2
fi
trap premaintenance_on_exit EXIT
crm_archive_bytes="$(git -C "$ROOT_DIR" archive --format=tar "$crm_revision" | wc -c)"
manager_archive_bytes="$(
  git -C "$MANAGER_SOURCE_DIR" archive --format=tar "$manager_revision" | wc -c
)"
if ! [[ "$crm_archive_bytes" =~ ^[0-9]+$ ]] \
  || ! [[ "$manager_archive_bytes" =~ ^[0-9]+$ ]] \
  || (( crm_archive_bytes < 1 || crm_archive_bytes > MAX_DISK_BUDGET_BYTES \
      || manager_archive_bytes < 1 || manager_archive_bytes > MAX_DISK_BUDGET_BYTES )); then
  echo "ERROR: immutable source archive size is invalid for the pre-build estimate." >&2
  exit 2
fi
estimated_build_bytes=$((
  previous_image_size + BUILD_DISK_RESERVE_BYTES + crm_archive_bytes + manager_archive_bytes
))
prebuild_required_bytes=$(( MIN_FREE_DISK_BYTES + estimated_build_bytes ))
require_disk_headroom "pre-build" "$prebuild_required_bytes"

mkdir -p "$MANAGER_RELEASE_ROOT" "$BACKUP_ROOT"
chmod 0755 "$MANAGER_RELEASE_ROOT"
manager_release_required_bytes=$(( MIN_FREE_DISK_BYTES + manager_archive_bytes ))
require_disk_headroom \
  "manager-release" "$manager_release_required_bytes" "$MANAGER_RELEASE_ROOT"
"$PYTHON_BIN" scripts/agent_release_retention.py cleanup-attempt \
  --manager-release-root "$MANAGER_RELEASE_ROOT" \
  --release-id "$release_id" \
  --manager-revision "$manager_revision" \
  --protected-manager-path "$previous_manager_dir" \
  --protected-image-tag "$STABLE_IMAGE" >/dev/null
snapshot_manager_commit "$MANAGER_SOURCE_DIR" "$manager_release_dir" "$manager_revision"
run_isolated_manager_knowledge_preflight

# The catalog files live outside the Git snapshot. Populate their persistent
# private cache from the pinned public Manager release before maintenance, so
# a download or extraction failure cannot extend the CRM outage. The helper is
# taken only from the verified immutable Manager candidate. Its additions are
# durable data and remain available if a later code release rolls back.
catalog_sync_script="$manager_release_dir/scripts/sync_offline_parts_catalog_release.py"
if [[ ! -f "$catalog_sync_script" || -L "$catalog_sync_script" ]]; then
  echo "ERROR: Manager candidate lacks the offline catalog release sync helper." >&2
  exit 2
fi
env \
  PYTHONPATH="$manager_release_dir" \
  PYTHONSAFEPATH=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  AUTOSTOP_MANAGER_DB="$MANAGER_DB" \
  "$MANAGER_RELEASE_PYTHON" "$catalog_sync_script" \
    --cache-root "$(dirname "$MANAGER_DB")/offline_parts_catalogs"
require_disk_headroom "post-catalog" "$prebuild_required_bytes"

release_git_assert_exact_state \
  "AutoStop CRM" "$ROOT_DIR" "$CRM_DEPLOY_BRANCH" "$crm_revision" >/dev/null
if [[ "$release_image_tag" == "$STABLE_IMAGE" || "$release_image_tag" == "$rollback_image" ]]; then
  echo "ERROR: candidate, stable, and rollback Docker references must be distinct." >&2
  exit 2
fi
release_image_tag_previous_id="$(
  docker image inspect --format '{{.Id}}' "$release_image_tag" 2>/dev/null || true
)"
if [[ -n "$release_image_tag_previous_id" ]] \
  && [[ ! "$release_image_tag_previous_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: existing candidate Docker reference has an invalid image identity." >&2
  exit 2
fi
release_image_tag_cleanup_authorized=1
echo "Prebuilding immutable release image $release_image_tag before maintenance..."
# Stream the verified commit itself as the Docker context. A concurrent or
# ignored worktree mutation can therefore never enter the release image.
git -C "$ROOT_DIR" archive --format=tar "$crm_revision" \
  | docker build \
      --label "org.opencontainers.image.revision=$crm_revision" \
      --tag "$release_image_tag" -
release_git_assert_exact_state \
  "AutoStop CRM" "$ROOT_DIR" "$CRM_DEPLOY_BRANCH" "$crm_revision" >/dev/null
if ! release_image="$(docker image inspect --format '{{.Id}}' "$release_image_tag" 2>/dev/null)" \
  || [[ ! "$release_image" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: commit-built release image id is unavailable." >&2
  exit 2
fi
release_image_revision="$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
    "$release_image" 2>/dev/null
)"
if [[ "$release_image_revision" != "$crm_revision" ]]; then
  echo "ERROR: release image revision does not match the verified CRM commit." >&2
  exit 2
fi

require_disk_headroom "post-build" "$MIN_FREE_DISK_BYTES"
protected_source_bytes="$(protected_backup_source_bytes)" || {
  echo "ERROR: protected backup source size is invalid." >&2
  exit 2
}
premaintenance_required_bytes=$(( MIN_FREE_DISK_BYTES + protected_source_bytes * 2 ))
require_disk_headroom \
  "pre-maintenance-backup" "$premaintenance_required_bytes" "$BACKUP_ROOT"
if docker image inspect "$rollback_image" >/dev/null 2>&1; then
  echo "ERROR: exact rollback Docker reference already exists: $rollback_image" >&2
  exit 2
fi
rollback_image_cleanup_authorized=1
docker tag "$previous_image_id" "$rollback_image"
if [[ "$(docker image inspect --format '{{.Id}}' "$rollback_image")" != "$previous_image_id" ]]; then
  echo "ERROR: rollback Docker reference identity did not verify." >&2
  exit 2
fi

elapsed_seconds() {
  local now
  printf -v now '%(%s)T' -1
  echo $(( now - maintenance_started_at ))
}

remaining_budget() {
  local elapsed
  elapsed="$(elapsed_seconds)"
  echo $(( MAINTENANCE_BUDGET_SECONDS - elapsed ))
}

remaining_release_budget() {
  local elapsed
  elapsed="$(elapsed_seconds)"
  echo $(( MAINTENANCE_BUDGET_SECONDS - ROLLBACK_RESERVE_SECONDS - elapsed ))
}

run_maintenance() {
  local remaining command_budget
  remaining="$(remaining_budget)"
  command_budget=$(( remaining - 5 ))
  if (( command_budget <= 0 )); then
    echo "ERROR: maintenance budget exhausted before command: $1" >&2
    return 1
  fi
  timeout --signal=TERM --kill-after=5 "${command_budget}s" "$@" </dev/null
}

run_maintenance_from_stdin() {
  local remaining command_budget
  remaining="$(remaining_budget)"
  command_budget=$(( remaining - 5 ))
  if (( command_budget <= 0 )); then
    echo "ERROR: maintenance budget exhausted before command: $1" >&2
    return 1
  fi
  timeout --signal=TERM --kill-after=5 "${command_budget}s" "$@"
}

run_release() {
  local remaining command_budget
  remaining="$(remaining_release_budget)"
  command_budget=$(( remaining - 5 ))
  if (( command_budget <= 0 )); then
    echo "ERROR: release budget exhausted; starting bounded rollback." >&2
    return 1
  fi
  timeout --signal=TERM --kill-after=5 "${command_budget}s" "$@" </dev/null
}

run_release_from_stdin() {
  local remaining command_budget
  remaining="$(remaining_release_budget)"
  command_budget=$(( remaining - 5 ))
  if (( command_budget <= 0 )); then
    echo "ERROR: release budget exhausted; starting bounded rollback." >&2
    return 1
  fi
  timeout --signal=TERM --kill-after=5 "${command_budget}s" "$@"
}

assert_release_budget() {
  local remaining
  remaining="$(remaining_release_budget)"
  if (( remaining <= 0 )); then
    echo "ERROR: release exceeded its budget; rollback reserve is now active." >&2
    return 1
  fi
}

wait_for_health() {
  local image_ref="$1"
  local enforce_budget="${2:-1}"
  local current_id state attempt
  for (( attempt = 1; attempt <= SMOKE_ATTEMPTS; attempt++ )); do
    if [[ "$enforce_budget" == "1" ]]; then
      assert_release_budget || return 1
      if ! current_id="$(run_release env AUTOSTOP_RELEASE_IMAGE="$image_ref" docker compose ps -q "$SERVICE_NAME")"; then
        return 1
      fi
    else
      if ! current_id="$(run_maintenance env AUTOSTOP_RELEASE_IMAGE="$image_ref" docker compose ps -q "$SERVICE_NAME")"; then
        return 1
      fi
    fi
    if [[ -n "$current_id" ]]; then
      if [[ "$enforce_budget" == "1" ]]; then
        state="$(run_release docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$current_id")" || return 1
      else
        state="$(run_maintenance docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$current_id")" || return 1
      fi
      if [[ "$state" == "healthy" ]]; then
        return 0
      fi
    fi
    if [[ "$enforce_budget" == "1" ]]; then
      run_release sleep "$SMOKE_DELAY_SECONDS" || return 1
    else
      run_maintenance sleep "$SMOKE_DELAY_SECONDS" || return 1
    fi
  done
  return 1
}

wait_for_internal_store_gateway() {
  local attempt

  for (( attempt = 1; attempt <= SMOKE_ATTEMPTS; attempt++ )); do
    assert_release_budget || return 1
    if run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_agent_gateway_v2.py \
      --mcp-url http://127.0.0.1:41831/mcp \
      --require-store; then
      return 0
    fi

    if (( attempt < SMOKE_ATTEMPTS )); then
      echo "Store Gateway is not ready yet; retrying in ${SMOKE_DELAY_SECONDS}s (${attempt}/${SMOKE_ATTEMPTS})."
      run_release sleep "$SMOKE_DELAY_SECONDS" || return 1
    fi
  done

  return 1
}

wait_for_public_mcp_gateway() {
  local attempt

  for (( attempt = 1; attempt <= 3; attempt++ )); do
    assert_release_budget || return 1
    if run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_agent_gateway_v2.py \
      --mcp-url "$PUBLIC_MCP_URL"; then
      return 0
    fi

    if (( attempt < 3 )); then
      echo "Public MCP session is not ready yet; retrying in ${SMOKE_DELAY_SECONDS}s (${attempt}/3)."
      run_release sleep "$SMOKE_DELAY_SECONDS" || return 1
    fi
  done
  return 1
}

reload_deploy_environment() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT_DIR/.env"
    set +a
  fi
  export AUTOSTOP_DEPLOYMENT_ENV="production"
  export AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED="0"
  export AUTOSTOP_MCP_OAUTH_ENABLED="1"
  validate_gateway_switches
  validate_crawl4ai_credentials
  export MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL="$PUBLIC_SITE_URL"
  export MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL="$PUBLIC_MCP_URL"
  export AUTOSTOP_MANAGER_HOST_DIR="$MANAGER_CURRENT_LINK"
  export AUTOSTOP_STORE_API_URL="${AUTOSTOP_STORE_API_URL:-http://autostop-app:8000}"
  export AUTOSTOP_STORE_READ_TOKEN AUTOSTOP_STORE_QUOTE_TOKEN AUTOSTOP_STORE_MANAGE_TOKEN
  export AUTOSTOP_STORE_OWNER_TOKEN
}

restore_auth_configuration() {
  if (( auth_rotated != 1 )); then
    return 0
  fi
  local status=0
  if (( maintenance_started == 1 )); then
    run_maintenance "$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
      --server-env "$ROOT_DIR/.env" \
      restore --backup-dir "$auth_backup_dir" || status=$?
  else
    "$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
      --server-env "$ROOT_DIR/.env" \
      restore --backup-dir "$auth_backup_dir" || status=$?
  fi
  if (( status == 0 )); then
    if reload_deploy_environment; then
      auth_rotated=0
    else
      status=$?
    fi
  fi
  if (( status != 0 )); then
    echo "AUTH RECOVERY WARNING: private auth snapshot is preserved at $auth_backup_dir." >&2
  fi
  return "$status"
}

remove_auth_backup_if_safe() {
  if (( auth_rotated != 0 )); then
    echo "AUTH RECOVERY WARNING: refusing to remove the active auth snapshot at $auth_backup_dir." >&2
    return 1
  fi
  if ! rm -rf "$auth_backup_dir"; then
    echo "WARN: restored or committed auth snapshot cleanup failed at $auth_backup_dir." >&2
    return 1
  fi
}

sync_manager_crm_mcp_configuration() {
  run_release "$PYTHON_BIN" scripts/configure_manager_crm_mcp.py \
    --server-env "$ROOT_DIR/.env" \
    --manager-env "$MANAGER_CRM_MCP_ENV" \
    snapshot --backup-dir "$manager_crm_mcp_backup_dir"
  manager_crm_mcp_snapshot_created=1
  run_release "$PYTHON_BIN" scripts/configure_manager_crm_mcp.py \
    --server-env "$ROOT_DIR/.env" \
    --manager-env "$MANAGER_CRM_MCP_ENV" sync
  manager_crm_mcp_synced=1
  run_release "$PYTHON_BIN" scripts/configure_manager_crm_mcp.py \
    --server-env "$ROOT_DIR/.env" \
    --manager-env "$MANAGER_CRM_MCP_ENV" check
}

restore_manager_crm_mcp_configuration() {
  if (( ${manager_crm_mcp_synced:-0} != 1 )); then
    return 0
  fi
  if run_maintenance "$PYTHON_BIN" scripts/configure_manager_crm_mcp.py \
    --manager-env "$MANAGER_CRM_MCP_ENV" \
    restore --backup-dir "$manager_crm_mcp_backup_dir"; then
    manager_crm_mcp_synced=0
    return 0
  fi
  echo "MANAGER CRM MCP RECOVERY WARNING: private E8 configuration snapshot is preserved at $manager_crm_mcp_backup_dir." >&2
  return 1
}

remove_manager_crm_mcp_backup_if_safe() {
  if (( ${manager_crm_mcp_snapshot_created:-0} != 1 )); then
    return 0
  fi
  if (( ${manager_crm_mcp_synced:-0} != 0 )); then
    echo "MANAGER CRM MCP RECOVERY WARNING: refusing to remove the active private snapshot at $manager_crm_mcp_backup_dir." >&2
    return 1
  fi
  if ! rm -rf "$manager_crm_mcp_backup_dir"; then
    echo "WARN: committed Manager CRM MCP snapshot cleanup failed at $manager_crm_mcp_backup_dir." >&2
    return 1
  fi
  manager_crm_mcp_snapshot_created=0
}

activate_manager_native_mcp() {
  local target_dir="$1"
  local mode="$2"
  local active_manager_dir expected_manager_dir installer
  active_manager_dir="$(readlink -f "$MANAGER_CURRENT_LINK")"
  expected_manager_dir="$(readlink -f "$target_dir")"
  if [[ "$active_manager_dir" != "$expected_manager_dir" ]]; then
    echo "ERROR: Manager native MCP activation source is not the expected release." >&2
    return 2
  fi
  installer="$MANAGER_CURRENT_LINK/scripts/install-manager-mcp.sh"
  if [[ ! -x "$installer" || -L "$installer" ]]; then
    echo "ERROR: active Manager native MCP installer is unavailable." >&2
    return 2
  fi
  if [[ "$mode" == "release" ]]; then
    run_release "$installer" --replace-unit --activate
  else
    run_maintenance "$installer" --replace-unit --activate
  fi
}

run_automation_release_module() {
  local source_dir="$1"
  local budget_mode="$2"
  local operation="$3"
  local attempt_key="$4"
  local -a command=(
    env
    "PYTHONPATH=$source_dir"
    PYTHONSAFEPATH=1
    PYTHONDONTWRITEBYTECODE=1
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null
    "AUTOSTOP_AUTOMATION_DB=$AUTOMATION_DB"
    "AUTOSTOP_AUTOMATION_CONTROL_SOCKET=$AUTOMATION_SOCKET"
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.automation_release
    "$operation" --release-attempt-key "$attempt_key"
  )
  if [[ "$budget_mode" == "release" ]]; then
    run_release "${command[@]}"
  else
    run_maintenance "${command[@]}"
  fi
}

capture_manager_automation_status() {
  local source_dir="$1"
  local budget_mode="$2"
  local output="$3"
  local -a command=(
    env
    "PYTHONPATH=$source_dir"
    PYTHONSAFEPATH=1
    PYTHONDONTWRITEBYTECODE=1
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null
    "AUTOSTOP_AUTOMATION_CONTROL_SOCKET=$AUTOMATION_SOCKET"
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.automation_control status
  )
  if [[ "$budget_mode" == "release" ]]; then
    run_release "${command[@]}" >"$output"
  else
    run_maintenance "${command[@]}" >"$output"
  fi
}

validate_manager_automation_status() {
  local input="$1"
  local expected_hold="$2"
  local budget_mode="$3"
  local readiness_mode="${4:-}"
  local -a command=(
    "$PYTHON_BIN" scripts/check_automation_center_release.py manager
    --input "$input"
    --manager-revision "$manager_revision"
    --crm-revision "$crm_revision"
    --crm-version "$CRM_APP_VERSION"
    --release-attempt-key "$automation_release_attempt_key"
    --baseline-snapshot "$automation_snapshot_dir"
    "$expected_hold"
  )
  if [[ "$readiness_mode" == "require-dependencies-ready" ]]; then
    command+=(--require-dependencies-ready)
  fi
  if [[ "$budget_mode" == "release" ]]; then
    run_release "${command[@]}"
  else
    run_maintenance "${command[@]}"
  fi
}

capture_safe_work_telegram_status() {
  local expected_inbound="$1"
  local expected_revision="$2"
  local budget_mode="$3"
  local output="$4"
  local duty_script="$WORK_TELEGRAM_RELEASE_LINK/scripts/set-work-telegram-duty.sh"
  local -a validator=(
    "$PYTHON_BIN" scripts/check_automation_center_release.py telegram
    --release-link "$WORK_TELEGRAM_RELEASE_LINK"
    --owner-config "$WORK_TELEGRAM_OWNER_CONFIG"
  )
  if [[ "$expected_inbound" == "1" ]]; then
    validator+=(--expect-inbound)
  elif [[ "$expected_inbound" == "any" ]]; then
    validator+=(--allow-either-inbound)
  else
    validator+=(--expect-outbound-only)
  fi
  if [[ -n "$expected_revision" ]]; then
    validator+=(--expected-revision "$expected_revision")
  fi
  if [[ ! -x "$duty_script" || -L "$duty_script" ]]; then
    echo "ERROR: work Telegram duty controller is unavailable." >&2
    return 2
  fi
  if [[ "$budget_mode" == "release" ]]; then
    run_release "$duty_script" --status \
      | run_release_from_stdin "${validator[@]}" >"$output"
  else
    run_maintenance "$duty_script" --status \
      | run_maintenance_from_stdin "${validator[@]}" >"$output"
  fi
}

safe_telegram_status_has_inbound() {
  local input="$1"
  "$PYTHON_BIN" -c \
    'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); sys.exit(0 if value.get("inbound_enabled") is True else 1)' \
    "$input"
}

set_work_telegram_duty() {
  local enabled="$1"
  local budget_mode="$2"
  local duty_script="$WORK_TELEGRAM_RELEASE_LINK/scripts/set-work-telegram-duty.sh"
  local option="--disable"
  [[ "$enabled" == "1" ]] && option="--enable"
  if [[ ! -x "$duty_script" || -L "$duty_script" ]]; then
    echo "ERROR: work Telegram duty controller is unavailable." >&2
    return 2
  fi
  if [[ "$budget_mode" == "release" ]]; then
    run_release "$duty_script" "$option"
  else
    run_maintenance "$duty_script" "$option"
  fi
}

activate_manager_automation() {
  local installer="$MANAGER_CURRENT_LINK/scripts/install-manager-automation.sh"
  if [[ ! -x "$installer" || -L "$installer" ]]; then
    echo "ERROR: active Manager Automation Center installer is unavailable." >&2
    return 2
  fi
  automation_scheduler_activation_attempted=1
  run_release "$installer" \
    --activate-under-hold \
    --replace-unit \
    --manager-revision "$manager_revision" \
    --crm-revision "$crm_revision" \
    --crm-version "$CRM_APP_VERSION" \
    --release-attempt-key "$automation_release_attempt_key"
}

probe_manager_crm_feed_auth() {
  run_release "$PYTHON_BIN" scripts/probe_manager_crm_feed_auth.py \
    --manager-env "$MANAGER_CRM_MCP_ENV" \
    --unit "$AUTOMATION_SERVICE_NAME"
}

activate_work_telegram_release() {
  work_telegram_release_attempted=1
  run_release "$MANAGER_SOURCE_DIR/scripts/deploy_telegram_bridge.sh" \
    --account work --no-start "$manager_revision"
  local wake_installer="$WORK_TELEGRAM_RELEASE_LINK/scripts/install-codex-wake.sh"
  if [[ ! -x "$wake_installer" || -L "$wake_installer" ]]; then
    echo "ERROR: candidate work Telegram wake installer is unavailable." >&2
    return 2
  fi
  run_release "$wake_installer"
}

prepare_work_telegram_candidate() {
  local dependency_installer="$MANAGER_SOURCE_DIR/scripts/install-telegram-bridge.sh"
  local model_installer="$MANAGER_SOURCE_DIR/scripts/provision-telegram-transcription-model.sh"
  if [[ ! -x "$dependency_installer" || -L "$dependency_installer" \
    || ! -x "$model_installer" || -L "$model_installer" ]]; then
    echo "ERROR: work Telegram candidate installers are unavailable." >&2
    return 2
  fi
  run_release "$dependency_installer" --account work --revision "$manager_revision"
  run_release "$model_installer" --account work --revision "$manager_revision"
}

verify_active_crm_image() {
  local active_container active_image active_revision
  active_container="$(run_release env AUTOSTOP_RELEASE_IMAGE="$release_image" docker compose ps -q "$SERVICE_NAME")"
  [[ -n "$active_container" ]] || return 1
  active_image="$(run_release docker inspect --format '{{.Image}}' "$active_container")"
  [[ "$active_image" == "$release_image" ]] || return 1
  active_revision="$(
    run_release docker image inspect \
      --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
      "$active_image"
  )"
  [[ "$active_revision" == "$crm_revision" ]]
}

verify_automation_socket_mount() {
  local active_container mount_readback
  active_container="$(run_release env AUTOSTOP_RELEASE_IMAGE="$release_image" docker compose ps -q "$SERVICE_NAME")"
  [[ -n "$active_container" ]] || return 1
  mount_readback="$(
    run_release docker inspect --format \
      '{{range .Mounts}}{{if eq .Destination "/run/autostop-manager-automation"}}{{.Source}}|{{.RW}}{{end}}{{end}}' \
      "$active_container"
  )"
  [[ "$mount_readback" == "/run/autostop-manager-automation|false" ]]
}

snapshot_j1_worker_state() {
  if [[ -L "$J1_UNIT_PATH" || ( -e "$J1_UNIT_PATH" && ! -f "$J1_UNIT_PATH" ) ]]; then
    echo "ERROR: existing J1 systemd unit is not a regular file." >&2
    return 2
  fi
  run_release install -d -m 0700 "$j1_worker_backup_dir"
  if [[ -f "$J1_UNIT_PATH" ]]; then
    run_release install -m 0600 "$J1_UNIT_PATH" "$j1_worker_backup_dir/previous.service"
    j1_worker_previous_unit_present=1
  fi
  if systemctl is-active --quiet "$J1_UNIT_NAME"; then
    j1_worker_previous_active=1
  fi
  if systemctl is-enabled --quiet "$J1_UNIT_NAME"; then
    j1_worker_previous_enabled=1
  fi
  if (( j1_worker_previous_unit_present == 0 \
      && (j1_worker_previous_active == 1 || j1_worker_previous_enabled == 1) )); then
    echo "ERROR: active or enabled J1 unit has no restorable unit file." >&2
    return 2
  fi
}

activate_j1_worker() {
  local target_dir="$1"
  local active_manager_dir expected_manager_dir installer
  active_manager_dir="$(readlink -f "$MANAGER_CURRENT_LINK")"
  expected_manager_dir="$(readlink -f "$target_dir")"
  if [[ "$active_manager_dir" != "$expected_manager_dir" ]]; then
    echo "ERROR: J1 worker activation source is not the expected Manager release." >&2
    return 2
  fi
  installer="$MANAGER_CURRENT_LINK/scripts/install-j1-worker.sh"
  if [[ ! -x "$installer" || -L "$installer" ]]; then
    echo "ERROR: active Manager J1 installer is unavailable." >&2
    return 2
  fi
  run_release "$installer" --replace-unit --activate
}

restore_j1_worker_state() {
  # The candidate has already been stopped. Restore the exact previous unit,
  # enablement and running state only after the Manager current link is back.
  run_maintenance systemctl disable "$J1_UNIT_NAME" >/dev/null 2>&1 || true
  if (( j1_worker_previous_unit_present == 1 )); then
    if [[ ! -f "$j1_worker_backup_dir/previous.service" ]]; then
      echo "ROLLBACK CRITICAL: previous J1 unit snapshot is missing." >&2
      return 2
    fi
    run_maintenance install -o root -g root -m 0644 \
      "$j1_worker_backup_dir/previous.service" "$J1_UNIT_PATH" || return $?
  else
    run_maintenance rm -f "$J1_UNIT_PATH" || return $?
  fi
  run_maintenance systemctl daemon-reload || return $?
  if (( j1_worker_previous_enabled == 1 )); then
    run_maintenance systemctl enable "$J1_UNIT_NAME" || return $?
  elif systemctl is-enabled --quiet "$J1_UNIT_NAME"; then
    echo "ROLLBACK CRITICAL: J1 worker remained enabled unexpectedly." >&2
    return 2
  fi
  if (( j1_worker_previous_active == 1 )); then
    run_maintenance systemctl start "$J1_UNIT_NAME" || return $?
    run_maintenance systemctl is-active --quiet "$J1_UNIT_NAME" || return $?
  elif systemctl is-active --quiet "$J1_UNIT_NAME"; then
    echo "ROLLBACK CRITICAL: J1 worker remained active unexpectedly." >&2
    return 2
  fi
}

j1_browser_marker_is_sealed() {
  local marker_path="${1:-$J1_BROWSER_MARKER_PATH}"
  [[ -f "$marker_path" && ! -L "$marker_path" \
    && "$(stat -c '%u:%g:%a' "$marker_path" 2>/dev/null)" == "0:0:600" ]]
}

snapshot_j1_browser_state() {
  if [[ -L "$J1_BROWSER_UNIT_PATH" || ( -e "$J1_BROWSER_UNIT_PATH" && ! -f "$J1_BROWSER_UNIT_PATH" ) ]]; then
    echo "ERROR: existing J1 browser systemd unit is not a regular file." >&2
    return 2
  fi
  if [[ -e "$J1_BROWSER_MARKER_PATH" || -L "$J1_BROWSER_MARKER_PATH" ]] \
    && ! j1_browser_marker_is_sealed "$J1_BROWSER_MARKER_PATH"; then
    echo "ERROR: existing J1 browser attestation is not a root-owned 0600 regular file." >&2
    return 2
  fi
  run_release install -d -m 0700 "$j1_browser_backup_dir"
  if [[ -f "$J1_BROWSER_UNIT_PATH" ]]; then
    run_release install -o root -g root -m 0600 "$J1_BROWSER_UNIT_PATH" "$j1_browser_backup_dir/previous.service"
    j1_browser_previous_unit_present=1
  fi
  if j1_browser_marker_is_sealed "$J1_BROWSER_MARKER_PATH"; then
    run_release install -o root -g root -m 0600 \
      "$J1_BROWSER_MARKER_PATH" "$j1_browser_backup_dir/previous.marker"
    j1_browser_previous_marker_present=1
  fi
  if systemctl is-active --quiet "$J1_BROWSER_UNIT_NAME"; then
    j1_browser_previous_active=1
  fi
  if systemctl is-enabled --quiet "$J1_BROWSER_UNIT_NAME"; then
    j1_browser_previous_enabled=1
  fi
  if (( j1_browser_previous_unit_present == 0 \
      && (j1_browser_previous_active == 1 || j1_browser_previous_enabled == 1) )); then
    echo "ERROR: active or enabled J1 browser unit has no restorable unit file." >&2
    return 2
  fi
}

read_j1_browser_memory_kib() {
  local field="$1"
  awk -v field="$field" '$1 == field ":" && $2 ~ /^[0-9]+$/ { print $2; exit }' /proc/meminfo
}

read_j1_browser_swap_io() {
  awk '
    $1 == "pswpin" && $2 ~ /^[0-9]+$/ { input = $2; found_input = 1 }
    $1 == "pswpout" && $2 ~ /^[0-9]+$/ { output = $2; found_output = 1 }
    END {
      if (found_input && found_output) {
        print input " " output
      } else {
        exit 1
      }
    }
  ' /proc/vmstat
}

preflight_j1_browser_resources() {
  local memory_available swap_free swap_before swap_after swap_in_before swap_out_before swap_in_after swap_out_after
  memory_available="$(read_j1_browser_memory_kib "MemAvailable")"
  swap_free="$(read_j1_browser_memory_kib "SwapFree")"
  if ! [[ "$memory_available" =~ ^[0-9]+$ ]] || (( memory_available < J1_BROWSER_MIN_MEM_AVAILABLE_KIB )); then
    echo "ERROR: J1 browser activation requires at least 2 GiB MemAvailable." >&2
    return 2
  fi
  if ! [[ "$swap_free" =~ ^[0-9]+$ ]] || (( swap_free < J1_BROWSER_MIN_SWAP_FREE_KIB )); then
    echo "ERROR: J1 browser activation requires at least 1 GiB SwapFree." >&2
    return 2
  fi
  if ! swap_before="$(read_j1_browser_swap_io)"; then
    echo "ERROR: J1 browser activation could not read swap I/O counters." >&2
    return 2
  fi
  read -r swap_in_before swap_out_before <<<"$swap_before"
  if ! [[ "$swap_in_before" =~ ^[0-9]+$ && "$swap_out_before" =~ ^[0-9]+$ ]]; then
    echo "ERROR: J1 browser activation received invalid swap I/O counters." >&2
    return 2
  fi
  if (( maintenance_started == 1 )); then
    if (( $(remaining_release_budget) < 65 )); then
      echo "ERROR: J1 browser activation has insufficient release budget for its 60-second preflight." >&2
      return 2
    fi
    run_release sleep 60
  else
    timeout --signal=TERM --kill-after=5 65s sleep 60 </dev/null
  fi
  if ! swap_after="$(read_j1_browser_swap_io)"; then
    echo "ERROR: J1 browser activation could not re-read swap I/O counters." >&2
    return 2
  fi
  read -r swap_in_after swap_out_after <<<"$swap_after"
  if ! [[ "$swap_in_after" =~ ^[0-9]+$ && "$swap_out_after" =~ ^[0-9]+$ ]]; then
    echo "ERROR: J1 browser activation received invalid swap I/O counters." >&2
    return 2
  fi
  if (( swap_in_before != swap_in_after || swap_out_before != swap_out_after )); then
    echo "ERROR: J1 browser activation requires zero swap I/O for 60 seconds." >&2
    return 2
  fi
  # Re-read capacity after the quiet window: a browser must never claim the
  # last GiB merely because the host was healthy a minute earlier.
  memory_available="$(read_j1_browser_memory_kib "MemAvailable")"
  swap_free="$(read_j1_browser_memory_kib "SwapFree")"
  if ! [[ "$memory_available" =~ ^[0-9]+$ ]] || (( memory_available < J1_BROWSER_MIN_MEM_AVAILABLE_KIB )); then
    echo "ERROR: J1 browser activation no longer has at least 2 GiB MemAvailable." >&2
    return 2
  fi
  if ! [[ "$swap_free" =~ ^[0-9]+$ ]] || (( swap_free < J1_BROWSER_MIN_SWAP_FREE_KIB )); then
    echo "ERROR: J1 browser activation no longer has at least 1 GiB SwapFree." >&2
    return 2
  fi
}

activate_j1_browser() {
  local target_dir="$1"
  local active_manager_dir expected_manager_dir installer
  active_manager_dir="$(readlink -f "$MANAGER_CURRENT_LINK")"
  expected_manager_dir="$(readlink -f "$target_dir")"
  if [[ "$active_manager_dir" != "$expected_manager_dir" ]]; then
    echo "ERROR: J1 browser activation source is not the expected Manager release." >&2
    return 2
  fi
  installer="$MANAGER_CURRENT_LINK/scripts/install-j1-browser-stack.sh"
  if [[ ! -x "$installer" || -L "$installer" ]]; then
    echo "ERROR: active Manager J1 browser installer is unavailable." >&2
    return 2
  fi
  run_release "$installer" --replace-unit --activate
}

restore_j1_browser_state() {
  # The candidate stack has already been stopped. Restore the old unit and
  # its exact ready/not-ready state only after the Manager current link is back.
  run_maintenance systemctl disable "$J1_BROWSER_UNIT_NAME" >/dev/null 2>&1 || true
  if (( j1_browser_previous_unit_present == 1 )); then
    if [[ ! -f "$j1_browser_backup_dir/previous.service" ]]; then
      echo "ROLLBACK CRITICAL: previous J1 browser unit snapshot is missing." >&2
      return 2
    fi
    run_maintenance install -o root -g root -m 0644 \
      "$j1_browser_backup_dir/previous.service" "$J1_BROWSER_UNIT_PATH" || return $?
  else
    run_maintenance rm -f "$J1_BROWSER_UNIT_PATH" || return $?
  fi
  run_maintenance systemctl daemon-reload || return $?
  if (( j1_browser_previous_enabled == 1 )); then
    run_maintenance systemctl enable "$J1_BROWSER_UNIT_NAME" || return $?
  elif systemctl is-enabled --quiet "$J1_BROWSER_UNIT_NAME"; then
    echo "ROLLBACK CRITICAL: J1 browser unit remained enabled unexpectedly." >&2
    return 2
  fi
  if (( j1_browser_previous_active == 1 )); then
    run_maintenance systemctl start "$J1_BROWSER_UNIT_NAME" || return $?
    run_maintenance systemctl is-active --quiet "$J1_BROWSER_UNIT_NAME" || return $?
    if (( j1_browser_previous_marker_present == 1 )); then
      if [[ ! -f "$j1_browser_backup_dir/previous.marker" ]] \
        || ! j1_browser_marker_is_sealed "$j1_browser_backup_dir/previous.marker"; then
        echo "ROLLBACK CRITICAL: previous J1 browser attestation snapshot is missing or invalid." >&2
        return 2
      fi
      if [[ -e "$J1_BROWSER_MARKER_PATH" || -L "$J1_BROWSER_MARKER_PATH" ]] \
        && ! j1_browser_marker_is_sealed "$J1_BROWSER_MARKER_PATH"; then
        echo "ROLLBACK CRITICAL: restored J1 browser attestation path is unsafe." >&2
        return 2
      fi
      run_maintenance install -o root -g root -m 0600 \
        "$j1_browser_backup_dir/previous.marker" "$J1_BROWSER_MARKER_PATH" || return $?
    fi
  elif systemctl is-active --quiet "$J1_BROWSER_UNIT_NAME"; then
    echo "ROLLBACK CRITICAL: J1 browser unit remained active unexpectedly." >&2
    return 2
  fi
}

rollback_j1_browser_only() {
  # Restore browser files without reactivating a marker from another Manager SHA.

  if systemctl is-active --quiet "$J1_BROWSER_UNIT_NAME"; then
    run_maintenance systemctl stop "$J1_BROWSER_UNIT_NAME" || return $?
  fi
  if systemctl is-active --quiet "$J1_BROWSER_UNIT_NAME"; then
    echo "ERROR: candidate J1 browser stack remained active after its rollback." >&2
    return 2
  fi
  run_maintenance systemctl disable "$J1_BROWSER_UNIT_NAME" >/dev/null 2>&1 || true
  # A pre-release marker is intentionally not restored: it is bound to the
  # former Manager revision and must remain invalid under the new release.
  run_maintenance rm -f -- "$J1_BROWSER_MARKER_PATH" || return $?
  if (( j1_browser_previous_unit_present == 1 )); then
    if [[ ! -f "$j1_browser_backup_dir/previous.service" ]]; then
      echo "ERROR: previous J1 browser unit snapshot is missing." >&2
      return 2
    fi
    run_maintenance install -o root -g root -m 0644 \
      "$j1_browser_backup_dir/previous.service" "$J1_BROWSER_UNIT_PATH" || return $?
  else
    run_maintenance rm -f -- "$J1_BROWSER_UNIT_PATH" || return $?
  fi
  run_maintenance systemctl daemon-reload || return $?
  if systemctl is-enabled --quiet "$J1_BROWSER_UNIT_NAME"; then
    echo "ERROR: candidate J1 browser unit remained enabled after its rollback." >&2
    return 2
  fi
}

activate_j1_browser_optional() {
  local target_dir="$1"
  # Browser rendering is additive. A resource shortage or verifier rejection
  # leaves static J1 and the already-validated CRM/Manager release intact.
  if ! preflight_j1_browser_resources; then
    echo "WARN: J1 browser activation skipped; static J1 remains available." >&2
    return 0
  fi
  if ! snapshot_j1_browser_state; then
    echo "WARN: J1 browser activation skipped; previous browser state was not safe to snapshot." >&2
    return 0
  fi
  j1_browser_activation_attempted=1
  if ! activate_j1_browser "$target_dir"; then
    echo "WARN: J1 browser activation failed; rolling back browser stack only." >&2
    if ! rollback_j1_browser_only; then
      echo "ERROR: J1 browser stack could not be safely stopped; full release rollback is required." >&2
      return 2
    fi
    j1_browser_activation_attempted=0
    echo "WARN: J1 browser remains safely disabled; its prior marker belongs to another Manager revision." >&2
    return 0
  fi
  assert_release_budget
}

guard_coordinated_rollback() {
  if (( automation_snapshot_captured != 1 )); then
    return 0
  fi
  # Usually the original release hold is still active. If it was already
  # released immediately before a later pre-open failure, acquire a distinct
  # rollback attempt. If neither CAS path is available, stopping the scheduler
  # is the fail-closed fallback.
  if run_automation_release_module \
    "$manager_release_dir" maintenance hold "$automation_release_attempt_key" \
    >/dev/null 2>&1; then
    automation_hold_acquired=1
  elif run_automation_release_module \
    "$manager_release_dir" maintenance hold "${automation_release_attempt_key}:rollback" \
    >/dev/null 2>&1; then
    automation_hold_acquired=1
  else
    run_maintenance systemctl stop "$AUTOMATION_SERVICE_NAME" >/dev/null 2>&1 || true
    if systemctl is-active --quiet "$AUTOMATION_SERVICE_NAME"; then
      echo "ROLLBACK CRITICAL: scheduler could not be held or stopped." >&2
      return 2
    fi
  fi
  if (( work_telegram_duty_paused != 1 )); then
    if set_work_telegram_duty 0 maintenance >/dev/null 2>&1; then
      work_telegram_duty_paused=1
    else
      run_maintenance systemctl stop autostop-codex-wake.service || true
      run_maintenance systemctl stop autostop-work-telegram.service || return $?
    fi
  fi
}

restore_coordinated_release_state() {
  if (( automation_snapshot_captured != 1 )); then
    return 0
  fi
  local -a command=(
    "$PYTHON_BIN" scripts/coordinated_release_state.py restore
    --snapshot "$automation_snapshot_dir"
  )
  if (( automation_registry_preexisting == 1 )); then
    if (( automation_registry_backup_ready != 1 )) \
      || [[ ! -f "$automation_registry_backup" ]]; then
      echo "ROLLBACK CRITICAL: scheduler registry backup is unavailable." >&2
      return 2
    fi
    command+=(--database-backup "$automation_registry_backup")
  fi
  run_maintenance "${command[@]}" || return $?
  work_telegram_duty_paused=0
  work_telegram_release_attempted=0
  automation_scheduler_activation_attempted=0
}

rollback_release() {
  local original_status="$1"
  local rollback_ok=1
  local marker_rearmed=1
  set +e
  rollback_active=1
  # Re-arm write protection before any diagnostic, stop, or restore action.
  # This remains a direct local operation so an exhausted command budget
  # cannot reopen a write window during rollback.
  if ! install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST"; then
    echo "ROLLBACK CRITICAL: maintenance marker could not be re-armed." >&2
    rollback_ok=0
    marker_rearmed=0
  fi
  if ! guard_coordinated_rollback; then
    echo "ROLLBACK CRITICAL: coordinated scheduler/Telegram guard failed." >&2
    rollback_ok=0
  fi
  if (( ${j1_browser_activation_attempted:-0} == 1 )) \
    && systemctl is-active --quiet "$J1_BROWSER_UNIT_NAME"; then
    run_maintenance systemctl stop "$J1_BROWSER_UNIT_NAME" || rollback_ok=0
  fi
  if (( ${j1_worker_activation_attempted:-0} == 1 )) \
    && systemctl is-active --quiet "$J1_UNIT_NAME"; then
    run_maintenance systemctl stop "$J1_UNIT_NAME" || rollback_ok=0
  fi
  # Restore the stable reference before any rollback operation can exhaust the
  # reserve. Retagging does not affect the running container, while it prevents
  # a watchdog from ever restarting the failed candidate image.
  if ! timeout --signal=TERM --kill-after=5 30s \
    docker tag "$rollback_image" "$STABLE_IMAGE" </dev/null; then
    echo "ROLLBACK CRITICAL: stable image reference could not be restored." >&2
    rollback_ok=0
  fi
  echo "ROLLBACK: restoring the previous CRM image and changed protected data." >&2
  if ! run_maintenance env AUTOSTOP_RELEASE_IMAGE="$release_image" \
    docker compose stop --timeout 20 "$SERVICE_NAME" >/dev/null 2>&1; then
    echo "ROLLBACK CRITICAL: candidate CRM could not be stopped; protected data remains untouched and maintenance stays active; the auth snapshot is preserved." >&2
    set -e
    return "$original_status"
  fi
  if (( automation_snapshot_captured == 1 )); then
    run_maintenance "$PYTHON_BIN" scripts/coordinated_release_state.py \
      stop-candidates || rollback_ok=0
  fi
  if (( marker_rearmed == 0 )); then
    echo "ROLLBACK CRITICAL: CRM remains stopped because write protection is unavailable." >&2
    if restore_auth_configuration; then
      if (( ${manager_crm_mcp_snapshot_created:-0} == 1 )); then
        restore_manager_crm_mcp_configuration || true
      fi
      remove_auth_backup_if_safe || true
    fi
    set -e
    return "$original_status"
  fi
  if [[ -n "$backup_dir" && -d "$backup_dir" ]]; then
    local fuser_status=0
    run_maintenance "$PYTHON_BIN" scripts/agent_release_backup.py \
      verify --backup-dir "$backup_dir" >&2 || rollback_ok=0
    run_maintenance "$PYTHON_BIN" scripts/agent_release_backup.py \
      restore-crm-changed --backup-dir "$backup_dir" >&2 || rollback_ok=0
    run_maintenance fuser "$MANAGER_DB" >/dev/null 2>&1 || fuser_status=$?
    if (( fuser_status == 0 )); then
      echo "ROLLBACK WARNING: manager SQLite is open; only Manager DB restore was skipped." >&2
      rollback_ok=0
    elif (( fuser_status == 1 )); then
      run_maintenance "$PYTHON_BIN" scripts/agent_release_backup.py \
        restore-manager-changed --backup-dir "$backup_dir" >&2 || rollback_ok=0
    else
      echo "ROLLBACK WARNING: could not verify Manager SQLite ownership; Manager DB restore was skipped." >&2
      rollback_ok=0
    fi
  fi
  restore_auth_configuration || rollback_ok=0
  if (( ${manager_crm_mcp_snapshot_created:-0} == 1 )); then
    restore_manager_crm_mcp_configuration || rollback_ok=0
  fi
  activate_manager_snapshot "$previous_manager_dir" || rollback_ok=0
  if (( automation_snapshot_captured == 1 )); then
    restore_coordinated_release_state || rollback_ok=0
  fi
  if (( ${j1_worker_activation_attempted:-0} == 1 )); then
    restore_j1_worker_state || rollback_ok=0
  fi
  if (( ${j1_browser_activation_attempted:-0} == 1 )); then
    restore_j1_browser_state || rollback_ok=0
  fi
  if (( ${manager_mcp_activation_attempted:-0} == 1 )); then
    activate_manager_native_mcp "$previous_manager_dir" maintenance || rollback_ok=0
  fi
  run_maintenance env AUTOSTOP_RELEASE_IMAGE="$rollback_image" docker compose up \
    -d --no-deps --no-build --force-recreate "$SERVICE_NAME" >&2 || rollback_ok=0
  if wait_for_health "$rollback_image" 0; then
    # Never reopen writes after an incomplete protected-data, Manager, auth,
    # image, or health rollback, even when the old container itself is healthy.
    if (( rollback_ok == 1 && automation_registry_preexisting == 1 )); then
      run_automation_release_module \
        "$previous_manager_dir" maintenance release-hold \
        "$automation_release_attempt_key" || rollback_ok=0
    fi
    if (( rollback_ok == 1 )); then
      run_maintenance rm -f "$MAINTENANCE_MARKER_HOST" || rollback_ok=0
    fi
    if (( rollback_ok == 1 )); then
      echo "ROLLBACK: previous CRM image and protected state are healthy." >&2
    else
      echo "ROLLBACK INCOMPLETE: maintenance marker remains; manual recovery is required." >&2
    fi
  else
    rollback_ok=0
    echo "ROLLBACK FAILED: maintenance marker remains in place; manual recovery is required." >&2
  fi
  if (( auth_rotated == 0 )); then
    remove_auth_backup_if_safe || true
  else
    echo "ROLLBACK CRITICAL: auth recovery is incomplete; private snapshot remains at $auth_backup_dir." >&2
  fi
  if (( ${manager_crm_mcp_snapshot_created:-0} == 1 )); then
    if (( ${manager_crm_mcp_synced:-0} == 0 )); then
      remove_manager_crm_mcp_backup_if_safe || true
    else
      echo "ROLLBACK CRITICAL: Manager CRM MCP recovery is incomplete; private snapshot remains at $manager_crm_mcp_backup_dir." >&2
    fi
  fi
  if (( rollback_ok == 0 )); then
    echo "ROLLBACK completed with warnings; inspect protected data and auth state." >&2
  fi
  set -e
  return "$original_status"
}

on_exit() {
  local status="$?"
  trap - EXIT
  if (( status != 0 && maintenance_started == 1 && deployment_succeeded == 0 )); then
    rollback_release "$status" || true
  elif (( status != 0 && auth_rotated == 1 )); then
    if restore_auth_configuration; then
      remove_auth_backup_if_safe || true
    fi
  fi
  if (( status != 0 && maintenance_started == 0 )); then
    cleanup_owned_premaintenance_artifacts || {
      echo "WARN: exact pre-maintenance artifact cleanup requires manual review." >&2
    }
  fi
  exit "$status"
}
trap on_exit EXIT

# The internal bearer changes only after all builds and rollback images are
# ready. Codex OAuth configuration is independent of deploy.
"$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
  --server-env "$ROOT_DIR/.env" \
  snapshot --backup-dir "$auth_backup_dir"
auth_rotated=1
if ! "$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
  --server-env "$ROOT_DIR/.env" \
  rotate --generate; then
  exit 2
fi
reload_deploy_environment
"$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
  --server-env "$ROOT_DIR/.env" \
  check
"$PYTHON_BIN" scripts/validate_production_env.py --require-production --require-store
docker compose config --quiet

maintenance_started=1
printf -v maintenance_started_at '%(%s)T' -1
run_release install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST"

if [[ -f "$AUTOMATION_DB" && ! -L "$AUTOMATION_DB" ]]; then
  automation_registry_preexisting=1
fi
run_release install -d -o root -g root -m 0700 "$automation_snapshot_root"
run_release "$PYTHON_BIN" scripts/coordinated_release_state.py capture \
  --output "$automation_snapshot_dir"
automation_snapshot_captured=1
run_release "$PYTHON_BIN" scripts/coordinated_release_state.py verify \
  --snapshot "$automation_snapshot_dir"
run_release "$PYTHON_BIN" scripts/check_automation_center_release.py \
  capture-telegram-effects \
  --state-dir "$WORK_TELEGRAM_STATE_DIR" \
  --runtime-dir "$WORK_TELEGRAM_RUNTIME_DIR" \
  --output "$automation_telegram_effects"
capture_safe_work_telegram_status any "" release "$automation_telegram_status"
if safe_telegram_status_has_inbound "$automation_telegram_status"; then
  work_telegram_inbound_before=1
fi

# Acquire the scheduler hold before any schema/install mutation. The command
# owns the hold with this unique release attempt and returns only after leases
# and sending outbox claims are quiescent.
run_automation_release_module \
  "$manager_release_dir" release hold "$automation_release_attempt_key" \
  >"$automation_hold_readback"
run_release "$PYTHON_BIN" scripts/check_automation_center_release.py hold \
  --input "$automation_hold_readback" \
  --release-attempt-key "$automation_release_attempt_key"
automation_hold_acquired=1
run_release "$MANAGER_RELEASE_PYTHON" \
  "$manager_release_dir/scripts/backup-manager-automation-state.py" \
  --source "$AUTOMATION_DB" --output "$automation_registry_backup"
automation_registry_backup_ready=1

# Pause only inbound duty. The local outbound transport remains active for its
# readiness probe, but this release never invokes a send operation.
set_work_telegram_duty 0 release
work_telegram_duty_paused=1
capture_safe_work_telegram_status 0 "" release "$automation_telegram_status"
# Prepare dependencies and the local transcription model only after the live
# Telegram links/config have a rollback snapshot and inbound duty is paused.
# This does not read chats or send a message.
prepare_work_telegram_candidate

echo "Maintenance window started; stopping only $SERVICE_NAME."
run_release docker compose stop --timeout 20 "$SERVICE_NAME"
assert_release_budget

run_release "$PYTHON_BIN" scripts/agent_release_backup.py create \
  --output-root "$BACKUP_ROOT" \
  --crm-data-dir "$CRM_DATA_DIR" \
  --manager-db "$MANAGER_DB" \
  --backup-id "$release_id"
backup_dir="$BACKUP_ROOT/$release_id"
run_release "$PYTHON_BIN" scripts/agent_release_backup.py verify --backup-dir "$backup_dir"
assert_release_budget

run_release mv -T "$automation_snapshot_dir" "$backup_dir/coordinated-release-state"
automation_snapshot_dir="$backup_dir/coordinated-release-state"
automation_registry_backup="$automation_snapshot_dir/registry-held.sqlite3"
automation_hold_readback="$automation_snapshot_dir/hold.json"
automation_manager_status="$automation_snapshot_dir/manager-status.json"
automation_telegram_status="$automation_snapshot_dir/telegram-status.json"
automation_telegram_effects="$automation_snapshot_dir/telegram-effects.json"
run_release rmdir "$automation_snapshot_root"

automation_feed_baseline="$backup_dir/automation-feed-baseline.json"
if [[ -f "$CRM_DATA_DIR/change_feed.sqlite3" ]]; then
  run_release "$PYTHON_BIN" scripts/check_automation_center_release.py capture-feed \
    --database "$CRM_DATA_DIR/change_feed.sqlite3" \
    --output "$automation_feed_baseline"
fi

if [[ -f "$CRM_DATA_DIR/change_feed.sqlite3" ]]; then
  run_release "$PYTHON_BIN" -m scripts.cleanup_audit_probe_consumer \
    --database "$CRM_DATA_DIR/change_feed.sqlite3"
  run_release "$PYTHON_BIN" -m scripts.cleanup_audit_probe_consumer \
    --database "$CRM_DATA_DIR/change_feed.sqlite3" \
    --backup-dir "$backup_dir" \
    --apply
fi
assert_release_budget

activate_manager_snapshot "$manager_release_dir"
# Knowledge sync intentionally changes the persistent Manager index only after
# its verified rollback backup exists and the immutable candidate is current.
# Any failure exits under the armed maintenance trap, which restores both the
# Manager SQLite and the previous current symlink before CRM is restarted.
sync_current_manager_knowledge
assert_release_budget

# Scheduler activation is bound to the sealed Manager/CRM revisions and starts
# under the already-owned hold. Its installer adopts the five live timers only
# when absent and seeds the singleton digest strictly OFF.
activate_manager_automation
capture_manager_automation_status \
  "$MANAGER_CURRENT_LINK" release "$automation_manager_status"
validate_manager_automation_status "$automation_manager_status" --expect-held release
assert_release_budget

# Switch the work bridge while inbound duty is paused. The deploy script owns
# its immutable runtime rollback; the coordinated snapshot covers any later
# candidate failure. This performs only status/self-checks, never a send.
activate_work_telegram_release
capture_safe_work_telegram_status \
  0 "$manager_revision" release "$automation_telegram_status"
assert_release_budget

# The hardened image runs without root. Migrate only the two persisted data
# trees after the verified backup and while the CRM container is stopped.
run_release chown -R "$RUNTIME_UID:$RUNTIME_GID" "$CRM_DATA_DIR" "$(dirname "$MANAGER_DB")"
# The CRM data tree also contains SearXNG bind mounts. Restore their dedicated
# non-root owner after the broad CRM ownership migration so search survives a
# deploy and the next container restart.
for searxng_dir in "$SEARXNG_CONFIG_DIR" "$SEARXNG_CACHE_DIR"; do
  if [[ -d "$searxng_dir" ]]; then
    run_release chown -R "$SEARXNG_RUNTIME_UID:$SEARXNG_RUNTIME_GID" "$searxng_dir"
  fi
done

run_release env AUTOSTOP_RELEASE_IMAGE="$release_image" docker compose up \
  -d --no-deps --no-build --force-recreate "$SERVICE_NAME"
if ! wait_for_health "$release_image"; then
  echo "ERROR: release container did not become healthy." >&2
  exit 1
fi
assert_release_budget
validate_store_network 1 run_release
verify_active_crm_image
verify_automation_socket_mount

run_release docker compose exec -T "$SERVICE_NAME" python \
  scripts/check_automation_center_release.py crm \
  --base-url http://127.0.0.1:41731 \
  --username "$AUTOSTOP_SMOKE_OPERATOR_USERNAME" \
  --password "$AUTOSTOP_SMOKE_OPERATOR_PASSWORD" \
  --manager-revision "$manager_revision" \
  --socket /run/autostop-manager-automation/control.sock \
  --manager-revision-path "$MANAGER_CONTAINER_DIR/REVISION" \
  --expect-held

run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_live_connector.py \
  --strict \
  --skip-public-site \
  --skip-public-write-protection \
  --skip-mcp \
  --local-api-url http://127.0.0.1:41731 \
  --expect-admin

wait_for_internal_store_gateway

# Run the long Store-read/feed/web smoke while public CRM writes remain blocked.
# The public MCP URL still verifies OAuth and the anonymous 401/403 boundary.
run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_agent_gateway_v2.py \
  --mcp-url "$PUBLIC_MCP_URL" \
  --exhaustive \
  --require-store \
  --require-web \
  --maintenance-safe \
  --release-revision "$crm_revision" \
  --release-attempt-id "$release_id"
run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_mcp_oauth.py \
  --mcp-url "$PUBLIC_MCP_URL"

# E8 credentials are synced only after the candidate CRM has passed its
# internal/public read-only Gateway and OAuth checks.  The helper writes only
# the fixed loopback CRM MCP URL and current internal bearer into Manager's
# separate root-only environment file, with a recoverable pre-sync snapshot.
sync_manager_crm_mcp_configuration
assert_release_budget

# The scheduler was first started while the previous Manager credential file
# was still installed. Restart it under the same owned hold so the live process
# imports the just-synchronised bearer, then prove the exact process credential
# can access CRM's non-mutating change-feed readiness route. The probe neither
# registers the digest consumer nor creates a delivery/ACK.
activate_manager_automation
probe_manager_crm_feed_auth
capture_manager_automation_status \
  "$MANAGER_CURRENT_LINK" release "$automation_manager_status"
validate_manager_automation_status \
  "$automation_manager_status" --expect-held release require-dependencies-ready
assert_release_budget

if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" == "1" ]]; then
  # The active Manager installer owns its bounded listener/native-MCP probe.
  # Mark the attempt first so rollback restores the previous service even when
  # candidate activation stops halfway through.
  manager_mcp_activation_attempted=1
  activate_manager_native_mcp "$manager_release_dir" release
  assert_release_budget
fi
if [[ "$J1_ACTIVATE_ON_DEPLOY" == "1" ]]; then
  snapshot_j1_worker_state
  # Rollback must restore the old unit even if candidate installation succeeds
  # but worker startup or the probe fails partway through.
  j1_worker_activation_attempted=1
  activate_j1_worker "$manager_release_dir"
  assert_release_budget
fi
if [[ "$J1_BROWSER_ACTIVATE_ON_DEPLOY" == "1" ]]; then
  # Browser activation is an optional capability after the static release has
  # passed its protected checks. Its own failure must not discard CRM/Manager.
  activate_j1_browser_optional "$manager_release_dir"
fi

# Public site/auth/health probes are non-mutating and run while the marker is
# still active. Removing the marker is the final fallible release action.
run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_live_connector.py \
  --strict \
  --site-url "$PUBLIC_SITE_URL" \
  --expect-https \
  --skip-mcp \
  --local-api-url http://127.0.0.1:41731 \
  --expect-admin
wait_for_public_mcp_gateway
run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_mcp_oauth.py \
  --mcp-url "$PUBLIC_MCP_URL"

# Watchdog installation remains a candidate-phase operation. A failure still
# has a protected rollback path and cannot turn a reopened healthy release into
# a misleading failed deploy.
if [[ "$INSTALL_WATCHDOG" == "1" ]]; then
  if [[ "$(id -u)" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
    run_release bash "$ROOT_DIR/scripts/install_production_watchdog.sh"
  else
    echo "WARN: watchdog install skipped; root and systemctl are required." >&2
  fi
fi

# Reconcile every release invariant immediately before opening production.
if [[ -n "$automation_feed_baseline" ]]; then
  run_release "$PYTHON_BIN" scripts/check_automation_center_release.py verify-feed \
    --database "$CRM_DATA_DIR/change_feed.sqlite3" \
    --baseline "$automation_feed_baseline"
fi
capture_safe_work_telegram_status \
  0 "$manager_revision" release \
  "$automation_telegram_status"
run_release "$PYTHON_BIN" scripts/check_automation_center_release.py \
  verify-telegram-effects \
  --state-dir "$WORK_TELEGRAM_STATE_DIR" \
  --runtime-dir "$WORK_TELEGRAM_RUNTIME_DIR" \
  --baseline "$automation_telegram_effects"
capture_manager_automation_status \
  "$MANAGER_CURRENT_LINK" release "$automation_manager_status"
validate_manager_automation_status \
  "$automation_manager_status" --expect-held release require-dependencies-ready

# The hold is released only after CRM, Manager, timers, feed and Telegram have
# passed exact readback. If the final marker removal fails, rollback reacquires
# a new owned hold before touching code or state.
run_automation_release_module \
  "$MANAGER_CURRENT_LINK" release release-hold "$automation_release_attempt_key"
automation_hold_acquired=0
capture_manager_automation_status \
  "$MANAGER_CURRENT_LINK" release "$automation_manager_status"
validate_manager_automation_status "$automation_manager_status" --expect-released release
run_release docker compose exec -T "$SERVICE_NAME" python \
  scripts/check_automation_center_release.py crm \
  --base-url http://127.0.0.1:41731 \
  --username "$AUTOSTOP_SMOKE_OPERATOR_USERNAME" \
  --password "$AUTOSTOP_SMOKE_OPERATOR_PASSWORD" \
  --manager-revision "$manager_revision" \
  --socket /run/autostop-manager-automation/control.sock \
  --manager-revision-path "$MANAGER_CONTAINER_DIR/REVISION" \
  --expect-released

assert_release_budget
run_release docker tag "$release_image" "$STABLE_IMAGE"
assert_release_budget
maintenance_elapsed="$(elapsed_seconds)"
run_release rm -f "$MAINTENANCE_MARKER_HOST"
deployment_succeeded=1
trap - EXIT

# Inbound duty is the only post-commit state restoration.  Keeping it paused
# until the rollback trap is gone makes it impossible for an owner message to
# trigger an outbound reply while this release is still protected/in flight.
# A failure here leaves the already healthy release open and inbound paused;
# it must never roll back a system that may now have accepted live commands.
set_work_telegram_duty "$work_telegram_inbound_before" release
work_telegram_duty_paused=0
capture_safe_work_telegram_status \
  "$work_telegram_inbound_before" "$manager_revision" release \
  "$automation_telegram_status"

auth_rotated=0
remove_auth_backup_if_safe || true
manager_crm_mcp_synced=0
remove_manager_crm_mcp_backup_if_safe || true
if [[ -d "$j1_worker_backup_dir" ]]; then
  rm -rf -- "$j1_worker_backup_dir" || {
    echo "WARN: committed J1 unit snapshot cleanup failed at $j1_worker_backup_dir." >&2
  }
fi
if [[ -d "$j1_browser_backup_dir" ]]; then
  rm -rf -- "$j1_browser_backup_dir" || {
    echo "WARN: committed J1 browser unit snapshot cleanup failed at $j1_browser_backup_dir." >&2
  }
fi

# Retention is deliberately post-success and best effort: cleanup can never
# roll back or interrupt a healthy release after public writes reopen. The
# helper removes only validated direct release artifacts and exact image tags.
if ! timeout --signal=TERM --kill-after=5 120s \
  "$PYTHON_BIN" scripts/agent_release_retention.py prune \
    --backup-root "$BACKUP_ROOT" \
    --manager-release-root "$MANAGER_RELEASE_ROOT" \
    --protected-backup "$backup_dir" \
    --protected-manager-release "$manager_release_dir" \
    --protected-manager-release "$previous_manager_dir" \
    --protected-image-tag "$release_image_tag" \
    --protected-image-tag "$rollback_image" \
    --protected-image-tag "$STABLE_IMAGE" \
    --keep-backups "$RELEASE_BACKUP_RETENTION_COUNT" \
    --keep-manager-releases "$MANAGER_RELEASE_RETENTION_COUNT" \
    --keep-release-images "$RELEASE_IMAGE_RETENTION_COUNT" \
    --keep-rollback-images "$ROLLBACK_IMAGE_RETENTION_COUNT"; then
  echo "WARN: post-success release retention failed; healthy release remains active." >&2
fi

echo "Deploy complete: $release_image_tag ($release_image) passed Gateway v2 smoke in ${maintenance_elapsed}s."
