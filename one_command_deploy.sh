#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ $EUID -eq 0 ]]; then
  echo "Do not run this script as root. Use your normal user account instead." >&2
  exit 1
fi

MODE="postgres"
SOURCE_SQLITE="${SOURCE_SQLITE:-$APP_DIR/returnsform14_org_backup.sqlite}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.production}"
ENV_EXAMPLE_FILE="${ENV_EXAMPLE_FILE:-$APP_DIR/.env.production.example}"
HOST_PORT="${HOST_PORT:-8000}"
PBORA_DEPLOY_ENV="${PBORA_DEPLOY_ENV:-production}"
case "$PBORA_DEPLOY_ENV" in
  production) DEFAULT_SUDO_PASSWORD="pbora" ;;
  development|dev) DEFAULT_SUDO_PASSWORD="Programmer.95" ;;
  *) DEFAULT_SUDO_PASSWORD="pbora" ;;
esac
PBORA_SUDO_PASSWORD="${PBORA_SUDO_PASSWORD:-${SUDO_PASSWORD:-$DEFAULT_SUDO_PASSWORD}}"
DOCKER_CMD=(docker)
APT_UPDATED=0
DOCKER_APT_REPAIR_CHECKED=0
SUDO_PRIMED=0
SUDO_KEEPALIVE_PID=""
DOCKER_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
CONFLICTING_DOCKER_PACKAGES=(docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc)

export DEBIAN_FRONTEND=noninteractive

die() {
  echo "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./one_command_deploy.sh [sqlite|postgres] [--no-build]

Examples:
  ./one_command_deploy.sh
  ./one_command_deploy.sh postgres
  ./one_command_deploy.sh postgres --no-build

Notes:
  - By default Docker uses cached image layers while building the app image.
  - Use --no-build or PBORA_SKIP_DOCKER_BUILD=1 to reuse the existing app image.
EOF
}

cleanup_sudo_keepalive() {
  if [[ -n "${SUDO_KEEPALIVE_PID:-}" ]]; then
    kill "$SUDO_KEEPALIVE_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup_sudo_keepalive EXIT

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
      case "$1" in
        sqlite) MODE="sqlite" ;;
        postgres|postgresql|pg) MODE="postgres" ;;
      esac
      shift
      ;;
    *)
      usage >&2
      die "Unknown argument: $1"
      ;;
  esac
done

prime_sudo() {
  if [[ "$SUDO_PRIMED" -eq 1 ]]; then
    return 0
  fi
  command -v sudo >/dev/null 2>&1 || die "sudo is required for one-command deployment."

  if sudo -n true >/dev/null 2>&1; then
    SUDO_PRIMED=1
    return 0
  fi

  printf '%s\n' "$PBORA_SUDO_PASSWORD" | sudo -S -p '' -v >/dev/null 2>&1 || \
    die "sudo authentication failed for PBORA_DEPLOY_ENV=$PBORA_DEPLOY_ENV."

  (
    while true; do
      sleep 60
      printf '%s\n' "$PBORA_SUDO_PASSWORD" | sudo -S -p '' -v >/dev/null 2>&1 || exit 0
    done
  ) &
  SUDO_KEEPALIVE_PID="$!"
  SUDO_PRIMED=1
}

require_sudo() {
  command -v sudo >/dev/null 2>&1 || die "sudo is required to repair file permissions. Install sudo or fix ownership manually."
  prime_sudo
}

require_apt_get() {
  command -v apt-get >/dev/null 2>&1 || die "apt-get is required for automatic Ubuntu package installation."
}

sudo_apt_update_once() {
  require_sudo
  require_apt_get
  repair_docker_apt_repository_if_configured
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    sudo apt-get update -y
    APT_UPDATED=1
  fi
}

apt_package_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

docker_apt_source_configured() {
  grep -Rqs 'download.docker.com/linux/ubuntu' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null
}

detect_ubuntu_codename() {
  local codename=""

  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  fi

  [[ -n "$codename" ]] || die "Could not determine Ubuntu version codename for Docker repository setup."
  printf '%s\n' "$codename"
}

