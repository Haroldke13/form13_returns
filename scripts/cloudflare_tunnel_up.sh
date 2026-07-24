#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$APP_DIR/_deploy_common.sh"

RAW_MODE="postgres"
RAW_TUNNEL_TARGET="primary"
CLOUDFLARE_ENV_FILE="${CLOUDFLARE_ENV_FILE:-$APP_DIR/.env.cloudflare}"
NO_BUILD=0

usage() {
  cat <<'EOF'
Usage:
  ./scripts/cloudflare_tunnel_up.sh [sqlite|postgres] [primary|dummy|both] [--no-build]

Starts the app with one or both Cloudflare Tunnel sidecars.
Create .env.cloudflare from .env.cloudflare.example before running.
EOF
}

resolve_tunnel_target() {
  case "${1:-primary}" in
    primary|default|returnsform14|returnsform14-tunnel)
      printf '%s' "primary"
      ;;
    dummy|dummy-tunnel|"dummy tunnel")
      printf '%s' "dummy"
      ;;
    both|all)
      printf '%s' "both"
      ;;
    *)
      return 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|help)
      usage
      exit 0
      ;;
    --no-build)
      NO_BUILD=1
      shift
      ;;
    sqlite|postgres|postgresql|pg)
      RAW_MODE="$1"
      shift
      ;;
    primary|default|returnsform14|returnsform14-tunnel|dummy|dummy-tunnel|"dummy tunnel"|both|all)
      RAW_TUNNEL_TARGET="$1"
      shift
      ;;
    *)
      usage >&2
      die "Unknown argument: $1"
      ;;
  esac
done

MODE="$(resolve_mode "$RAW_MODE")" || { usage >&2; die "Unknown mode: $RAW_MODE"; }
TUNNEL_TARGET="$(resolve_tunnel_target "$RAW_TUNNEL_TARGET")" || { usage >&2; die "Unknown tunnel target: $RAW_TUNNEL_TARGET"; }
COMPOSE_FILE="$(compose_file_for_mode "$MODE")"
CLOUDFLARE_COMPOSE_FILE="docker-compose.cloudflare.yml"
CLOUDFLARE_DUMMY_COMPOSE_FILE="docker-compose.cloudflare.dummy.yml"
CLOUDFLARE_TOKEN_DIR="$APP_DIR/.cloudflared"
CLOUDFLARE_PRIMARY_TOKEN_FILE="$CLOUDFLARE_TOKEN_DIR/returnsform14-tunnel.token"
CLOUDFLARE_DUMMY_TOKEN_FILE="$CLOUDFLARE_TOKEN_DIR/dummy-tunnel.token"

cd "$APP_DIR"
require_docker
require_env_file

if [[ ! -f "$CLOUDFLARE_ENV_FILE" ]]; then
  die "Cloudflare env file not found: $CLOUDFLARE_ENV_FILE. Copy .env.cloudflare.example to .env.cloudflare and add the tunnel token."
fi

set -a
# shellcheck disable=SC1090
source "$CLOUDFLARE_ENV_FILE"
set +a

ensure_cloudflare_token_file() {
  local env_name="$1"
  local token_file="$2"
  local token="${!env_name:-}"

  mkdir -p "$CLOUDFLARE_TOKEN_DIR"
  chmod 700 "$CLOUDFLARE_TOKEN_DIR"
  if [[ -n "$token" ]]; then
    printf '%s\n' "$token" >"$token_file"
    chmod 600 "$token_file"
  fi

  [[ -s "$token_file" ]] || die "$env_name is empty in $CLOUDFLARE_ENV_FILE and token file not found: $token_file"
}

compose_args=(--env-file "$ENV_FILE" --env-file "$CLOUDFLARE_ENV_FILE" -f "$COMPOSE_FILE")
services=(web)

if [[ "$TUNNEL_TARGET" == "primary" || "$TUNNEL_TARGET" == "both" ]]; then
  ensure_file_exists "$APP_DIR/$CLOUDFLARE_COMPOSE_FILE"
  ensure_cloudflare_token_file CLOUDFLARE_TUNNEL_TOKEN "$CLOUDFLARE_PRIMARY_TOKEN_FILE"
  compose_args+=(-f "$CLOUDFLARE_COMPOSE_FILE")
  services+=(cloudflared)
fi

if [[ "$TUNNEL_TARGET" == "dummy" || "$TUNNEL_TARGET" == "both" ]]; then
  ensure_file_exists "$APP_DIR/$CLOUDFLARE_DUMMY_COMPOSE_FILE"
  ensure_cloudflare_token_file CLOUDFLARE_DUMMY_TUNNEL_TOKEN "$CLOUDFLARE_DUMMY_TOKEN_FILE"
  compose_args+=(-f "$CLOUDFLARE_DUMMY_COMPOSE_FILE")
  services+=(cloudflared_dummy)
fi

log_info "Validating Docker Compose configuration with Cloudflare Tunnel target: $TUNNEL_TARGET."
docker_compose_cmd "${compose_args[@]}" config --quiet

if [[ "$NO_BUILD" == "1" ]] || skip_docker_build_enabled; then
  log_info "Starting $MODE deployment with Cloudflare Tunnel target: $TUNNEL_TARGET without rebuilding the app image."
  docker_compose_cmd "${compose_args[@]}" up -d --no-build "${services[@]}"
else
  log_info "Starting $MODE deployment with Cloudflare Tunnel target: $TUNNEL_TARGET."
  docker_compose_cmd "${compose_args[@]}" up -d --build "${services[@]}"
fi

log_info "Deployment status:"
docker_compose_cmd "${compose_args[@]}" ps
