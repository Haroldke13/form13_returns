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
PYTHON_BIN="${PYTHON_BIN:-python3}"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE:-$APP_DIR/logs/github_sqlite_backup.log}"
BACKUP_PID_FILE="${BACKUP_PID_FILE:-$APP_DIR/logs/github_sqlite_backup.pid}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/github_sqlite_backup.sh --once
  ./scripts/github_sqlite_backup.sh --loop
  ./scripts/github_sqlite_backup.sh --start-background
  ./scripts/github_sqlite_backup.sh --install-cron

Defaults:
  --loop runs one backup immediately, then repeats every 4 hours.
  --start-background installs a 4-hour cron entry and starts one backup now.

Environment overrides:
  CONTAINER_NAME=form14_web
  GIT_REMOTE=origin
  GIT_BRANCH=main
  BACKUP_PREFIX=backup
  BACKUP_INTERVAL_SECONDS=14400
  BACKUP_CRON_SCHEDULE="0 */4 * * *"
  BACKUP_LOG_FILE=logs/github_sqlite_backup.log
  BACKUP_PID_FILE=logs/github_sqlite_backup.pid
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
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

ensure_no_staged_changes() {
  if ! git diff --cached --quiet --ignore-submodules --; then
    die "Git index already has staged changes. Commit or unstage them before running this backup job."
  fi
}

ensure_container_running() {
  local running
  running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  [[ "$running" == "true" ]] || die "Docker container '$CONTAINER_NAME' is not running."
}

cleanup_container_files() {
  docker exec "$CONTAINER_NAME" rm -f "$@" >/dev/null 2>&1 || true
}

export_live_database_to_sqlite() {
  local backup_file="$1"
  local container_target="/tmp/$backup_file"
  local container_exporter="/tmp/export_live_db_to_sqlite.py"

  docker cp "$EXPORTER_PATH" "$CONTAINER_NAME:$container_exporter"

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

commit_and_push_backup() {
  local backup_file="$1"
  local commit_timestamp="$2"

  ensure_no_staged_changes
  git add -- "$backup_file"

  local staged_paths
  staged_paths="$(git diff --cached --name-only --diff-filter=ACMRTUXB)"
  if [[ "$staged_paths" != "$backup_file" ]]; then
    git restore --staged -- "$backup_file" >/dev/null 2>&1 || true
    die "Unexpected staged files detected. Refusing to commit anything except '$backup_file'."
  fi

  git commit -m "Add SQLite backup $commit_timestamp" -- "$backup_file"
  git push "$GIT_REMOTE" "HEAD:$GIT_BRANCH"
}

run_once() {
  require_command docker
  require_command git
  require_command "$PYTHON_BIN"

  cd "$APP_DIR"
  ensure_container_running
  ensure_no_staged_changes
  [[ -f "$EXPORTER_PATH" ]] || die "Exporter script not found: $EXPORTER_PATH"

  local timestamp
  timestamp="$(backup_timestamp)"
  local backup_file="${BACKUP_PREFIX}_${timestamp}.sqlite"
  local backup_path="$APP_DIR/$backup_file"

  [[ ! -e "$backup_path" ]] || die "Backup file already exists: $backup_path"

  log "Exporting live database from '$CONTAINER_NAME' to $backup_file"
  export_live_database_to_sqlite "$backup_file"

  log "Verifying $backup_file"
  verify_sqlite_backup "$backup_path" | while IFS= read -r line; do
    log "$line"
  done

  log "Committing and pushing only $backup_file"
  commit_and_push_backup "$backup_file" "$timestamp"
  log "Backup pushed to $GIT_REMOTE/$GIT_BRANCH: $backup_file"
}

run_loop() {
  while true; do
    if ( run_once ); then
      log "Next backup starts in $BACKUP_INTERVAL_SECONDS seconds."
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
  (
    cd "$APP_DIR"
    exec nohup "$SCRIPT_PATH" --once >> "$BACKUP_LOG_FILE" 2>&1
  ) </dev/null &
  local pid="$!"
  printf '%s\n' "$pid" > "$BACKUP_PID_FILE"
  log "Started immediate GitHub SQLite backup with PID $pid."
  log "Backup log: $BACKUP_LOG_FILE"
}

install_cron_entry() {
  require_command crontab
  mkdir -p "$APP_DIR/logs"

  local cron_line
  cron_line="$BACKUP_CRON_SCHEDULE cd $APP_DIR && $SCRIPT_PATH --once >> $APP_DIR/logs/github_sqlite_backup.log 2>&1"

  (
    crontab -l 2>/dev/null | grep -Fv "$SCRIPT_PATH --once" || true
    printf '%s\n' "$cron_line"
  ) | crontab -
}

install_cron() {
  install_cron_entry

  log "Installed cron entry:"
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