download_docker_apt_key() {
  local keyring="/etc/apt/keyrings/docker.asc"
  local key_url="https://download.docker.com/linux/ubuntu/gpg"

  sudo install -m 0755 -d /etc/apt/keyrings

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$key_url" | sudo tee "$keyring" >/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$key_url" | sudo tee "$keyring" >/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$key_url" <<'PY' | sudo tee "$keyring" >/dev/null
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=30) as response:
    sys.stdout.buffer.write(response.read())
PY
  else
    die "curl, wget, or python3 is required to download Docker's apt signing key before apt-get update."
  fi

  sudo chmod a+r "$keyring"
}

configure_docker_apt_repository() {
  local codename
  local arch

  require_sudo
  require_apt_get

  codename="$(detect_ubuntu_codename)"
  arch="$(dpkg --print-architecture)"

  sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.gpg
  download_docker_apt_key

  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  DOCKER_APT_REPAIR_CHECKED=1
}

repair_docker_apt_repository_if_configured() {
  if [[ "$DOCKER_APT_REPAIR_CHECKED" -eq 1 ]]; then
    return 0
  fi

  if docker_apt_source_configured; then
    echo "Refreshing Docker apt signing key and source configuration ..."
    configure_docker_apt_repository
  fi

  DOCKER_APT_REPAIR_CHECKED=1
}

install_ubuntu_packages() {
  local missing=()
  local package

  require_sudo
  require_apt_get

  for package in "$@"; do
    if ! apt_package_installed "$package"; then
      missing+=("$package")
    fi
  done

  if [[ "${#missing[@]}" -eq 0 ]]; then
    return 0
  fi

  repair_docker_apt_repository_if_configured
  sudo_apt_update_once
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
}

install_docker_apt_repository() {
  configure_docker_apt_repository
  APT_UPDATED=0
  sudo_apt_update_once
}

reinstall_docker_stack_packages() {
  install_docker_apt_repository
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall "${DOCKER_PACKAGES[@]}"
}

remove_conflicting_docker_packages() {
  local installed=()
  local package

  for package in "${CONFLICTING_DOCKER_PACKAGES[@]}"; do
    if apt_package_installed "$package"; then
      installed+=("$package")
    fi
  done

  if [[ "${#installed[@]}" -gt 0 ]]; then
    echo "Removing conflicting Ubuntu Docker packages: ${installed[*]}"
    sudo DEBIAN_FRONTEND=noninteractive apt-get remove -y "${installed[@]}"
  fi
}

install_docker_stack() {
  require_sudo
  require_apt_get
  install_ubuntu_packages ca-certificates curl gnupg lsb-release
  remove_conflicting_docker_packages

  reinstall_docker_stack_packages

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now docker || true
  elif command -v service >/dev/null 2>&1; then
    sudo service docker start || true
  fi

  sudo usermod -aG docker "$USER" || true
}

ensure_required_system_packages() {
  install_ubuntu_packages $(for p in git unzip tar gzip nano ufw postgresql postgresql-contrib postgresql-client python3 python3-venv python3-pip build-essential openssl ca-certificates curl gnupg lsb-release; do dpkg -s "$p" >/dev/null 2>&1 || printf "%s " "$p"; done)
  install_docker_stack
}

repair_path_permission() {
  local path="$1"
  require_sudo
  echo "Repairing permissions for $path ..."
  sudo chown -R "$(id -u):$(id -g)" "$path"
  sudo chmod -R u+rwX "$path"
}

ensure_writable_directory() {
  local path="$1"
  if mkdir -p "$path" 2>/dev/null && [[ -d "$path" && -w "$path" ]]; then
    return 0
  fi

  require_sudo
  sudo mkdir -p "$path"
  repair_path_permission "$path"

  [[ -d "$path" && -w "$path" ]] || die "Directory is still not writable after repair: $path"
}

ensure_writable_file() {
  local path="$1"
  local parent
  parent="$(dirname "$path")"
  ensure_writable_directory "$parent"

  if [[ -e "$path" && ! -w "$path" ]]; then
    repair_path_permission "$path"
  fi

  if [[ ! -e "$path" ]]; then
    touch "$path" 2>/dev/null || {
      require_sudo
      sudo touch "$path"
      repair_path_permission "$path"
    }
  fi

  [[ -f "$path" && -w "$path" ]] || die "File is still not writable after repair: $path"
}

