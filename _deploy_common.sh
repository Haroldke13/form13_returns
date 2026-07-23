#!/usr/bin/env bash

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.production}"
ENV_EXAMPLE_FILE="${ENV_EXAMPLE_FILE:-$APP_DIR/.env.production.example}"

timestamp_now() {
  date '+%F %T'
}

log_info() {
  printf '[%s] %s\n' "$(timestamp_now)" "$*"
}

log_warn() {
  printf '[%s] WARNING: %s\n' "$(timestamp_now)" "$*" >&2
}

die() {
  printf '[%s] ERROR: %s\n' "$(timestamp_now)" "$*" >&2
  exit 1
}

ensure_file_exists() {
  local path="$1"
  [[ -f "$path" ]] || die "Required file not found: $path"
}

resolve_mode() {
  local mode="${1:-sqlite}"
  case "$mode" in
    sqlite)
      printf '%s' "sqlite"
      ;;
    postgres|postgresql|pg)
      printf '%s' "postgres"
      ;;
    *)
      return 1
      ;;
  esac
}

compose_file_for_mode() {
  local mode="$1"
  case "$mode" in
    sqlite)
      printf '%s' "docker-compose.prod.yml"
      ;;
    postgres)
      printf '%s' "docker-compose.prod.postgres.yml"
      ;;
    *)
      return 1
      ;;
  esac
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    sudo docker "$@"
  fi
}

docker_compose_cmd() {
  if docker info >/dev/null 2>&1; then
    docker compose "$@"
  else
    sudo docker compose "$@"
  fi
}

run_docker() {
  docker_cmd "$@"
}

run_docker_compose() {
  docker_compose_cmd "$@"
}

