#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$APP_DIR/scripts/github_sqlite_backup.sh"

CONTAINER_NAME="${CONTAINER_NAME:-form14_web}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-main}"
BACKUP_PREFIX="${BACKUP_PREFIX:-backup}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-14400}"
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
  --start-background starts the same loop with nohup and a PID file.

Environment overrides:
  CONTAINER_NAME=form14_web
  GIT_REMOTE=origin
  GIT_BRANCH=main
  BACKUP_PREFIX=backup
  BACKUP_INTERVAL_SECONDS=14400
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
  ps -p "$pid" -o args= 2>/dev/null | grep -F "$SCRIPT_PATH" | grep -F -- "--loop" >/dev/null
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

export_live_database_to_sqlite() {
  local backup_file="$1"
  local container_target="/tmp/$backup_file"

  docker exec -i -e TARGET_SQLITE="$container_target" "$CONTAINER_NAME" python - <<'PY'
import json
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text


def normalized_database_url() -> str:
    url = (
        os.environ.get("INTERNAL_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("PBORA_DATABASE_URL")
        or ""
    ).strip()
    if not url:
        raise SystemExit("No database URL is configured in the container environment.")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    if url.startswith("postgresql://") or url.startswith("postgresql+"):
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        existing_keys = {key.lower() for key in query_params}
        if "sslmode" not in existing_keys:
            query_params["sslmode"] = [
                os.environ.get("DB_SSL_MODE")
                or os.environ.get("POSTGRES_SSL_MODE")
                or "disable"
            ]
            url = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(query_params, doseq=True),
                    parsed.fragment,
                )
            )

    return url


def serialize_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, sort_keys=True)
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            value = value.tz_localize(None)
        return value.isoformat(sep=" ")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def normalize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized = dataframe.copy()
    for column_name in normalized.columns:
        series = normalized[column_name]
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            normalized[column_name] = series.dt.tz_localize(None).astype(str)
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            normalized[column_name] = series.astype(str)
            continue
        if pd.api.types.is_object_dtype(series):
            normalized[column_name] = series.map(serialize_value)
    return normalized


target = Path(os.environ["TARGET_SQLITE"])
target.unlink(missing_ok=True)

database_url = normalized_database_url()
is_postgresql = database_url.startswith("postgresql://") or database_url.startswith("postgresql+")
engine = create_engine(database_url, pool_pre_ping=True)
sqlite_connection = sqlite3.connect(target)

try:
    sqlite_connection.execute("PRAGMA journal_mode=OFF")
    sqlite_connection.execute("PRAGMA synchronous=OFF")
    with engine.connect() as source_connection:
        transaction = source_connection.begin()
        try:
            if is_postgresql:
                source_connection.execute(text("SET TRANSACTION READ ONLY"))

            inspector = inspect(source_connection)
            table_names = sorted(inspector.get_table_names())
            print(f"exporting_tables={len(table_names)}", flush=True)

            for table_name in table_names:
                table = Table(table_name, MetaData(), autoload_with=source_connection)
                row_count = 0
                wrote_table = False
                for chunk in pd.read_sql(select(table), source_connection, chunksize=5000):
                    chunk = normalize_dataframe(chunk)
                    chunk.to_sql(
                        table_name,
                        sqlite_connection,
                        if_exists="replace" if not wrote_table else "append",
                        index=False,
                    )
                    row_count += len(chunk)
                    wrote_table = True

                if not wrote_table:
                    columns = [column["name"] for column in inspector.get_columns(table_name)]
                    pd.DataFrame(columns=columns).to_sql(
                        table_name,
                        sqlite_connection,
                        if_exists="replace",
                        index=False,
                    )

                print(f"{table_name}\t{row_count}", flush=True)
        except Exception:
            transaction.rollback()
            raise
        else:
            transaction.commit()

    sqlite_connection.commit()
    integrity = sqlite_connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(f"SQLite integrity check failed: {integrity}")
    print(f"integrity_check={integrity}", flush=True)
finally:
    sqlite_connection.close()
    engine.dispose()

print(f"target={target}", flush=True)
print(f"bytes={target.stat().st_size}", flush=True)
PY

  docker cp "$CONTAINER_NAME:$container_target" "$APP_DIR/$backup_file"
  docker exec "$CONTAINER_NAME" rm -f "$container_target" >/dev/null 2>&1 || true
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
    if "$SCRIPT_PATH" --once; then
      log "Next backup starts in $BACKUP_INTERVAL_SECONDS seconds."
    else
      log "Backup attempt failed. Retrying in $BACKUP_INTERVAL_SECONDS seconds." >&2
    fi
    sleep "$BACKUP_INTERVAL_SECONDS"
  done
}

start_background() {
  require_command nohup
  mkdir -p "$(dirname "$BACKUP_LOG_FILE")" "$(dirname "$BACKUP_PID_FILE")"

  if pid_is_running; then
    log "GitHub SQLite backup loop is already running with PID $(cat "$BACKUP_PID_FILE")."
    return 0
  fi

  rm -f "$BACKUP_PID_FILE"
  nohup "$SCRIPT_PATH" --loop >> "$BACKUP_LOG_FILE" 2>&1 &
  local pid="$!"
  printf '%s\n' "$pid" > "$BACKUP_PID_FILE"
  log "Started GitHub SQLite backup loop with PID $pid."
  log "Backup log: $BACKUP_LOG_FILE"
}

install_cron() {
  require_command crontab
  mkdir -p "$APP_DIR/logs"

  local cron_line
  cron_line="0 */4 * * * cd $APP_DIR && $SCRIPT_PATH --once >> $APP_DIR/logs/github_sqlite_backup.log 2>&1"

  (
    crontab -l 2>/dev/null | grep -Fv "$SCRIPT_PATH --once" || true
    printf '%s\n' "$cron_line"
  ) | crontab -

  log "Installed cron entry:"
  log "$cron_line"
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