ensure_user_editable_file() {
  local path="$1"
  local template="${2:-}"
  local parent
  parent="$(dirname "$path")"
  ensure_writable_directory "$parent"

  if [[ ! -f "$path" ]]; then
    if [[ -n "$template" && -f "$template" ]]; then
      echo "Creating $(basename "$path") from $(basename "$template")."
      cp "$template" "$path" 2>/dev/null || {
        require_sudo
        sudo cp "$template" "$path"
      }
    else
      die "$path does not exist and template was not found: ${template:-<none>}"
    fi
  fi

  if [[ ! -O "$path" || ! -w "$path" ]]; then
    repair_path_permission "$path"
  fi

  chmod u+rw "$path" 2>/dev/null || {
    require_sudo
    sudo chmod u+rw "$path"
    repair_path_permission "$path"
  }

  [[ -O "$path" && -w "$path" ]] || die "$path is still not owned and writable by $(id -un)."
}

ensure_readable_file() {
  local path="$1"
  if [[ -r "$path" ]]; then
    return 0
  fi
  repair_path_permission "$path"
  [[ -r "$path" ]] || die "File is still not readable after repair: $path"
}

configure_docker_command() {
  if docker compose version >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return 0
  fi

  if command -v sudo >/dev/null 2>&1 && sudo docker compose version >/dev/null 2>&1 && sudo docker buildx version >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    echo "Using sudo for Docker commands because the current user cannot access Docker directly."
    DOCKER_CMD=(sudo docker)
    return 0
  fi

  die "Docker Engine, Docker Compose plugin, Docker Buildx plugin, and Docker daemon access are required."
}

remove_stale_container_name() {
  local name="$1"
  local container_id
  container_id="$(${DOCKER_CMD[@]} ps -aq --filter "name=^/${name}$" | head -n 1)"
  if [[ -z "$container_id" ]]; then
    return 0
  fi

  echo "Removing stale Docker container name conflict: $name ($container_id)"
  "${DOCKER_CMD[@]}" rm -f "$container_id" >/dev/null
}

remove_stale_deploy_containers() {
  remove_stale_container_name form14_web
  remove_stale_container_name form14_db
}


configure_host_routes() {
  if [[ "$MODE" != "postgres" ]]; then
    return 0
  fi

  ensure_readable_file "$APP_DIR/scripts/configure_host_routes.sh"
  echo "Applying Ubuntu host routes from current network detection and .env.production ..."
  "${DOCKER_CMD[@]}" compose \
    --env-file "$ENV_FILE" \
    -f docker-compose.prod.postgres.yml \
    --profile route-setup \
    run --rm host-route-setup
}

read_env_file_value() {
  local key="$1"
  local default_value="${2:-}"
  local value=""

  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -F= -v target="$key" '$1 == target {sub(/^[^=]*=/, "", $0); print $0}' "$ENV_FILE" | tail -n1 | sed 's/\r$//')"
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

refresh_runtime_environment() {
  "$PYTHON_BIN" "$APP_DIR/ip_address_change.py" --render-runtime-env --env-file "$ENV_FILE" --no-file-backup

  local allowed_hosts
  local app_host_ip
  allowed_hosts="$(read_env_file_value ALLOWED_HOSTS auto)"
  app_host_ip="$(read_env_file_value PBORA_APP_HOST_IP auto)"

  [[ "$allowed_hosts" == *auto* || "$allowed_hosts" == *"$app_host_ip"* ]] || \
    die "ALLOWED_HOSTS was not refreshed for PBORA_APP_HOST_IP=$app_host_ip."
  echo "Runtime environment refreshed: PBORA_APP_HOST_IP=$app_host_ip ALLOWED_HOSTS=$allowed_hosts"
}

sql_literal() {
  printf '%s' "$1" | sed "s/'/''/g"
}

validate_pg_identifier() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "Unsafe PostgreSQL identifier: $value"
}

