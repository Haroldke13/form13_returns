#!/usr/bin/env bash
set -euo pipefail

# PBORA Form 13 production setup and deployment sequence.
#
# This script documents and automates the Linux server setup used to host the
# Flask app on the LAN. It intentionally does not contain passwords, API keys,
# database dumps, private certificates, or other secrets.
#
# Usage:
#   chmod +x setup_steps.sh
#   ./setup_steps.sh
#
# Optional environment overrides:
#   APP_DIR=/home/pbora/form13_returns
#   APP_USER=pbora
#   APP_IP=10.107.20.241
#   APP_GATEWAY=10.107.20.254
#   LAN_CIDR=10.107.0.0/19
#   HOST_PORT=8000
#   DB_NAME=pbora
#   DB_USER=pbora
#   DB_PORT=5432

APP_DIR="${APP_DIR:-/home/pbora/form13_returns}"
APP_USER="${APP_USER:-pbora}"
APP_IP="${APP_IP:-10.107.20.241}"
APP_GATEWAY="${APP_GATEWAY:-10.107.20.254}"
LAN_CIDR="${LAN_CIDR:-10.107.0.0/19}"
HOST_PORT="${HOST_PORT:-8000}"
DB_NAME="${DB_NAME:-pbora}"
DB_USER="${DB_USER:-pbora}"
DB_PORT="${DB_PORT:-5432}"
DOCKER_BRIDGE_CIDRS="${DOCKER_BRIDGE_CIDRS:-172.17.0.0/16,172.18.0.0/16}"

log() {
  printf '[setup] %s\n' "$*"
}

die() {
  printf '[setup] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required."
}

require_ubuntu_or_debian() {
  [[ -r /etc/os-release ]] || die "/etc/os-release not found."
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian)
      ;;
    *)
      die "This setup script expects Ubuntu or Debian. Detected ID=${ID:-unknown}."
      ;;
  esac
}

sudo_refresh() {
  require_command sudo
  sudo -v
}

read_env_value() {
  local key="$1"
  local default_value="${2:-}"
  local value=""

  if [[ -f "$APP_DIR/.env.production" ]]; then
    value="$(awk -F= -v target="$key" '$1 == target {sub(/^[^=]*=/, "", $0); print $0}' "$APP_DIR/.env.production" | tail -n1 | sed 's/\r$//')"
  fi

  if [[ -n "$value" ]]; then
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s' "$value"
  else
    printf '%s' "$default_value"
  fi
}

sql_literal() {
  printf '%s' "$1" | sed "s/'/''/g"
}

validate_pg_identifier() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "Unsafe PostgreSQL identifier: $value"
}

install_base_packages() {
  log "Installing base OS packages."
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    apt-transport-https \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    iproute2 \
    lsb-release \
    nano \
    nginx \
    openssl \
    postgresql \
    postgresql-client \
    postgresql-contrib \
    python3 \
    python3-pip \
    python3-venv \
    ufw \
    unzip
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker and Docker Compose are already installed."
  else
    log "Installing Docker Engine and Docker Compose plugin."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    local codename arch
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    [[ -n "$codename" ]] || die "Could not determine Ubuntu/Debian codename."
    arch="$(dpkg --print-architecture)"

    sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $codename
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
      containerd.io \
      docker-buildx-plugin \
      docker-ce \
      docker-ce-cli \
      docker-compose-plugin
  fi

  sudo systemctl enable --now docker
  sudo systemctl enable --now docker.socket
  sudo usermod -aG docker "$APP_USER"
  log "Docker group membership applied. Existing shells may need: newgrp docker"
}

ensure_project_layout() {
  log "Checking project directory."
  [[ -d "$APP_DIR" ]] || die "Application directory not found: $APP_DIR"
  cd "$APP_DIR"

  mkdir -p instance/uploads backups certs
  sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR/instance" "$APP_DIR/backups" "$APP_DIR/certs"
  chmod -R u+rwX "$APP_DIR/instance" "$APP_DIR/backups" "$APP_DIR/certs"
}

