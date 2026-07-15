#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"

MODE="sqlite"
DOMAIN_NAME="${CANONICAL_HOSTNAME:-returnsform13.org}"
APP_PORT="${APP_PORT:-$(host_port)}"
APP_HOST="${APP_HOST:-127.0.0.1}"
DEPLOY_ARGS=()

usage() {
  cat <<EOF
Usage:
  ./deploy_once.sh [sqlite|postgres] [domain-name] [--no-build]

Examples:
  ./deploy_once.sh sqlite returnsform13.org
  ./deploy_once.sh postgres returnsform13.org
  ./deploy_once.sh postgres returnsform13.org --no-build

This script will:
  1. prepare the production environment file,
  2. deploy the Flask app with Docker,
  3. install and configure Caddy for HTTPS,
  4. print the domain and server IP to use.
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
      DEPLOY_ARGS+=("--no-build")
      shift
      ;;
    sqlite|postgres|postgresql|pg)
      MODE="$(resolve_mode "$1")"
      shift
      ;;
    *)
      DOMAIN_NAME="$1"
      shift
      ;;
  esac
done

if [[ "$MODE" != "sqlite" && "$MODE" != "postgres" ]]; then
  usage >&2
  die "Unsupported mode: $MODE"
fi

if [[ $EUID -eq 0 ]]; then
  SUDO=""
else
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    die "Run this script as root or install sudo."
  fi
fi

ensure_file_exists "$APP_DIR/_deploy_common.sh"
cd "$APP_DIR"

require_docker
command -v python3 >/dev/null 2>&1 || die "python3 is required."

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
  log_info "Created $ENV_FILE from $ENV_EXAMPLE_FILE"
fi

python3 - "$ENV_FILE" "$DOMAIN_NAME" <<'PY'
import os
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
domain = sys.argv[2].strip().lower()

if not domain:
    raise SystemExit('Domain name is required')

lines = []
updated = False
for line in env_path.read_text().splitlines():
    if line.startswith('CANONICAL_HOSTNAME='):
        lines.append(f'CANONICAL_HOSTNAME={domain}')
        updated = True
    elif line.startswith('FORCE_CANONICAL_HOST='):
        lines.append('FORCE_CANONICAL_HOST=1')
        updated = True
    elif line.startswith('PREFERRED_URL_SCHEME='):
        lines.append('PREFERRED_URL_SCHEME=https')
        updated = True
    elif line.startswith('ALLOWED_HOSTS='):
        existing = line.split('=', 1)[1]
        hosts = [item.strip() for item in existing.split(',') if item.strip()]
        if domain not in hosts:
            hosts.append(domain)
        if 'returnsform13.com' in hosts and domain != 'returnsform13.com':
            hosts = [h for h in hosts if h != 'returnsform13.com']
        if 'returnsform13.org' in hosts and domain != 'returnsform13.org':
            hosts = [h for h in hosts if h != 'returnsform13.org']
        lines.append('ALLOWED_HOSTS=' + ','.join(hosts))
        updated = True
    else:
        lines.append(line)

if not updated:
    lines.extend([
        f'CANONICAL_HOSTNAME={domain}',
        'FORCE_CANONICAL_HOST=1',
        'PREFERRED_URL_SCHEME=https',
        'ALLOWED_HOSTS=auto,localhost,127.0.0.1,' + domain,
    ])

env_path.write_text('\n'.join(lines) + '\n')
PY

log_info "Deploying the application in $MODE mode..."
./deploy.sh "$MODE" "${DEPLOY_ARGS[@]}"

install_caddy() {
  if command -v caddy >/dev/null 2>&1; then
    return 0
  fi

  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update
    $SUDO apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
    $SUDO install -d -m 0755 /usr/share/keyrings
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | $SUDO gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" | $SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    $SUDO apt-get update
    $SUDO apt-get install -y caddy
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y caddy
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y caddy
  else
    die "Could not install Caddy automatically. Install it manually and rerun this script."
  fi
}

configure_caddy() {
  local domain="$1"
  local port="$2"
  local caddyfile="/etc/caddy/Caddyfile"
  local tmpfile="$(mktemp)"

  cat > "$tmpfile" <<EOF
$domain {
    reverse_proxy $APP_HOST:$port
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
}

www.$domain {
    redir https://$domain{uri} 308
}
EOF

  $SUDO install -d -m 0755 /etc/caddy
  $SUDO install -m 0644 "$tmpfile" "$caddyfile"
  rm -f "$tmpfile"

  if command -v systemctl >/dev/null 2>&1; then
    $SUDO systemctl daemon-reload || true
    $SUDO systemctl enable caddy || true
    $SUDO systemctl restart caddy || true
  else
    $SUDO service caddy restart || true
  fi

  if command -v ufw >/dev/null 2>&1; then
    $SUDO ufw allow 80/tcp >/dev/null 2>&1 || true
    $SUDO ufw allow 443/tcp >/dev/null 2>&1 || true
  fi
}

log_info "Installing Caddy reverse proxy..."
install_caddy
log_info "Configuring HTTPS for $DOMAIN_NAME..."
configure_caddy "$DOMAIN_NAME" "$APP_PORT"

SERVER_IP="$(server_ip)"

cat <<EOF

Deployment summary
==================
Domain: https://$DOMAIN_NAME
Server IP: $SERVER_IP
Application port: $APP_PORT
Fallback direct access: http://$SERVER_IP:$APP_PORT

Next steps:
  - Make sure DNS for $DOMAIN_NAME points to $SERVER_IP
  - If Caddy says it is waiting for DNS, wait a few minutes and retry
  - Browse to https://$DOMAIN_NAME
EOF