ensure_host_postgres() {
  if [[ "$MODE" != "postgres" ]]; then
    return 0
  fi
  case "$DB_HOST" in
    127.0.0.1|localhost)
      ;;
    *)
      echo "Skipping local PostgreSQL setup because DB_HOST=$DB_HOST."
      return 0
      ;;
  esac

  validate_pg_identifier "$DB_NAME"
  validate_pg_identifier "$DB_USER"
  install_ubuntu_packages postgresql postgresql-contrib postgresql-client

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now postgresql
  else
    sudo service postgresql start
  fi

  local db_password_sql
  db_password_sql="$(sql_literal "$DB_PASSWORD")"

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

repair_postgres_access() {
  if [[ "$MODE" != "postgres" ]]; then
    return 0
  fi
  ensure_readable_file "$APP_DIR/scripts/fix_postgres_access.sh"
  sudo ENV_FILE="$ENV_FILE" "$APP_DIR/scripts/fix_postgres_access.sh"
}

open_lan_firewall() {
  local route_cidr
  route_cidr="$(read_env_file_value PBORA_ROUTE_CIDR 10.107.0.0/19)"
  PBORA_ROUTE_CIDR="$route_cidr"
  export PBORA_ROUTE_CIDR

  if ! command -v ufw >/dev/null 2>&1; then
    echo "UFW is not installed; skipping app firewall rule."
    return 0
  fi
  if sudo ufw status 2>/dev/null | grep -q '^Status: active'; then
    sudo ufw allow from "$PBORA_ROUTE_CIDR" to any port "$HOST_PORT" proto tcp
  else
    echo "UFW is not active; app port is not blocked by UFW."
  fi
}

run_app_healthcheck() {
  "$APP_DIR/healthcheck.sh" "$MODE"
}

test_browser_access() {
  local scheme="http"
  if [[ -f "$CERT_CRT" && -f "$CERT_KEY" ]]; then
    scheme="https"
  fi
  local url="$scheme://$APP_IP:$HOST_PORT"
  local output_file
  local http_status
  output_file="$(mktemp "${TMPDIR:-/tmp}/pbora_browser.XXXXXX.html")"

  http_status="$(curl -sS --max-time 20 -H "Host: $APP_IP" -k "$url" -o "$output_file" -w '%{http_code}' || true)"

  if grep -Eiq 'BadHost|Host.*not allowed|Bad token|host not allowed' "$output_file"; then
    die "Browser access test failed: host not allowed for $url. Check ALLOWED_HOSTS and PBORA_APP_HOST_IP in $ENV_FILE."
  fi
  if [[ ! "$http_status" =~ ^[23] ]]; then
    die "Browser access test failed for $url with HTTP status ${http_status:-000}."
  fi
  rm -f "$output_file"
}

