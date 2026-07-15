#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$APP_DIR/_deploy_common.sh"

RAW_MODE="postgres"
NGROK_ENV_FILE="${NGROK_ENV_FILE:-$APP_DIR/.env.ngrok}"
NGROK_COMPOSE_FILE="docker-compose.ngrok.yml"
NO_BUILD=0

usage() {
  cat <<'EOF'
Usage:
  ./scripts/ngrok_tunnel_up.sh [sqlite|postgres] [--no-build]

Starts or refreshes the app with an ngrok sidecar and local inspector.

Required local file:
  .env.ngrok with NGROK_AUTHTOKEN set

Outputs:
  - ngrok public URL
  - local inspector URL, usually http://127.0.0.1:4040
EOF
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
    sqlite|postgres|mysql)
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
require_env_file
ensure_file_exists "$APP_DIR/$NGROK_COMPOSE_FILE"

if [[ ! -f "$NGROK_ENV_FILE" ]]; then
  die "ngrok env file not found: $NGROK_ENV_FILE. Copy .env.ngrok.example to .env.ngrok and set NGROK_AUTHTOKEN."
fi

set -a
# shellcheck disable=SC1090
source "$NGROK_ENV_FILE"
set +a

[[ -n "${NGROK_AUTHTOKEN:-}" ]] || die "NGROK_AUTHTOKEN is empty in $NGROK_ENV_FILE. Sign in at https://dashboard.ngrok.com/get-started/your-authtoken and paste the token there."

INSPECTOR_HOST="${NGROK_INSPECTOR_HOST:-127.0.0.1}"
INSPECTOR_PORT="${NGROK_INSPECTOR_PORT:-4040}"
INSPECTOR_URL="http://${INSPECTOR_HOST}:${INSPECTOR_PORT}"

compose_args=(--env-file "$ENV_FILE" --env-file "$NGROK_ENV_FILE" -f "$COMPOSE_FILE" -f "$NGROK_COMPOSE_FILE")

log_info "Validating Docker Compose configuration with ngrok sidecar."
docker_compose_cmd "${compose_args[@]}" config --quiet

if [[ "$NO_BUILD" == "1" ]]; then
  log_info "Starting $MODE deployment with ngrok sidecar without rebuilding the app image."
  docker_compose_cmd "${compose_args[@]}" up -d --no-build web ngrok
else
  log_info "Building/restarting $MODE deployment with ngrok sidecar."
  docker_compose_cmd "${compose_args[@]}" up -d --build web ngrok
fi

log_info "Waiting for ngrok inspector API at $INSPECTOR_URL."
public_url=""
for _ in {1..30}; do
  public_url="$(
    curl -fsS "$INSPECTOR_URL/api/tunnels" 2>/dev/null \
      | python3 -c 'import json,sys; data=json.load(sys.stdin); print(next((t.get("public_url","") for t in data.get("tunnels", []) if t.get("proto") in {"https", "http"} and t.get("public_url", "").startswith("https://")), ""))' \
      || true
  )"
  if [[ -n "$public_url" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "$public_url" ]]; then
  docker logs form14_ngrok --tail 120 || true
  die "ngrok did not expose a public HTTPS URL through $INSPECTOR_URL/api/tunnels."
fi

public_host="$(python3 - "$public_url" <<'PY'
from urllib.parse import urlparse
import sys
print(urlparse(sys.argv[1]).hostname or "")
PY
)"
[[ -n "$public_host" ]] || die "Could not parse ngrok public host from $public_url."

allowed_hosts="$(
  python3 - "$public_host" "${NGROK_CLOUDFLARE_HOSTS:-}" <<'PY'
import sys
hosts = []
for raw in sys.argv[1:]:
    for item in (raw or "").split(","):
        item = item.strip()
        if item and item not in hosts:
            hosts.append(item)
print(",".join(hosts))
PY
)"

python3 - "$NGROK_ENV_FILE" "$allowed_hosts" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
allowed_hosts = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
found = False
for index, line in enumerate(lines):
    if line.startswith("NGROK_ALLOWED_HOSTS="):
        lines[index] = f"NGROK_ALLOWED_HOSTS={allowed_hosts}"
        found = True
        break
if not found:
    lines.append(f"NGROK_ALLOWED_HOSTS={allowed_hosts}")
path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
PY

log_info "Updated NGROK_ALLOWED_HOSTS in $NGROK_ENV_FILE: $allowed_hosts"
log_info "Recreating web container so Flask trusts the ngrok/CNAME hostnames."
docker_compose_cmd "${compose_args[@]}" up -d --no-deps --force-recreate web

log_info "Waiting for recreated web container to become healthy."
for _ in {1..30}; do
  status="$(docker inspect -f '{{.State.Health.Status}}' form14_web 2>/dev/null || true)"
  if [[ "$status" == "healthy" ]]; then
    break
  fi
  sleep 2
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' form14_web 2>/dev/null || true)" == "healthy" ]] || die "form14_web did not become healthy after ngrok host update."

log_info "Testing ngrok public URL."
curl -fsSI --max-time 20 "$public_url" >/tmp/form14-ngrok-head.txt
head_status="$(awk 'toupper($0) ~ /^HTTP\\// {print $2; exit}' /tmp/form14-ngrok-head.txt)"
case "$head_status" in
  200|301|302|303|307|308)
    ;;
  *)
    cat /tmp/form14-ngrok-head.txt >&2
    die "Unexpected ngrok HTTP status: ${head_status:-unknown}"
    ;;
esac

log_info "ngrok public URL: $public_url"
log_info "ngrok inspector: $INSPECTOR_URL"
log_info "Deployment status:"
docker_compose_cmd "${compose_args[@]}" ps
