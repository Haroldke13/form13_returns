#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$APP_DIR/scripts/github_sqlite_backup.sh"
EXPORTER_PATH="$APP_DIR/scripts/export_live_db_to_sqlite.py"

CONTAINER_NAME="${CONTAINER_NAME:-form14_web}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-main}"
BACKUP_PREFIX="${BACKUP_PREFIX:-backup}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-14400}"
BACKUP_CRON_SCHEDULE="${BACKUP_CRON_SCHEDULE:-0 */4 * * *}"
BACKUP_WATCH_CRON_SCHEDULE="${BACKUP_WATCH_CRON_SCHEDULE:-* * * * *}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE:-$APP_DIR/logs/github_sqlite_backup.log}"
BACKUP_PID_FILE="${BACKUP_PID_FILE:-$APP_DIR/logs/github_sqlite_backup.pid}"
BACKUP_STATE_FILE="${BACKUP_STATE_FILE:-$APP_DIR/logs/github_sqlite_backup_state.json}"
BACKUP_LOCK_DIR="${BACKUP_LOCK_DIR:-$APP_DIR/logs/github_sqlite_backup.lock}"
INCLUDE_CHANGED_FILES="${GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES:-1}"
BACKUP_CURRENT_STATE_PATH=""

usage() {
  cat <<'EOF'
Usage:
  ./scripts/github_sqlite_backup.sh --once
  ./scripts/github_sqlite_backup.sh --loop
  ./scripts/github_sqlite_backup.sh --start-background
  ./scripts/github_sqlite_backup.sh --install-cron

Defaults:
  --once fingerprints the live database and skips backup when rows have not changed.
  --loop runs --once repeatedly using BACKUP_INTERVAL_SECONDS.
  --start-background installs cron entries and starts one detached --once run.

Environment overrides:
  CONTAINER_NAME=form14_web
  GIT_REMOTE=origin
  GIT_BRANCH=main
  BACKUP_PREFIX=backup
  BACKUP_INTERVAL_SECONDS=14400
  BACKUP_CRON_SCHEDULE="0 */4 * * *"
  BACKUP_WATCH_CRON_SCHEDULE="* * * * *"
  GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES=1
  BACKUP_LOG_FILE=logs/github_sqlite_backup.log
  BACKUP_STATE_FILE=logs/github_sqlite_backup_state.json
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

truthy() {
  local value
  value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

pid_is_running() {
  local pid
  [[ -f "$BACKUP_PID_FILE" ]] || return 1
  read -r pid < "$BACKUP_PID_FILE" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  ps -p "$pid" -o args= 2>/dev/null | grep -F "$SCRIPT_PATH" | grep -E -- "--(loop|once)" >/dev/null
}

backup_timestamp() {
  date '+%d_%m_%Y_%H-%M-%S'
}

ensure_container_running() {
  local running
  running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "Docker container '$CONTAINER_NAME' is not running."
}

release_lock() {
  rm -rf "$BACKUP_LOCK_DIR"
}

acquire_lock() {
  mkdir -p "$(dirname "$BACKUP_LOCK_DIR")"
  if mkdir "$BACKUP_LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$BACKUP_LOCK_DIR/pid"
    trap release_lock EXIT
    return 0
  fi

  local existing_pid=""
  if [[ -f "$BACKUP_LOCK_DIR/pid" ]]; then
    read -r existing_pid < "$BACKUP_LOCK_DIR/pid" || true
  fi
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" >/dev/null 2>&1; then
    log "Another GitHub SQLite backup is already running with PID $existing_pid; skipping."
    return 1
  fi

  rm -rf "$BACKUP_LOCK_DIR"
  mkdir "$BACKUP_LOCK_DIR"
  printf '%s\n' "$$" > "$BACKUP_LOCK_DIR/pid"
  trap release_lock EXIT
}

cleanup_container_files() {
  docker exec "$CONTAINER_NAME" rm -f "$@" >/dev/null 2>&1 || true
}

copy_exporter_to_container() {
  local container_exporter="$1"
  docker cp "$EXPORTER_PATH" "$CONTAINER_NAME:$container_exporter"
}

fingerprint_live_database() {
  local container_exporter="/tmp/export_live_db_to_sqlite.py"
  copy_exporter_to_container "$container_exporter"
  if ! docker exec "$CONTAINER_NAME" python "$container_exporter" --fingerprint; then
    cleanup_container_files "$container_exporter"
    return 1
  fi
  cleanup_container_files "$container_exporter"
}

json_field() {
  local json_path="$1"
  local field_name="$2"
  "$PYTHON_BIN" - "$json_path" "$field_name" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
field = sys.argv[2]
payload = json.loads(path.read_text(encoding="utf-8"))
print(payload.get(field, ""))
PY
}

last_pushed_fingerprint() {
  [[ -f "$BACKUP_STATE_FILE" ]] || return 0
  "$PYTHON_BIN" - "$BACKUP_STATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
else:
    print(payload.get("database_state", {}).get("fingerprint", ""))
PY
}

write_backup_state() {
  local current_state_path="$1"
  local backup_file="$2"
  local commit_hash="$3"
  mkdir -p "$(dirname "$BACKUP_STATE_FILE")"
  "$PYTHON_BIN" - "$current_state_path" "$BACKUP_STATE_FILE" "$backup_file" "$commit_hash" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

state_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
backup_file = sys.argv[3]
commit_hash = sys.argv[4]

payload = {
    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "backup_file": backup_file,
    "commit_hash": commit_hash,
    "database_state": json.loads(state_path.read_text(encoding="utf-8")),
}
output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

export_live_database_to_sqlite() {
  local backup_file="$1"
  local container_target="/tmp/$backup_file"
  local container_exporter="/tmp/export_live_db_to_sqlite.py"

  copy_exporter_to_container "$container_exporter"

  if ! docker exec -e TARGET_SQLITE="$container_target" "$CONTAINER_NAME" python "$container_exporter"; then
    cleanup_container_files "$container_target" "$container_exporter"
    return 1
  fi

  if ! docker cp "$CONTAINER_NAME:$container_target" "$APP_DIR/$backup_file"; then
    cleanup_container_files "$container_target" "$container_exporter"
    return 1
  fi

  cleanup_container_files "$container_target" "$container_exporter"
}

verify_sqlite_backup() {
  local backup_path="$1"
  "$PYTHON_BIN" - "$backup_path" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(f"Backup file not found: {path}")

connection = sqlite3.connect(path)
try:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(f"SQLite integrity check failed: {integrity}")
    table_count = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
finally:
    connection.close()

print(f"bytes={path.stat().st_size}")
print(f"integrity_check={integrity}")
print(f"table_count={table_count}")
PY
}

extra_git_path_excluded() {
  local path="$1"
  local backup_file="$2"

  [[ "$path" == "$backup_file" ]] && return 0

  case "$path" in
    .env|.env.*|logs/*|backups/*|instance/*|certs/*|.cloudflared/*|cloudflared/*)
      return 0
      ;;
    application_default_credentials.json|client_secret_*.json|*.pem|*.key|*.crt)
      return 0
      ;;
    "$BACKUP_PREFIX"_*.sqlite)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

stage_extra_changed_files() {
  local backup_file="$1"
  truthy "$INCLUDE_CHANGED_FILES" || return 0

  local path
  while IFS= read -r -d '' path; do
    if ! extra_git_path_excluded "$path" "$backup_file"; then
      git add -- "$path"
    fi
  done < <(git diff --name-only -z --)

  while IFS= read -r -d '' path; do
    if ! extra_git_path_excluded "$path" "$backup_file"; then
      git add -- "$path"
    fi
  done < <(git ls-files --others --exclude-standard -z)
}

commit_and_push_backup() {
  local backup_file="$1"
  local commit_timestamp="$2"

  if ! git diff --cached --quiet --ignore-submodules --; then
    die "Git index already has staged changes. Commit or unstage them before running this backup job."
  fi

  git add -- "$backup_file"
  stage_extra_changed_files "$backup_file"

  local staged_paths
  staged_paths="$(git diff --cached --name-only --diff-filter=ACMRTUXB)"
  if [[ -z "$staged_paths" ]]; then
    die "No files were staged for the backup commit."
  fi

  log "Staged files for backup commit:"
  printf '%s\n' "$staged_paths" | while IFS= read -r path; do
    log "  $path"
  done

  git commit -m "Add SQLite backup $commit_timestamp"
  git push "$GIT_REMOTE" "HEAD:$GIT_BRANCH"
}

run_once() {
  require_command docker
  require_command git
  require_command "$PYTHON_BIN"

  cd "$APP_DIR"
  ensure_container_running
  [[ -f "$EXPORTER_PATH" ]] || die "Exporter script not found: $EXPORTER_PATH"
  if ! acquire_lock; then
    return 0
  fi

  mkdir -p "$APP_DIR/logs"
  BACKUP_CURRENT_STATE_PATH="$(mktemp "$APP_DIR/logs/github_sqlite_db_state.XXXXXX.json")"
  local current_state_path="$BACKUP_CURRENT_STATE_PATH"
  trap 'rm -f "${BACKUP_CURRENT_STATE_PATH:-}"; release_lock' EXIT

  log "Checking live database row-change fingerprint."
  fingerprint_live_database > "$current_state_path"

  local current_fingerprint last_fingerprint table_count
  current_fingerprint="$(json_field "$current_state_path" fingerprint)"
  table_count="$(json_field "$current_state_path" table_count)"
  last_fingerprint="$(last_pushed_fingerprint)"

  if [[ -n "$last_fingerprint" && "$current_fingerprint" == "$last_fingerprint" ]]; then
    log "No database row changes detected across $table_count tables; backup skipped."
    return 0
  fi

  local timestamp backup_file backup_path
  timestamp="$(backup_timestamp)"
  backup_file="${BACKUP_PREFIX}_${timestamp}.sqlite"
  backup_path="$APP_DIR/$backup_file"

  [[ ! -e "$backup_path" ]] || die "Backup file already exists: $backup_path"

  log "Database changes detected; exporting live database from '$CONTAINER_NAME' to $backup_file"
  export_live_database_to_sqlite "$backup_file"

  log "Verifying $backup_file"
  verify_sqlite_backup "$backup_path" | while IFS= read -r line; do
    log "$line"
  done

  log "Committing and pushing backup changes"
  commit_and_push_backup "$backup_file" "$timestamp"
  local commit_hash
  commit_hash="$(git rev-parse HEAD)"
  write_backup_state "$current_state_path" "$backup_file" "$commit_hash"
  log "Backup pushed to $GIT_REMOTE/$GIT_BRANCH: $backup_file"
  log "Recorded pushed database fingerprint: $current_fingerprint"
}

run_loop() {
  while true; do
    if ( run_once ); then
      log "Next backup check starts in $BACKUP_INTERVAL_SECONDS seconds."
    else
      log "Backup attempt failed. Retrying in $BACKUP_INTERVAL_SECONDS seconds." >&2
    fi
    sleep "$BACKUP_INTERVAL_SECONDS"
  done
}

start_background() {
  require_command nohup
  install_cron_entry
  mkdir -p "$(dirname "$BACKUP_LOG_FILE")" "$(dirname "$BACKUP_PID_FILE")"

  if pid_is_running; then
    log "GitHub SQLite backup is already running with PID $(cat "$BACKUP_PID_FILE")."
    return 0
  fi

  rm -f "$BACKUP_PID_FILE"
  local original_dir
  original_dir="$PWD"
  cd "$APP_DIR"
  nohup "$SCRIPT_PATH" --once >> "$BACKUP_LOG_FILE" 2>&1 < /dev/null &
  local pid="$!"
  cd "$original_dir"
  printf '%s\n' "$pid" > "$BACKUP_PID_FILE"
  log "Started immediate GitHub SQLite backup check with PID $pid."
  log "Backup log: $BACKUP_LOG_FILE"
}

install_cron_entry() {
  require_command crontab
  mkdir -p "$APP_DIR/logs"

  local watch_cron_line four_hour_cron_line
  watch_cron_line="$BACKUP_WATCH_CRON_SCHEDULE cd $APP_DIR && $SCRIPT_PATH --once >> $APP_DIR/logs/github_sqlite_backup.log 2>&1"
  four_hour_cron_line="$BACKUP_CRON_SCHEDULE cd $APP_DIR && $SCRIPT_PATH --once >> $APP_DIR/logs/github_sqlite_backup.log 2>&1"

  (
    crontab -l 2>/dev/null | grep -Fv "$SCRIPT_PATH --once" || true
    printf '%s\n' "$watch_cron_line"
    if [[ "$four_hour_cron_line" != "$watch_cron_line" ]]; then
      printf '%s\n' "$four_hour_cron_line"
    fi
  ) | crontab -
}

install_cron() {
  install_cron_entry

  log "Installed cron entries:"
  crontab -l | grep -F "$SCRIPT_PATH --once" || true
}

case "${1:---loop}" in
  --once)
    run_once
    ;;
  --loop)
    run_loop
    ;;
  --start-background)
    start_background
    ;;
  --install-cron)
    install_cron
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    die "Unknown argument: $1"
    ;;
esac