prepare_env_file() {
  log "Preparing .env.production."
  cd "$APP_DIR"

  if [[ ! -f .env.production ]]; then
    [[ -f .env.production.example ]] || die ".env.production.example not found."
    cp .env.production.example .env.production
    log ".env.production created from example. Edit secrets before production use."
  fi

  python3 - "$APP_DIR/.env.production" "$APP_IP" "$APP_GATEWAY" "$LAN_CIDR" "$HOST_PORT" "$DB_NAME" "$DB_USER" "$DB_PORT" "$DOCKER_BRIDGE_CIDRS" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
app_ip, gateway, lan_cidr, host_port, db_name, db_user, db_port, docker_cidrs = sys.argv[2:]

env_values = {}
for raw in env_path.read_text().splitlines():
    if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
        key, value = raw.split("=", 1)
        env_values[key] = value.strip().strip('"').strip("'")

db_password = (
    env_values.get("DB_PASSWORD")
    or env_values.get("POSTGRES_PASSWORD")
    or "change-this-db-password"
)
database_url = f"postgresql+psycopg2://{db_user}:{db_password}@{app_ip}:{db_port}/{db_name}"

values = {
    "ALLOWED_HOSTS": f"{app_ip},172.17.0.1,172.18.0.1,localhost,127.0.0.1,returnsform14.onrender.com,www.returnsform14.org,returnsform14.org",
    "ANNUAL_RETURNS_DATABASE_URL": database_url,
    "APP_HOST": "0.0.0.0",
    "DATABASE_URL": database_url,
    "DB_HOST": app_ip,
    "DB_NAME": db_name,
    "DB_PORT": db_port,
    "DB_SSL_MODE": "disable",
    "DB_USER": db_user,
    "DOCKER_BIND_IP": app_ip,
    "INTERNAL_DATABASE_URL": database_url,
    "FLASK_DEBUG": "0",
    "FLASK_RUN_HOST": "0.0.0.0",
    "FORCE_CANONICAL_HOST": "0",
    "HOST_PORT": host_port,
    "MAX_CONTENT_LENGTH": "52428800",
    "MAX_REQUEST_MB": "50",
    "PBORA_APP_HOST_IP": app_ip,
    "PBORA_DATABASE_URL": database_url,
    "PBORA_GATEWAY": gateway,
    "PBORA_POSTGRES_HOST": app_ip,
    "PBORA_POSTGRES_PORT": db_port,
    "PBORA_REPLACE_DEFAULT_ROUTE": "1",
    "PBORA_REPLACE_LAN_ROUTE": "0",
    "PBORA_ROUTE_CIDR": lan_cidr,
    "PBORA_ROUTE_PREFIX": "19",
    "POSTGRES_ALLOWED_CIDR": lan_cidr,
    "POSTGRES_EXTRA_ALLOWED_CIDRS": docker_cidrs,
    "POSTGRES_DB": db_name,
    "POSTGRES_HOST": app_ip,
    "POSTGRES_PASSWORD": db_password,
    "POSTGRES_PORT": db_port,
    "POSTGRES_SSL_MODE": "disable",
    "POSTGRES_USER": db_user,
    "PREFERRED_URL_SCHEME": "https",
    "PUBLIC_BASE_URL": f"https://{app_ip}",
    "PUBLIC_HOST_PORT": "443",
    "RUN_SCHEMA_SYNC_ON_STARTUP": "1",
    "SESSION_COOKIE_SECURE": "true",
}

existing = {}
order = []
for raw in env_path.read_text().splitlines():
    if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
        key = raw.split("=", 1)[0]
        existing[key] = raw
        order.append(key)

for key, value in values.items():
    existing[key] = f"{key}={value}"
    if key not in order:
        order.append(key)

written = []
seen = set()
for raw in env_path.read_text().splitlines():
    if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
        key = raw.split("=", 1)[0]
        if key in existing:
            written.append(existing[key])
            seen.add(key)
        else:
            written.append(raw)
    else:
        written.append(raw)

for key in order:
    if key not in seen and key in existing:
        written.append(existing[key])

env_path.write_text("\n".join(written).rstrip() + "\n")
PY
}

generate_local_certificates() {
  log "Ensuring local HTTPS certificates exist."
  cd "$APP_DIR"

  if [[ -f certs/server.crt && -f certs/server.key ]]; then
    return 0
  fi

  local openssl_conf
  openssl_conf="$(mktemp)"
  cat >"$openssl_conf" <<EOF
[req]
prompt = no
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = $APP_IP

[v3_req]
subjectAltName = IP:$APP_IP
EOF

  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout certs/server.key \
    -out certs/server.crt \
    -config "$openssl_conf" \
    -extensions v3_req

  rm -f "$openssl_conf"
  chmod 600 certs/server.key
}

