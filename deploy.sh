#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# An armed installation uses the separate monotonic CRM successor consumer.
# This dispatch never changes the legacy initial-activation refusal below.
if [[ "${1:-}" == "--forward-j1" ]]; then
  shift
  exec env LD_LIBRARY_PATH=/opt/autostop-sqlite/3.51.3/lib \
    PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I -S -B \
    "$ROOT_DIR/scripts/j1_forward_release.py" "$@"
fi

# Dispatch before reading the live shell environment. Preparation creates only
# new source artifacts and a receipt, without executing the deployment below.
if [[ "${1:-}" == "--prepare-j1" ]]; then
  if [[ $# != 5 || "$2" != "--manager-source" || "$4" != "--output" ]]; then
    echo "usage: $0 --prepare-j1 --manager-source PATH --output NEW_DIRECTORY" >&2
    exit 2
  fi
  exec /usr/bin/python3 -I -S "$ROOT_DIR/scripts/j1_release_boundary.py" prepare \
    --crm-source "$ROOT_DIR" --manager-source "$3" --output "$5"
fi
if [[ $# != 4 || "$1" != "--j1-prepared" || "$3" != "--sha256" ]]; then
  echo "usage: $0 --prepare-j1 --manager-source PATH --output NEW_DIRECTORY" >&2
  echo "   or: $0 --j1-prepared /absolute/preparation.json --sha256 HASH" >&2
  exit 2
fi
J1_PREPARATION="$2"
J1_PREPARATION_SHA256="$4"
j1_prepared_fields="$(/usr/bin/python3 -I -S "$ROOT_DIR/scripts/j1_release_boundary.py" verify \
  --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256")"
mapfile -t j1_prepared <<<"$j1_prepared_fields"
[[ ${#j1_prepared[@]} == 4 ]] || exit 2
/usr/bin/python3 -I -S "$ROOT_DIR/scripts/j1_release_boundary.py" rollback-allowed || exit 2
j1_closed_fields="$(/usr/bin/python3 -I -S "$ROOT_DIR/scripts/j1_release_boundary.py" verify-closure \
  --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256")"
mapfile -t j1_closed <<<"$j1_closed_fields"
[[ ${#j1_closed[@]} == 13 ]] || exit 2
j1_previously_active="${j1_closed[0]}"
# The accepted window is already closed; failure must preserve it for explicit
# forward recovery, rather than restore a snapshot of stopped services.
j1_candidate_access_possible=1

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
MANAGER_ROLE_WORKSPACE="/var/lib/autostop-manager/roles/M2"
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
DOCS_AUDIT_PYTHON="${AUTOSTOP_DOCS_AUDIT_PYTHON:-$ROOT_DIR/.venv/bin/python}"
MANAGER_CRM_MCP_ENV="/opt/AutostopManager/.crm-mcp.env"
MANAGER_MCP_ACTIVATE_ON_DEPLOY="${AUTOSTOP_MANAGER_MCP_ACTIVATE_ON_DEPLOY:-1}"
if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" != "${j1_closed[1]}" ]]; then
  echo "ERROR: native MCP activation must preserve the accepted pre-closure state." >&2
  exit 2
fi
AUTOMATION_SERVICE_NAME="autostop-manager-scheduler.service"
AUTOMATION_STATE_DIR="/var/lib/autostop-manager-scheduler"
AUTOMATION_DB="$AUTOMATION_STATE_DIR/registry.sqlite3"
AUTOMATION_SOCKET="/run/autostop-manager-automation/control.sock"
WORK_TELEGRAM_RELEASE_LINK="/opt/autostop-work-telegram-releases/current"
WORK_TELEGRAM_STATE_DIR="/var/lib/autostop-work-telegram"
WORK_TELEGRAM_RUNTIME_DIR="/run/autostop-work-telegram"
WORK_TELEGRAM_OWNER_CONFIG="/etc/autostop-work-telegram/owner.json"
WORK_DUTY_CONTROLLER="/usr/local/libexec/autostop-crm-j1-duty/restore.py"
work_duty_intent_sha256=""
J1_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_ACTIVATE_ON_DEPLOY:-0}"
if [[ "$j1_previously_active" == "1" ]]; then
  if [[ "${AUTOSTOP_J1_ACTIVATE_ON_DEPLOY:-1}" != "1" ]]; then
    echo "ERROR: the closed release must preserve the previously active J1 worker." >&2
    exit 2
  fi
  J1_ACTIVATE_ON_DEPLOY=1
fi
if [[ "$J1_ACTIVATE_ON_DEPLOY" != "$j1_previously_active" ]]; then
  echo "ERROR: J1 activation must preserve the accepted pre-closure state." >&2
  exit 2
fi
J1_UNIT_NAME="autostop-j1.service"
J1_UNIT_PATH="/etc/systemd/system/$J1_UNIT_NAME"
J1_BROWSER_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_BROWSER_ACTIVATE_ON_DEPLOY:-0}"
if [[ "$J1_BROWSER_ACTIVATE_ON_DEPLOY" != "0" ]]; then
  echo "ERROR: the prepared guard denies the unsupported J1 browser profile." >&2
  exit 2
fi
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


validate_gateway_switches
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
    "$CRM_DATA_DIR/manager_structure.json" \
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
  if [[ -d "$MANAGER_ROLE_WORKSPACE" ]]; then
    size="$(du -sb "$MANAGER_ROLE_WORKSPACE" | awk '{print $1}')"
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
release_id="${j1_prepared[0]}"
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
manager_release_dir="${j1_prepared[1]}"
work_telegram_prepared_dir="${j1_prepared[2]}"
personal_telegram_prepared_dir="${j1_prepared[3]}"
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
work_telegram_inbound_before="${j1_closed[6]}"
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


adopt_prepared_manager_commit() {
  local source_dir="$1" target_dir="$2" expected_revision="$3"
  [[ "$target_dir" == "$manager_release_dir" ]] || return 2
  release_git_assert_exact_state \
    "AutoStopManager" "$source_dir" "$MANAGER_DEPLOY_BRANCH" "$expected_revision" >/dev/null
  "$PYTHON_BIN" -I -S scripts/j1_release_boundary.py verify \
    --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256" >/dev/null
  verify_manager_snapshot_artifact "$target_dir" "$expected_revision"
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
  # the read-only instruction audits. Keep the disposable DB target for compatibility.
  verify_manager_snapshot_artifact "$manager_release_dir" "$manager_revision"
  manager_knowledge_gate_dir="$(mktemp -d /tmp/autostopcrm-manager-knowledge.XXXXXX)"
  trap on_isolated_manager_knowledge_preflight_exit EXIT
  # The host venv supplies only dependencies. The exact candidate snapshot,
  # not the mutable source checkout, supplies the Manager code and knowledge map.
  env \
    PYTHONPATH="$manager_release_dir" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
    AUTOSTOP_MANAGER_DB="$manager_knowledge_gate_dir/preflight.sqlite3" \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-sync
  env \
    PYTHONPATH="$manager_release_dir" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
    AUTOSTOP_MANAGER_DB="$manager_knowledge_gate_dir/preflight.sqlite3" \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-audit
  # The schema probe needs both CRM and Manager dependencies from the CRM venv. Audit the
  # sealed Manager candidate alongside this exact CRM checkout before maintenance.
  env \
    PYTHONPATH="$manager_release_dir" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null \
    AUTOSTOP_MANAGER_DB="$manager_knowledge_gate_dir/preflight.sqlite3" \
    "$DOCS_AUDIT_PYTHON" "$ROOT_DIR/scripts/docs_audit.py" \
      --manager-root "$manager_release_dir" --format text
  env \
    PYTHONPATH="$manager_release_dir" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null \
    AUTOSTOP_MANAGER_DB="$manager_knowledge_gate_dir/preflight.sqlite3" \
    "$DOCS_AUDIT_PYTHON" "$ROOT_DIR/scripts/check_automotive_tool_catalog.py" \
      --expected-source-revision "$manager_revision" --manager-root "$manager_release_dir" \
      --manager-python "$MANAGER_RELEASE_PYTHON"
)

sync_current_manager_knowledge() {
  local active_manager_dir expected_manager_dir
  active_manager_dir="$(run_release readlink -f "$MANAGER_CURRENT_LINK")"
  expected_manager_dir="$(run_release readlink -f "$manager_release_dir")"
  if [[ "$active_manager_dir" != "$expected_manager_dir" ]]; then
    echo "ERROR: current Manager release does not match the candidate instruction source." >&2
    return 2
  fi
  # The venv supplies only dependencies. PYTHONPATH pins the code and local
  # instructions to the immutable current snapshot. The explicit DB target
  # remains for compatibility; these instruction audits do not open the database.
  run_release env \
    PYTHONPATH="$MANAGER_CURRENT_LINK" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_DB="$MANAGER_DB" \
  AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli knowledge-sync
  run_release env \
    PYTHONPATH="$MANAGER_CURRENT_LINK" \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_DB="$MANAGER_DB" \
  AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
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
if [[ ! -x "$DOCS_AUDIT_PYTHON" ]]; then
  echo "ERROR: CRM documentation audit venv is unavailable: $DOCS_AUDIT_PYTHON" >&2
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
adopt_prepared_manager_commit "$MANAGER_SOURCE_DIR" "$manager_release_dir" "$manager_revision"
verify_manager_snapshot_artifact "$work_telegram_prepared_dir" "$manager_revision"
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
  AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null AUTOSTOP_J1_DATA_DIR=/dev/null \
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
  local target_dir="$1" mode="$2" attempt ready=0
  [[ "$mode" == release && "$(readlink -f "$MANAGER_CURRENT_LINK")" == "$(readlink -f "$target_dir")" ]] || return 2
  run_release systemctl enable --now autostop-manager-mcp.service
  for attempt in {1..50}; do
    run_release systemctl is-active --quiet autostop-manager-mcp.service || return 2
    if (exec 3<>/dev/tcp/127.0.0.1/41931) 2>/dev/null; then ready=1; break; fi
    run_release sleep 0.2
  done
  (( ready == 1 )) || return 2
  run_release env PYTHONPATH="$manager_release_dir" PYTHONSAFEPATH=1 PYTHONDONTWRITEBYTECODE=1 \
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_MANAGER_DB="$MANAGER_DB" AUTOSTOP_J1_CACHE_DIR=/dev/null \
    "$MANAGER_RELEASE_PYTHON" -m autostop_manager.cli mcp-probe \
    --url http://127.0.0.1:41931/mcp --provider-failure-check --store-check
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
    AUTOSTOP_J1_CACHE_DIR=/dev/null
    AUTOSTOP_J1_DATA_DIR=/dev/null
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
    AUTOSTOP_J1_CACHE_DIR=/dev/null
    AUTOSTOP_J1_DATA_DIR=/dev/null
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
  local expected_inbound="$1" expected_revision="$2" budget_mode="$3" output="$4"
  local -a validator=("$PYTHON_BIN" scripts/check_automation_center_release.py telegram
    --release-link "$WORK_TELEGRAM_RELEASE_LINK" --owner-config "$WORK_TELEGRAM_OWNER_CONFIG"
    --expected-root "$work_telegram_prepared_dir")
  if [[ "$expected_inbound" == 1 ]]; then validator+=(--expect-inbound)
  elif [[ "$expected_inbound" == any ]]; then validator+=(--allow-either-inbound)
  else validator+=(--expect-outbound-only); fi
  [[ -z "$expected_revision" ]] || validator+=(--expected-revision "$expected_revision")
  local -a command=(sudo -u autostop-work-telegram env PYTHONDONTWRITEBYTECODE=1 PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1
    "PYTHONPATH=$work_telegram_prepared_dir"
    AUTOSTOP_MANAGER_ENV_FILE=/dev/null AUTOSTOP_J1_CACHE_DIR=/dev/null
    /opt/autostop-work-telegram-venv/bin/python -m autostop_manager.telegram_bridge --account work status)
  if [[ "$budget_mode" == release ]]; then
    run_release "${command[@]}" | run_release_from_stdin "${validator[@]}" >"$output"
  else
    run_maintenance "${command[@]}" | run_maintenance_from_stdin "${validator[@]}" >"$output"
  fi
}

safe_telegram_status_has_inbound() {
  local input="$1"
  "$PYTHON_BIN" -c \
    'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); sys.exit(0 if value.get("inbound_enabled") is True else 1)' \
    "$input"
}

set_work_telegram_duty() {
  local enabled="$1" budget_mode="$2" option=--disabled
  [[ "$enabled" != 1 ]] || option=--enabled
  [[ -f "$(dirname "$J1_PREPARATION")/admitted.json" ]] || return 2
  # Runtime RPC only: no env/wake config changes, unit restart or bootstrap.
  local -a command=(env PYTHONDONTWRITEBYTECODE=1 PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1
    "PYTHONPATH=$work_telegram_prepared_dir" AUTOSTOP_MANAGER_ENV_FILE=/dev/null
    AUTOSTOP_J1_CACHE_DIR=/dev/null /opt/autostop-work-telegram-venv/bin/python
    -m autostop_manager.telegram_bridge --account work duty-set "$option")
  if [[ "$budget_mode" == release ]]; then run_release "${command[@]}"
  else run_maintenance "${command[@]}"; fi
}

activate_manager_automation() {
  local attempt
  automation_scheduler_activation_attempted=1
  run_automation_release_module "$manager_release_dir" release hold "$automation_release_attempt_key"
  run_automation_release_module "$manager_release_dir" release adopt-current "$automation_release_attempt_key"
  run_release systemctl enable --now "$AUTOMATION_SERVICE_NAME"
  for attempt in {1..50}; do
    run_release systemctl is-active --quiet "$AUTOMATION_SERVICE_NAME" || return 2
    [[ -S "$AUTOMATION_SOCKET" ]] && break
    run_release sleep 0.2
  done
  [[ -S "$AUTOMATION_SOCKET" ]] || return 2
  run_automation_release_module "$manager_release_dir" release seed "$automation_release_attempt_key"
}

probe_manager_crm_feed_auth() {
  run_release "$PYTHON_BIN" scripts/probe_manager_crm_feed_auth.py \
    --manager-env "$MANAGER_CRM_MCP_ENV" \
    --unit "$AUTOMATION_SERVICE_NAME"
}

activate_work_telegram_release() {
  local attempt ready=0
  work_telegram_release_attempted=1
  # Reuse exact prepared sources and accepted dependency/model selectors.
  run_release systemctl enable --now autostop-work-telegram.service
  for attempt in {1..50}; do
    run_release systemctl is-active --quiet autostop-work-telegram.service || return 2
    if capture_safe_work_telegram_status 0 "$manager_revision" release "$automation_telegram_status"; then
      ready=1
      break
    fi
    run_release sleep 0.2
  done
  [[ "$ready" == 1 ]] || return 2
  run_release /usr/local/sbin/autostop-work-telegram-media self-check
  run_release "$work_telegram_prepared_dir/scripts/run-work-telegram-monitor-voice.sh" --self-check
  # Preserve daemon/instruction1b and wake.json; never run the bootstrap installer.
}

prepare_guarded_j1_units() {
  local unit next
  run_release "$PYTHON_BIN" -I -S scripts/j1_release_boundary.py verify-closure \
    --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256" >/dev/null
  work_duty_intent_sha256="$(run_release "$PYTHON_BIN" -I -S scripts/j1_release_boundary.py prepare-duty \
    --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256" \
    --marker "$MAINTENANCE_MARKER_HOST" --lock "$DEPLOY_LOCK_PATH")"
  run_release install -D -o root -g root -m 0644 "$ROOT_DIR/scripts/j1_work_duty.py" "$WORK_DUTY_CONTROLLER"
  run_release install -D -o root -g root -m 0444 "$(dirname "$J1_PREPARATION")/work-duty-intent.json" \
    /etc/autostop-crm-j1-duty/intent.json
  run_release install -d -o root -g root -m 0755 /var/lib/autostop-crm-j1-duty
  # No live alias may change until this second effective quiet/fence readback.
  snapshot_j1_worker_state
  # Fixed inert configuration, never a shell hook. It also prevents an
  # unsupported unit's ExecStopPost from running after its guard returns DENY.
  for unit in autostop-j1.service autostop-manager-mcp.service autostop-manager-scheduler.service \
    autostop-work-telegram.service autostop-telegram.service autostop-codex-wake.service \
    autostop-codex-start.service autostop-j1-browser.service; do
    run_release install -D -o root -g root -m 0644 "$ROOT_DIR/scripts/j1_release_hooks.conf" \
      "/etc/systemd/system/$unit.d/98-j1-release-hooks-held.conf"
  done
  activate_manager_snapshot "$manager_release_dir"
  next="$(dirname "$WORK_TELEGRAM_RELEASE_LINK")/.current-$release_id"
  run_release ln -s "$work_telegram_prepared_dir" "$next"
  run_release mv -Tf "$next" "$WORK_TELEGRAM_RELEASE_LINK"
  next="/opt/autostop-telegram-releases/.current-$release_id"
  run_release ln -s "$personal_telegram_prepared_dir" "$next"
  run_release mv -Tf "$next" /opt/autostop-telegram-releases/current
  run_release install -o root -g root -m 0644 \
    "$personal_telegram_prepared_dir/deploy/systemd/autostop-telegram.service" \
    /etc/systemd/system/autostop-telegram.service
  run_release "$manager_release_dir/scripts/install-manager-automation.sh" --replace-unit \
    --manager-revision "$manager_revision" --crm-revision "$crm_revision" --crm-version "$CRM_APP_VERSION"
  run_release "$manager_release_dir/scripts/install-manager-mcp.sh" --replace-unit
  run_release "$manager_release_dir/scripts/install-j1-worker.sh" --replace-unit
  for unit in autostop-work-telegram.service autostop-codex-wake.service; do
    run_release install -o root -g root -m 0644 \
      "$work_telegram_prepared_dir/deploy/systemd/$unit" "/etc/systemd/system/$unit"
  done
  run_release install -D -o root -g root -m 0644 "$(dirname "$J1_PREPARATION")/work-duty-restore.conf" \
    /etc/systemd/system/autostop-work-telegram.service.d/98-j1-work-duty-restore.conf
  if [[ -f "$(dirname "$J1_PREPARATION")/kernel-readiness.conf" ]]; then
    run_release install -D -o root -g root -m 0644 "$ROOT_DIR/scripts/j1_kernel_bootstrap.py" \
      /usr/local/libexec/autostop-crm-j1-duty/kernel.py
    run_release install -D -o root -g root -m 0644 "$(dirname "$J1_PREPARATION")/autostop-j1-native-recover.service" \
      /etc/systemd/system/autostop-j1-native-recover.service
    run_release install -D -o root -g root -m 0644 "$(dirname "$J1_PREPARATION")/z1-j1-native-recovery.conf" \
      /etc/systemd/system/autostop-codex-start.service.d/z1-j1-native-recovery.conf
    for unit in autostop-j1.service autostop-manager-mcp.service autostop-manager-scheduler.service \
      autostop-work-telegram.service autostop-telegram.service autostop-codex-wake.service; do
      local gate_source="kernel-readiness.conf"
      if [[ "$unit" == "autostop-codex-wake.service" ]]; then gate_source="kernel-wake-readiness.conf"; fi
      run_release install -D -o root -g root -m 0644 "$(dirname "$J1_PREPARATION")/$gate_source" \
        "/etc/systemd/system/$unit.d/z1-j1-kernel-readiness.conf"
    done
  fi
  run_release install -o root -g root -m 0755 \
    "$work_telegram_prepared_dir/scripts/run-work-telegram-media.sh" /usr/local/sbin/autostop-work-telegram-media
  run_release systemctl daemon-reload
}

admit_j1_candidate() {
  j1_candidate_access_possible=1
  prepare_guarded_j1_units
  sync_manager_crm_mcp_configuration
  assert_release_budget
  local remaining
  remaining="$(remaining_release_budget)"
  (( remaining > 0 )) || return 2
  run_release "$PYTHON_BIN" -I -S scripts/j1_release_boundary.py admit \
    --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256" \
    --image "$release_image" --backup "$backup_dir" --timeout "$remaining"
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
  [[ ! -d "$j1_worker_backup_dir" ]] || return 0
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
  [[ "$(readlink -f "$MANAGER_CURRENT_LINK")" == "$(readlink -f "$target_dir")" ]] || return 2
  run_release systemctl enable --now "$J1_UNIT_NAME"
  run_release sleep 1
  run_release systemctl is-active --quiet "$J1_UNIT_NAME"
  run_release "$PYTHON_BIN" -I -S scripts/j1_release_boundary.py native-probe \
    --crm-source "$ROOT_DIR" --preparation "$J1_PREPARATION" --sha256 "$J1_PREPARATION_SHA256"
  run_release systemctl is-active --quiet "$J1_UNIT_NAME"
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
  set +e
  rollback_active=1
  # The accepted window was already closed. Never restore stopped-service
  # snapshots, old writers, credentials or DBs over possibly new research.
  install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST" || {
    echo "RECOVERY CRITICAL: maintenance marker could not be re-armed." >&2
  }
  timeout --signal=TERM --kill-after=5 30s systemctl stop \
    autostop-j1.service autostop-manager-mcp.service autostop-manager-scheduler.service \
    autostop-work-telegram.service autostop-telegram.service autostop-codex-wake.service \
    autostop-j1-browser.service >&2 || true
  timeout --signal=TERM --kill-after=5 30s \
    docker compose stop --timeout 20 "$SERVICE_NAME" >&2 || true
  echo "J1 FORWARD RECOVERY REQUIRED: old writers and legacy rollback are forbidden; current research, data, sources, credentials, backups and maintenance marker are preserved." >&2
  set -e
  return "$original_status"
}

on_exit() {
  local status="$?"
  trap - EXIT
  if (( status != 0 && maintenance_started == 1 && deployment_succeeded == 0 )); then
    rollback_release "$status" || true
  elif (( status != 0 )); then
    echo "CLOSED RELEASE FAILED: preserve source preparation, credentials and closure evidence for forward recovery." >&2
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
# Pre-closure intent and runtime duty pause were accepted before writer closure.
# A stopped bridge cannot supply a readiness probe before candidate admission.

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
work_telegram_duty_paused=1
# The independently accepted dependency/model runtime remains unchanged.
# Running the full installer here would alter the inputs being bound by guard.

echo "Maintenance window started; stopping only $SERVICE_NAME."
run_release docker compose stop --timeout 20 "$SERVICE_NAME"
assert_release_budget

run_release "$PYTHON_BIN" scripts/agent_release_backup.py create \
  --output-root "$BACKUP_ROOT" \
  --crm-data-dir "$CRM_DATA_DIR" \
  --manager-db "$MANAGER_DB" \
  --manager-role-workspace "$MANAGER_ROLE_WORKSPACE" \
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

admit_j1_candidate
# Read-only instruction audits use the immutable current Manager candidate.
# Any failure keeps maintenance and current research for explicit forward recovery.
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

# Start the already prepared work bridge while runtime duty remains paused.
# Sources, dependency/model selectors and final environment stay guard-bound.
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

# Final credentials were bound before any candidate service start. Keep the
# protected feed-auth check below; do not mutate this immutable guard input.
assert_release_budget

# The scheduler imported the final guard-bound bearer on its first start.
# Prove that exact process credential can read the CRM change-feed readiness
# route without changing credentials or restarting an admitted unit.
probe_manager_crm_feed_auth
capture_manager_automation_status \
  "$MANAGER_CURRENT_LINK" release "$automation_manager_status"
validate_manager_automation_status \
  "$automation_manager_status" --expect-held release require-dependencies-ready
assert_release_budget

if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" == "1" ]]; then
  # Start only the accepted guarded unit and retain the bounded listener/MCP probe.
  manager_mcp_activation_attempted=1
  activate_manager_native_mcp "$manager_release_dir" release
  assert_release_budget
fi
if [[ "$J1_ACTIVATE_ON_DEPLOY" == "1" ]]; then
  snapshot_j1_worker_state
  # The original unit backup is retained; failures never restart old J1 code.
  j1_worker_activation_attempted=1
  activate_j1_worker "$manager_release_dir"
  assert_release_budget
fi
if [[ "$J1_BROWSER_ACTIVATE_ON_DEPLOY" == "1" ]]; then
  # Browser activation is an optional capability after the static release has
  # passed its protected checks. Its own failure must not discard CRM/Manager.
  activate_j1_browser_optional "$manager_release_dir"
fi

# Every previously active native role must return through its exact guard.
# Wake may reuse only an independently accepted warm daemon; cold bootstrap
# remains unsupported and is rejected by the initial closure contract.
for native_index in 4 5; do
  if [[ "${j1_closed[$native_index]}" == "1" ]]; then
    if [[ "$native_index" == "4" ]]; then native_unit=autostop-telegram.service; else native_unit=autostop-codex-wake.service; fi
    run_release systemctl start "$native_unit"
    run_release systemctl is-active --quiet "$native_unit"
  fi
done
native_units=(autostop-j1.service autostop-manager-mcp.service autostop-manager-scheduler.service
  autostop-work-telegram.service autostop-telegram.service autostop-codex-wake.service)
for native_index in 0 1 2 3 4 5; do
  native_unit="${native_units[$native_index]}"
  if [[ "${j1_closed[$((native_index + 7))]}" == "1" ]]; then
    run_release systemctl enable "$native_unit"
    run_release systemctl is-enabled --quiet "$native_unit"
  else
    run_release systemctl disable "$native_unit"
    if run_release systemctl is-enabled --quiet "$native_unit"; then exit 2; fi
  fi
  run_release systemctl is-active --quiet "$native_unit"
done
run_release systemctl is-active --quiet autostop-manager-scheduler.service autostop-work-telegram.service
if [[ "$J1_ACTIVATE_ON_DEPLOY" == "1" ]]; then run_release systemctl is-active --quiet "$J1_UNIT_NAME"; fi
if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" == "1" ]]; then run_release systemctl is-active --quiet autostop-manager-mcp.service; fi

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

# Watchdog installation remains inside the protected candidate phase.
# A failure preserves maintenance and requires explicit forward recovery.
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
# passed exact readback. A later failure closes native writers and CRM;
# it never overlays current data or reopens old code.
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

# Publish durable success only after maintenance removal and trap disposal.
# First start has no receipt; subsequent starts restore the same desired duty
# through the fixed hash-bound controller, without mutating startup inputs.
if ! run_release /usr/bin/python3 -I -S "$WORK_DUTY_CONTROLLER" commit \
  --intent-sha256 "$work_duty_intent_sha256"; then
  echo "ERROR: release committed; durable Work duty receipt failed. Forward recovery required." >&2
  exit 3
fi
# Native restoration takes a shared lock, serializing it with any later release.
flock -u "$DEPLOY_LOCK_FD"
if ! run_release /usr/bin/python3 -I -S "$WORK_DUTY_CONTROLLER" restore \
  --intent-sha256 "$work_duty_intent_sha256"; then
  echo "ERROR: release committed; Work duty restoration is unverified. Forward recovery required." >&2
  exit 3
fi
if ! capture_safe_work_telegram_status \
  "$work_telegram_inbound_before" "$manager_revision" release \
  "$automation_telegram_status"; then
  echo "ERROR: release committed; Work duty readback failed. Forward recovery required." >&2
  exit 3
fi
work_telegram_duty_paused=0

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
