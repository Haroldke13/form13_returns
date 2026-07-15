#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"
RAW_MODE="sqlite"

usage() {
  cat <<'EOF'
Usage:
  ./deploy.sh [sqlite|postgres] [--no-build]

Examples:
  ./deploy.sh
  ./deploy.sh sqlite
  ./deploy.sh postgres
  ./deploy.sh postgres --no-build

Notes:
  - Default mode is sqlite.
  - By default Docker uses the layer cache while building the app image.
  - Use --no-build or PBORA_SKIP_DOCKER_BUILD=1 to reuse the existing app image.
  - If `.env.production` does not exist, this script copies `.env.production.example`
    to `.env.production` and stops so you can edit real server values first.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|help)
      usage
      exit 0
      ;;
    --no-build|--skip-build|--cached-image)
      export PBORA_SKIP_DOCKER_BUILD=1
      shift
      ;;
    sqlite|postgres|postgresql|pg)
      RAW_MODE="$1"
      shift
      ;;
    *)
      usage >&2
      die "Unknown argument: $1"
      ;;
  esac
done

MODE="$(resolve_mode "$RAW_MODE")" || { usage >&2; die "Unknown mode: $RAW_MODE"; }
COMPOSE_FILE="$(compose_file_for_mode "$MODE")"

cd "$APP_DIR"
require_docker
mkdir -p instance/uploads backups

if [[ ! -f .env.production ]]; then
  cp .env.production.example .env.production
  log_info "Created .env.production from .env.production.example"
  log_warn "Edit .env.production with real production values, then rerun this script."
  exit 1
fi

if [[ "$MODE" == "postgres" ]]; then
  render_runtime_env_if_enabled
fi

warn_if_placeholder_env

log_info "Validating compose file: $COMPOSE_FILE"
validate_compose "$COMPOSE_FILE"

if [[ "$MODE" == "postgres" ]]; then
  if external_postgres_enabled; then
    log_info "Using external PostgreSQL database from .env.production."
  else
    log_info "Waiting for PostgreSQL service to become ready after startup."
  fi
fi

log_info "Starting $MODE deployment..."
compose_up_detached "$COMPOSE_FILE"

if [[ "$MODE" == "postgres" ]] && ! external_postgres_enabled; then
  wait_for_postgres_container "$(read_env_value POSTGRES_DB form14)" "$(read_env_value POSTGRES_USER form14)" 45 2 || \
    die "PostgreSQL did not become ready in time."
fi

if [[ "$MODE" == "postgres" ]] && external_postgres_enabled; then
  log_info "Checking external PostgreSQL reachability."
  check_external_postgres || die "External PostgreSQL check failed."
fi

log_info "Initializing database..."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T web flask init-db

log_info "Running healthcheck..."
"$APP_DIR/healthcheck.sh" "$MODE"

echo
log_info "Deployment complete."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

if [[ -n "$(server_ip)" ]]; then
  echo "Open from another computer on the LAN: http://$(server_ip):$(host_port)"
else
  echo "Open from another computer on the LAN: http://<server-ip>:$(host_port)"
fi
