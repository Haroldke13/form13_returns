#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"
RAW_MODE="${1:-sqlite}"

usage() {
  cat <<'EOF'
Usage:
  ./update.sh [sqlite|postgres]

Examples:
  ./update.sh
  ./update.sh sqlite
  ./update.sh postgres

Notes:
  - Default mode is sqlite.
  - Run this after updated source code has already been copied to the server.
EOF
}

case "$RAW_MODE" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

MODE="$(resolve_mode "$RAW_MODE")" || { usage >&2; die "Unknown mode: $RAW_MODE"; }
COMPOSE_FILE="$(compose_file_for_mode "$MODE")"

cd "$APP_DIR"
require_docker
require_env_file
if [[ "$MODE" == "postgres" ]]; then
  render_runtime_env_if_enabled
fi
warn_if_placeholder_env

log_info "Validating compose file: $COMPOSE_FILE"
validate_compose "$COMPOSE_FILE"

log_info "Rebuilding and restarting $MODE deployment..."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

if [[ "$MODE" == "postgres" ]] && ! external_postgres_enabled; then
  wait_for_postgres_container "$(read_env_value POSTGRES_DB form14)" "$(read_env_value POSTGRES_USER form14)" 45 2 || \
    die "PostgreSQL did not become ready in time."
fi

if [[ "$MODE" == "postgres" ]] && external_postgres_enabled; then
  log_info "Checking external PostgreSQL reachability."
  check_external_postgres || die "External PostgreSQL check failed."
fi

log_info "Running database initialization/migrations..."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T web flask init-db

log_info "Running post-update healthcheck..."
"$APP_DIR/healthcheck.sh" "$MODE"

echo
log_info "Update complete."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
