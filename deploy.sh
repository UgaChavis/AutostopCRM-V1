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
MAINTENANCE_MARKER_HOST="${AUTOSTOP_MAINTENANCE_MARKER_HOST:-$CRM_DATA_DIR/.agent-gateway-maintenance}"
PUBLIC_SITE_URL="${AUTOSTOP_PUBLIC_SITE_URL:-https://crm.autostopcrm.ru}"
PUBLIC_MCP_URL="${AUTOSTOP_PUBLIC_MCP_URL:-https://crm.autostopcrm.ru/mcp}"
CODEX_CONFIG_PATH="${AUTOSTOP_CODEX_CONFIG_PATH:-/root/.codex/config.toml}"
CODEX_RUNTIME_ENV_PATH="${AUTOSTOP_CODEX_RUNTIME_ENV_PATH:-/root/.config/autostopcrm/codex-mcp.env}"
DESKTOP_INSTRUCTION_PATH="${AUTOSTOP_DESKTOP_INSTRUCTION_PATH:-/root/Desktop/AUTOSTOPCRM_FULL_INSTRUCTION.txt}"
INSTALL_WATCHDOG="${AUTOSTOP_INSTALL_WATCHDOG:-1}"
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
    "$MANAGER_DB" \
    "$MANAGER_DB-wal" \
    "$MANAGER_DB-shm"; do
    if [[ -f "$candidate" ]]; then
      size="$(stat -c '%s' "$candidate")"
      [[ "$size" =~ ^[0-9]+$ ]] || return 2
      total=$(( total + size ))
    fi
  done
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
manager_release_dir="$MANAGER_RELEASE_ROOT/${release_id}-manager-${manager_revision:0:12}"
manager_release_staging_dir="${manager_release_dir}.partial-$$"
manager_attempt_cleanup_authorized=0
premaintenance_cleanup_done=0

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
  # Docker can overlay the live manager SQLite data only when the nested
  # mountpoint already exists inside the immutable read-only source snapshot.
  mkdir -p "$staging_dir/data"
  # Production may keep the Manager checkout private (0600/0700).  The
  # sanitized snapshot is mounted read-only into the unprivileged CRM
  # container, so normalize only read/traverse access without inventing
  # executable bits on regular files.
  chmod -R a+rX "$staging_dir"
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

if [[ ! -d "$MANAGER_SOURCE_DIR/autostop_manager" ]]; then
  echo "ERROR: AutoStopManager source is unavailable: $MANAGER_SOURCE_DIR" >&2
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
      --codex-config "$CODEX_CONFIG_PATH" \
      --runtime-env "$CODEX_RUNTIME_ENV_PATH" \
      restore --backup-dir "$auth_backup_dir" || status=$?
  else
    "$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
      --server-env "$ROOT_DIR/.env" \
      --codex-config "$CODEX_CONFIG_PATH" \
      --runtime-env "$CODEX_RUNTIME_ENV_PATH" \
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
  if (( marker_rearmed == 0 )); then
    echo "ROLLBACK CRITICAL: CRM remains stopped because write protection is unavailable." >&2
    if restore_auth_configuration; then
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
  activate_manager_snapshot "$previous_manager_dir" || rollback_ok=0
  restore_auth_configuration || rollback_ok=0
  run_maintenance env AUTOSTOP_RELEASE_IMAGE="$rollback_image" docker compose up \
    -d --no-deps --no-build --force-recreate "$SERVICE_NAME" >&2 || rollback_ok=0
  if wait_for_health "$rollback_image" 0; then
    # Never reopen writes after an incomplete protected-data, Manager, auth,
    # image, or health rollback, even when the old container itself is healthy.
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

# The server and Codex bearer are changed only after all builds and rollback
# images are ready. The private snapshot is restored on every failed exit.
"$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
  --server-env "$ROOT_DIR/.env" \
  --codex-config "$CODEX_CONFIG_PATH" \
  --runtime-env "$CODEX_RUNTIME_ENV_PATH" \
  snapshot --backup-dir "$auth_backup_dir"
auth_rotated=1
if ! "$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
  --server-env "$ROOT_DIR/.env" \
  --codex-config "$CODEX_CONFIG_PATH" \
  --runtime-env "$CODEX_RUNTIME_ENV_PATH" \
  rotate --generate --mcp-url "$PUBLIC_MCP_URL"; then
  exit 2
fi
reload_deploy_environment
"$PYTHON_BIN" scripts/configure_codex_mcp_auth.py \
  --server-env "$ROOT_DIR/.env" \
  --codex-config "$CODEX_CONFIG_PATH" \
  --runtime-env "$CODEX_RUNTIME_ENV_PATH" \
  check --mcp-url "$PUBLIC_MCP_URL"
"$PYTHON_BIN" scripts/validate_production_env.py --require-production --require-store
docker compose config --quiet

maintenance_started=1
printf -v maintenance_started_at '%(%s)T' -1
run_release install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST"

echo "Maintenance window started; stopping only $SERVICE_NAME."
run_release docker compose stop --timeout 20 "$SERVICE_NAME"
assert_release_budget

activate_manager_snapshot "$manager_release_dir"

run_release "$PYTHON_BIN" scripts/agent_release_backup.py create \
  --output-root "$BACKUP_ROOT" \
  --crm-data-dir "$CRM_DATA_DIR" \
  --manager-db "$MANAGER_DB" \
  --backup-id "$release_id"
backup_dir="$BACKUP_ROOT/$release_id"
run_release "$PYTHON_BIN" scripts/agent_release_backup.py verify --backup-dir "$backup_dir"
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

run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_live_connector.py \
  --strict \
  --skip-public-site \
  --skip-public-write-protection \
  --skip-mcp \
  --local-api-url http://127.0.0.1:41731 \
  --expect-admin

wait_for_internal_store_gateway

# Run the long owner/feed/web smoke while public CRM writes remain blocked.
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

assert_release_budget
run_release docker tag "$release_image" "$STABLE_IMAGE"
assert_release_budget
maintenance_elapsed="$(elapsed_seconds)"
run_release rm -f "$MAINTENANCE_MARKER_HOST"
deployment_succeeded=1
trap - EXIT
auth_rotated=0
remove_auth_backup_if_safe || true

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

if [[ -n "$DESKTOP_INSTRUCTION_PATH" ]]; then
  install -D -m 644 "$ROOT_DIR/AUTOSTOPCRM_FULL_INSTRUCTION.txt" "$DESKTOP_INSTRUCTION_PATH" 2>/dev/null || true
fi

echo "Deploy complete: $release_image_tag ($release_image) passed Gateway v2 smoke in ${maintenance_elapsed}s."
