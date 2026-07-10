#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"
MODE="${1:-}"
BACKUP_FILE="${2:-}"
CONFIRM_FLAG="${3:-}"

usage() {
  cat <<'EOF'
Usage:
  ./restore.sh <sqlite|postgres> <backup-file> [--yes]

Examples:
  ./restore.sh sqlite backups/form14-2026-03-24-120000.db
  ./restore.sh postgres backups/form14-2026-03-24-120000.sql
  ./restore.sh postgres backups/form14-2026-03-24-120000.sql --yes

Notes:
  - This is a destructive restore.
  - The current database contents will be replaced.
EOF
}

confirm_restore() {
  if [[ "$CONFIRM_FLAG" == "--yes" ]]; then
    return
  fi
  echo "This will replace the current $MODE database using: $BACKUP_FILE"
  printf "Type RESTORE to continue: "
  read -r answer
  if [[ "$answer" != "RESTORE" ]]; then
    echo "Restore cancelled."
    exit 1
  fi
}

if [[ -z "$MODE" || -z "$BACKUP_FILE" ]]; then
  usage >&2
  exit 1
fi

case "$MODE" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

MODE="$(resolve_mode "$MODE")" || { usage >&2; die "Unknown mode: $MODE"; }
COMPOSE_FILE="$(compose_file_for_mode "$MODE")"

cd "$APP_DIR"
require_docker

if [[ ! -f "$BACKUP_FILE" ]]; then
  die "Backup file not found: $BACKUP_FILE"
fi

require_env_file

confirm_restore

log_info "Creating a safety backup before restore."
"$APP_DIR/backup.sh" "$MODE"

SERVICE_URL="$(public_base_url)"

if [[ "$MODE" == "sqlite" ]]; then
  mkdir -p instance
  run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" stop web >/dev/null 2>&1 || true
  cp "$BACKUP_FILE" instance/form14.db
  run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" up -d web
  wait_for_http "$SERVICE_URL" 30 2 || die "Web service did not come back after SQLite restore."
  run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" exec -T web flask init-db
  log_info "SQLite restore completed from $BACKUP_FILE"
  exit 0
fi

POSTGRES_DB="$(read_env_value POSTGRES_DB form14)"
POSTGRES_USER="$(read_env_value POSTGRES_USER form14)"

run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" up -d db

wait_for_postgres_container "$POSTGRES_DB" "$POSTGRES_USER" 45 2 || die "PostgreSQL container form14_db is not running or ready."

run_docker exec form14_db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
cat "$BACKUP_FILE" | run_docker exec -i form14_db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" up -d web
wait_for_http "$SERVICE_URL" 45 2 || die "Web service did not come back after PostgreSQL restore."
run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" exec -T web flask init-db

log_info "PostgreSQL restore completed from $BACKUP_FILE"