skip_docker_build_enabled() {
  local value
  value="$(printf '%s' "${PBORA_SKIP_DOCKER_BUILD:-${SKIP_DOCKER_BUILD:-0}}" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on|skip|no-build)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

compose_up_detached() {
  local compose_file="$1"
  if skip_docker_build_enabled; then
    log_info "Starting containers from existing cached Docker images; skipping app image rebuild."
    docker_compose_cmd --env-file "$ENV_FILE" -f "$compose_file" up -d --no-build
  else
    log_info "Building with Docker layer cache and starting containers."
    docker_compose_cmd --env-file "$ENV_FILE" -f "$compose_file" up -d --build
  fi
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker is not installed or not on PATH."
  docker_compose_cmd version >/dev/null 2>&1 || die "Docker Compose plugin is not available."
}

require_env_file() {
  [[ -f "$ENV_FILE" ]] || die ".env.production not found. Create it first from .env.production.example."
}

read_env_value() {
  local key="$1"
  local default_value="${2:-}"
  if [[ ! -f "$ENV_FILE" ]]; then
    printf '%s' "$default_value"
    return
  fi
  local value
  value="$(awk -F= -v target="$key" '$1 == target {sub(/^[^=]*=/, "", $0); print $0}' "$ENV_FILE" | tail -n1)"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$default_value"
  fi
}

is_auto_env_value() {
  local value
  value="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    ""|auto|detect|dynamic|\<server-ip\>|\<server_ip\>)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

host_port() {
  read_env_value HOST_PORT 8000
}

external_postgres_enabled() {
  local value
  value="$(read_env_value EXTERNAL_POSTGRES 0 | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

public_base_url() {
  local configured_url server_host
  configured_url="$(read_env_value PUBLIC_BASE_URL)"
  if [[ -n "$configured_url" ]] && ! is_auto_env_value "$configured_url" && [[ "$configured_url" != *"<server-ip>"* ]]; then
    printf '%s' "${configured_url%/}"
    return
  fi

  server_host="$(server_ip)"
  if [[ -n "$server_host" ]]; then
    printf 'http://%s:%s' "$server_host" "$(host_port)"
    return
  fi

  printf 'http://127.0.0.1:%s' "$(host_port)"
}

server_ip() {
  local routed_ip hostname_ip
  routed_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
  if [[ -n "$routed_ip" ]]; then
    printf '%s' "$routed_ip"
    return
  fi
  hostname_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '%s' "$hostname_ip"
}

runtime_env_auto_update_enabled() {
  local value
  value="$(read_env_value PBORA_RUNTIME_ENV_AUTO_UPDATE 1 | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    0|false|no|off)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

render_runtime_env_if_enabled() {
  require_env_file
  runtime_env_auto_update_enabled || {
    log_warn "PBORA_RUNTIME_ENV_AUTO_UPDATE is disabled; using existing environment file values."
    return 0
  }

  command -v python3 >/dev/null 2>&1 || die "python3 is required to refresh dynamic PBORA runtime environment values."
  [[ -f "$APP_DIR/ip_address_change.py" ]] || die "Runtime environment renderer not found: $APP_DIR/ip_address_change.py"

  log_info "Refreshing runtime environment values from the current server network."
  python3 "$APP_DIR/ip_address_change.py" --render-runtime-env --env-file "$ENV_FILE" --no-file-backup
}

validate_compose() {
  local compose_file="$1"
  ensure_file_exists "$APP_DIR/$compose_file"
  require_env_file
  docker_compose_cmd --env-file "$ENV_FILE" -f "$compose_file" config >/dev/null
}

warn_if_placeholder_env() {
  [[ -f "$ENV_FILE" ]] || return 0

  local secret_key admin_email admin_password pg_password
  secret_key="$(read_env_value SECRET_KEY)"
  admin_email="$(read_env_value ADMIN_USER_EMAIL)"
  admin_password="$(read_env_value ADMIN_USER_PASSWORD)"
  pg_password="$(read_env_value POSTGRES_PASSWORD)"

  [[ "$secret_key" == "change-this-to-a-long-random-secret" ]] && log_warn "SECRET_KEY still uses the example placeholder."
  [[ "$admin_email" == "admin@example.local" ]] && log_warn "ADMIN_USER_EMAIL still uses the example placeholder."
  [[ "$admin_password" == "change-this-admin-password" ]] && log_warn "ADMIN_USER_PASSWORD still uses the example placeholder."
  [[ "$pg_password" == "change-this-db-password" ]] && log_warn "POSTGRES_PASSWORD still uses the example placeholder."
  return 0
}

wait_for_http() {
  local url="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsSk -o /dev/null "$url"; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  return 1
}

wait_for_postgres_container() {
  local db_name="$1"
  local db_user="$2"
  local attempts="${3:-30}"
  local sleep_seconds="${4:-2}"
  for _ in $(seq 1 "$attempts"); do
    if docker_cmd ps --format '{{.Names}}' | grep -qx 'form14_db' && \
      docker_cmd exec form14_db pg_isready -U "$db_user" -d "$db_name" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  return 1
}

postgres_connection_url() {
  local configured_url db_host db_port db_name db_user db_password sslmode
  configured_url="$(read_env_value INTERNAL_DATABASE_URL)"
  if [[ -n "$configured_url" ]] && ! is_auto_env_value "$configured_url"; then
    printf '%s' "$configured_url"
    return
  fi

  db_host="$(read_env_value DB_HOST 127.0.0.1)"
  db_port="$(read_env_value DB_PORT 5432)"
  db_name="$(read_env_value DB_NAME pbora)"
  db_user="$(read_env_value DB_USER pbora)"
  db_password="$(read_env_value DB_PASSWORD pbora)"
  sslmode="$(read_env_value DB_SSL_MODE disable)"
  printf 'postgresql://%s:%s@%s:%s/%s?sslmode=%s' "$db_user" "$db_password" "$db_host" "$db_port" "$db_name" "$sslmode"
}

check_external_postgres() {
  local db_host db_port db_name db_user
  db_host="$(read_env_value DB_HOST 127.0.0.1)"
  db_port="$(read_env_value DB_PORT 5432)"
  db_name="$(read_env_value DB_NAME pbora)"
  db_user="$(read_env_value DB_USER pbora)"

  if command -v pg_isready >/dev/null 2>&1; then
    PGPASSWORD="$(read_env_value DB_PASSWORD pbora)" pg_isready -h "$db_host" -p "$db_port" -U "$db_user" -d "$db_name" || return 1
  fi

  if docker_compose_cmd --env-file "$ENV_FILE" -f "$(compose_file_for_mode postgres)" ps --services --filter status=running | grep -qx 'web'; then
    docker_compose_cmd --env-file "$ENV_FILE" -f "$(compose_file_for_mode postgres)" exec -T web \
      python -c "from sqlalchemy import create_engine, text; import os; e=create_engine(os.environ['INTERNAL_DATABASE_URL'], pool_pre_ping=True); c=e.connect(); print(c.execute(text('select current_database(), current_user')).fetchone()); c.close()"
  else
    log_warn "web container is not running; skipped in-container PostgreSQL check."
  fi
}

github_sqlite_backup_enabled() {
  local value
  value="$(read_env_value GITHUB_SQLITE_BACKUP_ENABLED 1 | tr '[:upper:]' '[:lower:]')"
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
    log_info "GitHub SQLite backup loop disabled by GITHUB_SQLITE_BACKUP_ENABLED."
    return 0
  fi

  if [[ ! -f "$backup_script" ]]; then
    log_warn "GitHub SQLite backup script not found: $backup_script"
    return 0
  fi

  chmod +x "$backup_script" >/dev/null 2>&1 || true

  log_info "Starting GitHub SQLite backup scheduler in the background."
  CONTAINER_NAME="$(read_env_value GITHUB_SQLITE_BACKUP_CONTAINER_NAME form14_web)" \
  GIT_REMOTE="$(read_env_value GITHUB_SQLITE_BACKUP_REMOTE origin)" \
  GIT_BRANCH="$(read_env_value GITHUB_SQLITE_BACKUP_BRANCH main)" \
  BACKUP_PREFIX="$(read_env_value GITHUB_SQLITE_BACKUP_PREFIX backup)" \
  BACKUP_INTERVAL_SECONDS="$(read_env_value GITHUB_SQLITE_BACKUP_INTERVAL_SECONDS 14400)" \
  BACKUP_CRON_SCHEDULE="$(read_env_value GITHUB_SQLITE_BACKUP_CRON_SCHEDULE '0 */4 * * *')" \
  "$backup_script" --start-background || \
    log_warn "GitHub SQLite backup scheduler did not start. The app deployment remains active."
}
