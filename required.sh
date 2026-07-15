#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"
RAW_MODE="postgres"
APT_UPDATED=0

usage() {
  cat <<'EOF'
Usage:
  ./required.sh [sqlite|postgres] [--no-build]

Examples:
  ./required.sh
  ./required.sh postgres
  ./required.sh sqlite
  ./required.sh postgres --no-build

What it does:
  - Installs Ubuntu/Debian host prerequisites if missing
  - Installs Docker Engine and Docker Compose plugin if missing
  - Enables and starts Docker
  - Adds the current user to the docker group if needed
  - Prepares app directories and script permissions
  - Copies `.env.production.example` to `.env.production` if missing
  - Deploys the app automatically once `.env.production` exists

Important:
  - If `.env.production` does not exist yet, the script creates it and stops
    so you can edit real production values before continuing.
  - By default Docker uses cached image layers while building the app image.
  - Use --no-build or PBORA_SKIP_DOCKER_BUILD=1 to reuse the existing app image.
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

if [[ ! -d "$APP_DIR" ]]; then
  die "Application directory not found: $APP_DIR"
fi

if ! command -v apt-get >/dev/null 2>&1; then
  die "This script currently supports Ubuntu/Debian VMs that provide apt-get."
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  if ! command -v sudo >/dev/null 2>&1; then
    die "sudo is required when not running as root."
  fi
  SUDO="sudo"
fi

run_docker() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    $SUDO docker "$@"
  fi
}

run_docker_compose() {
  if docker info >/dev/null 2>&1; then
    docker compose "$@"
  else
    $SUDO docker compose "$@"
  fi
}

apt_update_once() {
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    echo "Updating apt package index..."
    $SUDO apt-get update
    APT_UPDATED=1
  fi
}

ensure_package() {
  local package="$1"
  if dpkg -s "$package" >/dev/null 2>&1; then
    return
  fi
  apt_update_once
  echo "Installing package: $package"
  $SUDO apt-get install -y "$package"
}

ensure_docker_repository() {
  local repo_file="/etc/apt/sources.list.d/docker.list"
  local keyring="/etc/apt/keyrings/docker.gpg"

  ensure_package ca-certificates
  ensure_package curl
  ensure_package gnupg
  ensure_package lsb-release

  if [[ ! -f "$keyring" ]]; then
    echo "Adding Docker apt repository key..."
    $SUDO install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o "$keyring"
    $SUDO chmod a+r "$keyring"
  fi

  if [[ ! -f "$repo_file" ]]; then
    echo "Adding Docker apt repository..."
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=$keyring] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      $SUDO tee "$repo_file" >/dev/null
  fi

  apt_update_once
}

ensure_docker_engine() {
  local missing=0

  if ! command -v docker >/dev/null 2>&1; then
    missing=1
  elif ! docker compose version >/dev/null 2>&1; then
    missing=1
  fi

  if [[ "$missing" -eq 1 ]]; then
    ensure_docker_repository
    echo "Installing Docker Engine and Docker Compose plugin..."
    $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  echo "Ensuring Docker service is enabled and running..."
  $SUDO systemctl enable docker
  $SUDO systemctl start docker
}

ensure_support_packages() {
  for package in git unzip tar gzip nano ufw postgresql-client; do
    ensure_package "$package"
  done
}

ensure_docker_group_membership() {
  local user_name
  user_name="$(id -un)"
  if ! getent group docker >/dev/null 2>&1; then
    $SUDO groupadd docker
  fi
  if ! id -nG "$user_name" | tr ' ' '\n' | grep -qx docker; then
    echo "Adding $user_name to the docker group..."
    $SUDO usermod -aG docker "$user_name"
    echo "Note: future shells will pick up docker-group access automatically after re-login."
  fi
}

ensure_app_layout() {
  cd "$APP_DIR"
  mkdir -p instance/uploads backups
  chmod +x deploy.sh update.sh backup.sh restore.sh healthcheck.sh required.sh
}

ensure_env_file() {
  cd "$APP_DIR"
  if [[ ! -f .env.production ]]; then
    cp .env.production.example .env.production
    log_info "Created .env.production from .env.production.example"
    log_warn "Edit .env.production with real production values, then rerun ./required.sh $MODE"
    exit 1
  fi
}

prepull_runtime_images() {
  echo "Preparing Docker images..."
  if [[ "$MODE" == "postgres" ]] && ! external_postgres_enabled; then
    run_docker pull postgres:16
  fi
}

read_env_value() {
  local key="$1"
  local default_value="${2:-}"
  local value
  value="$(awk -F= -v target="$key" '$1 == target {sub(/^[^=]*=/, "", $0); print $0}' "$APP_DIR/.env.production" | tail -n1)"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$default_value"
  fi
}

deploy_application() {
  local host_port server_ip
  log_info "Validating compose configuration..."
  validate_compose "$COMPOSE_FILE"

  log_info "Starting application using $COMPOSE_FILE..."
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
  run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" exec -T web flask init-db

  log_info "Running healthcheck..."
  if [[ -x "$APP_DIR/healthcheck.sh" ]]; then
    "$APP_DIR/healthcheck.sh" "$MODE"
  fi

  host_port="$(host_port)"
  server_ip="$(server_ip)"

  echo
  log_info "Environment setup and deployment complete."
  run_docker_compose --env-file .env.production -f "$COMPOSE_FILE" ps
  if [[ -n "${server_ip:-}" ]]; then
    echo "Open from another LAN computer: http://${server_ip}:${host_port}"
  else
    echo "Open from another LAN computer: http://<server-ip>:${host_port}"
  fi
}

cd "$APP_DIR"

log_info "Preparing host requirements for $MODE deployment..."
ensure_support_packages
ensure_docker_engine
ensure_docker_group_membership
ensure_app_layout
ensure_env_file
warn_if_placeholder_env
prepull_runtime_images
deploy_application
