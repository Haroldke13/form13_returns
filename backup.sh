#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"
RAW_MODE="${1:-sqlite}"
TIMESTAMP="$(date +%F-%H%M%S)"
BACKUP_DIR="$APP_DIR/backups"

usage() {
  cat <<'EOF'
Usage:
  ./backup.sh [sqlite|postgres]

Examples:
  ./backup.sh
  ./backup.sh sqlite
  ./backup.sh postgres
EOF
}

case "$RAW_MODE" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

MODE="$(resolve_mode "$RAW_MODE")" || { usage >&2; die "Unknown mode: $RAW_MODE"; }

cd "$APP_DIR"
mkdir -p "$BACKUP_DIR" instance/uploads

UPLOADS_ARCHIVE="$BACKUP_DIR/uploads-$TIMESTAMP.tar.gz"
tar -czf "$UPLOADS_ARCHIVE" instance/uploads

if [[ "$MODE" == "sqlite" ]]; then
  DB_FILE="$APP_DIR/instance/form14.db"
  if [[ ! -f "$DB_FILE" ]]; then
    echo "SQLite database not found at $DB_FILE" >&2
    exit 1
  fi
  DB_BACKUP="$BACKUP_DIR/form14-$TIMESTAMP.db"
  cp "$DB_FILE" "$DB_BACKUP"
  log_info "SQLite database backup: $DB_BACKUP"
  log_info "Uploads backup: $UPLOADS_ARCHIVE"
  exit 0
fi

require_docker
require_env_file
POSTGRES_DB="$(read_env_value POSTGRES_DB form14)"
POSTGRES_USER="$(read_env_value POSTGRES_USER form14)"
DB_BACKUP="$BACKUP_DIR/form14-$TIMESTAMP.sql"

if ! run_docker ps --format '{{.Names}}' | grep -qx 'form14_db'; then
  die "PostgreSQL container form14_db is not running."
fi

run_docker exec form14_db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$DB_BACKUP"

log_info "PostgreSQL database backup: $DB_BACKUP"
log_info "Uploads backup: $UPLOADS_ARCHIVE"