ensure_postgres_database() {
  log "Ensuring local PostgreSQL role and database exist."
  validate_pg_identifier "$DB_NAME"
  validate_pg_identifier "$DB_USER"

  local db_password db_password_sql
  db_password="$(read_env_value DB_PASSWORD "$(read_env_value POSTGRES_PASSWORD "")")"
  [[ -n "$db_password" ]] || die "DB_PASSWORD or POSTGRES_PASSWORD must be set in .env.production."
  db_password_sql="$(sql_literal "$db_password")"

  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE "$DB_USER" LOGIN PASSWORD '$db_password_sql';
  ELSE
    ALTER ROLE "$DB_USER" WITH LOGIN PASSWORD '$db_password_sql';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE "$DB_NAME" OWNER "$DB_USER"' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$DB_NAME')\gexec
ALTER DATABASE "$DB_NAME" OWNER TO "$DB_USER";
\c "$DB_NAME"
GRANT ALL ON SCHEMA public TO "$DB_USER";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "$DB_USER";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "$DB_USER";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "$DB_USER";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "$DB_USER";
SQL
}

configure_postgres_access() {
  log "Configuring PostgreSQL for LAN/container access."
  cd "$APP_DIR"
  sudo systemctl enable --now postgresql

  if [[ -x scripts/fix_postgres_access.sh ]]; then
    sudo ENV_FILE="$APP_DIR/.env.production" "$APP_DIR/scripts/fix_postgres_access.sh"
  else
    die "scripts/fix_postgres_access.sh not found or not executable."
  fi
}

configure_firewall() {
  log "Configuring firewall policy requested for this deployment."
  sudo ufw default allow incoming
  sudo ufw default allow outgoing
  sudo ufw default allow routed
  sudo ufw allow 80/tcp || true
  sudo ufw allow 443/tcp || true
  sudo ufw allow "$HOST_PORT/tcp" || true
  sudo ufw allow "$DB_PORT/tcp" || true
  sudo ufw --force enable
  sudo ufw reload
  sudo ufw status verbose
}

configure_routes() {
  log "Configuring LAN route via gateway for routed WiFi/VLAN clients."
  sudo ip route replace "$LAN_CIDR" via "$APP_GATEWAY" dev eth0 src "$APP_IP"
  ip route get "$APP_IP" >/dev/null
  ip -4 route show
}

configure_nginx() {
  log "Configuring nginx reverse proxy."
  sudo tee /etc/nginx/sites-available/pbora-form13 >/dev/null <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;

    ssl_certificate $APP_DIR/certs/server.crt;
    ssl_certificate_key $APP_DIR/certs/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    client_max_body_size 50m;

    location / {
        proxy_pass https://$APP_IP:$HOST_PORT;
        proxy_ssl_verify off;
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;

        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_connect_timeout 30s;
        proxy_send_timeout 360s;
        proxy_read_timeout 360s;
    }
}
EOF

  sudo rm -f /etc/nginx/sites-enabled/default
  sudo ln -sf /etc/nginx/sites-available/pbora-form13 /etc/nginx/sites-enabled/pbora-form13
  sudo nginx -t
  sudo systemctl enable --now nginx
  sudo systemctl reload nginx
}

deploy_app() {
  log "Deploying Flask app with Docker Compose."
  cd "$APP_DIR"

  python3 ip_address_change.py --render-runtime-env --env-file .env.production --no-file-backup || true

  if docker info >/dev/null 2>&1; then
    ./deploy.sh postgres
  elif command -v sg >/dev/null 2>&1; then
    sg docker -c './deploy.sh postgres'
  else
    sudo ./deploy.sh postgres
  fi
}

verify_deployment() {
  log "Verifying deployment."
  cd "$APP_DIR"

  if docker info >/dev/null 2>&1; then
    docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
  elif command -v sg >/dev/null 2>&1; then
    sg docker -c "docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'"
  else
    sudo docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
  fi

  if docker info >/dev/null 2>&1; then
    ./healthcheck.sh postgres
  elif command -v sg >/dev/null 2>&1; then
    sg docker -c './healthcheck.sh postgres'
  else
    sudo ./healthcheck.sh postgres
  fi

  curl -k -I --max-time 10 "https://$APP_IP/"
  curl -I --max-time 10 "http://$APP_IP/"
  curl -k -I --max-time 10 "https://$APP_IP:$HOST_PORT/"

  log "LAN URL: https://$APP_IP/"
}

main() {
  require_ubuntu_or_debian
  sudo_refresh
  install_base_packages
  install_docker
  ensure_project_layout
  prepare_env_file
  generate_local_certificates
  ensure_postgres_database
  configure_postgres_access
  configure_firewall
  configure_routes
  configure_nginx
  deploy_app
  verify_deployment
}

main "$@"