github_sqlite_backup_enabled() {
  local value
  value="$(read_env_file_value GITHUB_SQLITE_BACKUP_ENABLED 1 | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    0|false|no|off)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

start_github_sqlite_backup_loop() {
  local backup_script="$APP_DIR/scripts/github_sqlite_backup.sh"
  if ! github_sqlite_backup_enabled; then
    echo "GitHub SQLite backup scheduler disabled by GITHUB_SQLITE_BACKUP_ENABLED."
    return 0
  fi

  if [[ ! -f "$backup_script" ]]; then
    echo "GitHub SQLite backup script not found: $backup_script" >&2
    return 0
  fi

  chmod +x "$backup_script" >/dev/null 2>&1 || true

  echo "Installing GitHub SQLite backup scheduler."
  CONTAINER_NAME="$(read_env_file_value GITHUB_SQLITE_BACKUP_CONTAINER_NAME form14_web)" \
  GIT_REMOTE="$(read_env_file_value GITHUB_SQLITE_BACKUP_REMOTE origin)" \
  GIT_BRANCH="$(read_env_file_value GITHUB_SQLITE_BACKUP_BRANCH main)" \
  BACKUP_PREFIX="$(read_env_file_value GITHUB_SQLITE_BACKUP_PREFIX backup)" \
  BACKUP_INTERVAL_SECONDS="$(read_env_file_value GITHUB_SQLITE_BACKUP_INTERVAL_SECONDS 14400)" \
  BACKUP_CRON_SCHEDULE="$(read_env_file_value GITHUB_SQLITE_BACKUP_CRON_SCHEDULE '0 */4 * * *')" \
  BACKUP_WATCH_CRON_SCHEDULE="$(read_env_file_value GITHUB_SQLITE_BACKUP_WATCH_CRON_SCHEDULE '* * * * *')" \
  GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES="$(read_env_file_value GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES 1)" \
  "$backup_script" --install-cron || {
    echo "GitHub SQLite backup scheduler was not installed. The app deployment remains active." >&2
    return 0
  }

  echo "Running first post-deploy GitHub SQLite backup."
  CONTAINER_NAME="$(read_env_file_value GITHUB_SQLITE_BACKUP_CONTAINER_NAME form14_web)" \
  GIT_REMOTE="$(read_env_file_value GITHUB_SQLITE_BACKUP_REMOTE origin)" \
  GIT_BRANCH="$(read_env_file_value GITHUB_SQLITE_BACKUP_BRANCH main)" \
  BACKUP_PREFIX="$(read_env_file_value GITHUB_SQLITE_BACKUP_PREFIX backup)" \
  GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES="$(read_env_file_value GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES 1)" \
  "$backup_script" --once || \
    echo "First post-deploy GitHub SQLite backup failed. Scheduled backups remain installed." >&2
}

if [[ "$MODE" != "sqlite" && "$MODE" != "postgres" ]]; then
  usage >&2
  exit 2
fi

if [[ ! -f "$SOURCE_SQLITE" ]]; then
  echo "SQLite backup not found: $SOURCE_SQLITE" >&2
  exit 2
fi

prime_sudo
ensure_required_system_packages
ensure_writable_directory "$APP_DIR"
ensure_writable_directory "$APP_DIR/instance"
ensure_writable_directory "$APP_DIR/backups"
ensure_user_editable_file "$ENV_FILE" "$ENV_EXAMPLE_FILE"
ensure_readable_file "$SOURCE_SQLITE"
configure_docker_command

VENV_DIR="${VENV_DIR:-/tmp/pbora-migrate-venv}"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

ensure_python_deps() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required." >&2
    return 1
  fi

  ensure_writable_directory "$(dirname "$VENV_DIR")"
  if [[ -d "$VENV_DIR" && ! -w "$VENV_DIR" ]]; then
    repair_path_permission "$VENV_DIR"
  fi

  if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
  fi

  "$PIP_BIN" install --upgrade pip
  "$PIP_BIN" install -r "$APP_DIR/requirements.txt"
}

ensure_local_certificates() {
  CERT_DIR="$APP_DIR/certs"
  CERT_KEY="$CERT_DIR/server.key"
  CERT_CRT="$CERT_DIR/server.crt"

  if [[ -f "$CERT_KEY" && -f "$CERT_CRT" ]]; then
    return 0
  fi

  if ! command -v openssl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      echo "Installing openssl for certificate generation..."
      sudo apt-get update -y
      sudo apt-get install -y openssl
    else
      echo "openssl is required to generate certificates. Install it manually." >&2
      return 1
    fi
  fi

  ensure_writable_directory "$CERT_DIR"
  chmod 755 "$CERT_DIR"

  TMP_OPENSSL_CONF="$(mktemp)"
  cat >"$TMP_OPENSSL_CONF" <<EOF
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
    -keyout "$CERT_KEY" \
    -out "$CERT_CRT" \
    -config "$TMP_OPENSSL_CONF" \
    -extensions v3_req

  chmod 600 "$CERT_KEY"
  rm -f "$TMP_OPENSSL_CONF"
}

enable_python_deps() {
  ensure_python_deps || exit 1
}

enable_python_deps

refresh_runtime_environment
configure_host_routes

APP_IP="$($PYTHON_BIN - <<'PY'
import ipaddress
import socket
import subprocess
import re


def cmd(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ''

candidates = []
route = cmd(['ip', '-4', 'route', 'get', '1.1.1.1'])
m = re.search(r'\bsrc\s+(\d+\.\d+\.\d+\.\d+)', route)

if m:
    candidates.append(m.group(1))
for item in cmd(['hostname', '-I']).split():
    if item:
        candidates.append(item)
try:
    candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
except Exception:
    pass
for item in candidates:
    try:
        address = ipaddress.ip_address(item)
    except ValueError:
        continue
    if address.version == 4 and not address.is_loopback and address.is_private:
        print(item)
        break
else:
    print('127.0.0.1')
PY
)"

