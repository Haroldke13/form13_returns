#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$APP_DIR/_deploy_common.sh"

RAW_MODE="${1:-postgres}"
CLOUDFLARE_ENV_FILE="${CLOUDFLARE_ENV_FILE:-$APP_DIR/.env.cloudflare}"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/cloudflare_tunnel_up.sh [sqlite|postgres]

Starts the app with the Cloudflare Tunnel sidecar.
Create .env.cloudflare from .env.cloudflare.example before running.
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
CLOUDFLARE_COMPOSE_FILE="docker-compose.cloudflare.yml"

cd "$APP_DIR"
require_docker
require_env_file
ensure_file_exists "$APP_DIR/$CLOUDFLARE_COMPOSE_FILE"

if [[ ! -f "$CLOUDFLARE_ENV_FILE" ]]; then
  die "Cloudflare env file not found: $CLOUDFLARE_ENV_FILE. Copy .env.cloudflare.example to .env.cloudflare and add the tunnel token."
fi

set -a
# shellcheck disable=SC1090
source "$CLOUDFLARE_ENV_FILE"
set +a

[[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]] || die "CLOUDFLARE_TUNNEL_TOKEN is empty in $CLOUDFLARE_ENV_FILE."

log_info "Validating Docker Compose configuration with Cloudflare Tunnel."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$CLOUDFLARE_COMPOSE_FILE" config --quiet

log_info "Starting $MODE deployment with Cloudflare Tunnel."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$CLOUDFLARE_COMPOSE_FILE" up -d --build

log_info "Deployment status:"
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" -f "$CLOUDFLARE_COMPOSE_FILE" ps
