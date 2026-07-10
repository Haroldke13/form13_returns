#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"
RAW_MODE="${1:-sqlite}"

usage() {
  cat <<'EOF'
Usage:
  ./healthcheck.sh [sqlite|postgres]
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

PORT="$(read_env_value HOST_PORT 8000)"
URL="$(public_base_url)"
if [[ -f "$APP_DIR/certs/server.crt" && -f "$APP_DIR/certs/server.key" ]]; then
  URL="${URL/http:\/\//https:\/\/}"
fi

log_info "Compose status:"
run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" ps
echo
log_info "HTTP check: $URL"
wait_for_http "$URL" 15 2 || die "HTTP healthcheck failed for $URL"
log_info "HTTP check passed."

if [[ "$MODE" == "postgres" ]] && ! external_postgres_enabled; then
  log_info "Database check:"
  run_docker exec form14_db pg_isready -U "$(read_env_value POSTGRES_USER form14)" -d "$(read_env_value POSTGRES_DB form14)"
fi

if [[ "$MODE" == "postgres" ]] && external_postgres_enabled; then
  log_info "External PostgreSQL check:"
  check_external_postgres
fi

echo
log_info "Healthcheck passed."
if [[ -n "$(server_ip)" ]]; then
  echo "LAN URL: http://$(server_ip):$PORT"
fi