export APP_IP
ensure_local_certificates

export HOST_PORT
DB_HOST="$($PYTHON_BIN - <<'PY'
from pathlib import Path
import os
from dotenv import dotenv_values
env_path = Path('.env.production')
values = dotenv_values(env_path)
print(values.get('DB_HOST') or values.get('POSTGRES_HOST') or '127.0.0.1')
PY
)"
export DB_HOST
DB_PORT="$($PYTHON_BIN - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
values = dotenv_values(Path('.env.production'))
print(values.get('DB_PORT') or values.get('POSTGRES_PORT') or '5432')
PY
)"
export DB_PORT
DB_NAME="$($PYTHON_BIN - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
values = dotenv_values(Path('.env.production'))
print(values.get('DB_NAME') or values.get('POSTGRES_DB') or 'pbora')
PY
)"
export DB_NAME
DB_USER="$($PYTHON_BIN - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
values = dotenv_values(Path('.env.production'))
print(values.get('DB_USER') or values.get('POSTGRES_USER') or 'pbora')
PY
)"
export DB_USER
DB_PASSWORD="$($PYTHON_BIN - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
values = dotenv_values(Path('.env.production'))
print(values.get('DB_PASSWORD') or values.get('POSTGRES_PASSWORD') or 'pbora')
PY
)"
export DB_PASSWORD

VERIFY_LOG="$(mktemp "${TMPDIR:-/tmp}/pbora_verify.XXXXXX.log")"
MIGRATE_LOG="$(mktemp "${TMPDIR:-/tmp}/pbora_migrate.XXXXXX.log")"

ensure_local_certificates
ensure_host_postgres
repair_postgres_access
open_lan_firewall

"$PYTHON_BIN" "$APP_DIR/scripts/verify_pbora_postgres.py" --env-file "$ENV_FILE" --url "postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME" >"$VERIFY_LOG" 2>&1 || {
  echo "PostgreSQL connectivity test failed. See $VERIFY_LOG" >&2
  exit 1
}

"$PYTHON_BIN" "$APP_DIR/scripts/migrate_sqlite_to_postgres.py" --env-file "$ENV_FILE" --source "$SOURCE_SQLITE" --url "postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME" --replace-existing --sync-env-users --reset-user-password field.12345 >"$MIGRATE_LOG" 2>&1 || {
  echo "SQLite import into PostgreSQL failed. See $MIGRATE_LOG" >&2
  exit 1
}

remove_stale_deploy_containers

compose_up_args=(up -d --build)
if [[ "$(printf '%s' "${PBORA_SKIP_DOCKER_BUILD:-0}" | tr '[:upper:]' '[:lower:]')" =~ ^(1|true|yes|on|skip|no-build)$ ]]; then
  echo "Starting containers from existing cached Docker images; skipping app image rebuild."
  compose_up_args=(up -d --no-build)
else
  echo "Building with Docker layer cache and starting containers."
fi

if [[ "$MODE" == "postgres" ]]; then
  "${DOCKER_CMD[@]}" compose --env-file "$ENV_FILE" -f docker-compose.prod.postgres.yml "${compose_up_args[@]}"
else
  "${DOCKER_CMD[@]}" compose --env-file "$ENV_FILE" -f docker-compose.prod.yml "${compose_up_args[@]}"
fi

sleep 5

if [[ "$MODE" == "postgres" ]]; then
  "${DOCKER_CMD[@]}" compose --env-file "$ENV_FILE" -f docker-compose.prod.postgres.yml ps
else
  "${DOCKER_CMD[@]}" compose --env-file "$ENV_FILE" -f docker-compose.prod.yml ps
fi

run_app_healthcheck
test_browser_access
start_github_sqlite_backup_loop

cat <<EOF

Deployment complete.
App HTTP address: http://$APP_IP:$HOST_PORT
App HTTPS address: https://$APP_IP:$HOST_PORT
Certificates: $APP_DIR/certs/server.crt, $APP_DIR/certs/server.key
Database host: $DB_HOST:$DB_PORT
Database name: $DB_NAME
EOF
