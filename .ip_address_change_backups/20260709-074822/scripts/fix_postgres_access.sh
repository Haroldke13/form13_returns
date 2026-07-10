#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.production}"
source "$APP_DIR/_deploy_common.sh"

usage() {
  cat <<'EOF'
Usage:
  sudo scripts/fix_postgres_access.sh [--dry-run]

What it fixes on the Ubuntu/PostgreSQL host:
  - PostgreSQL listen_addresses so the LAN/app container can reach it
  - PostgreSQL port from .env.production
  - pg_hba.conf LAN access for the configured DB/user
  - UFW rule for the configured DB port when UFW is active
  - PostgreSQL restart and connection verification

Run this on the server that actually hosts PostgreSQL.
EOF
}

DRY_RUN=0
case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
  --dry-run)
    DRY_RUN=1
    ;;
  "")
    ;;
  *)
    usage >&2
    die "Unknown option: $1"
    ;;
esac

require_env_file

DB_HOST="$(read_env_value DB_HOST 10.107.20.230)"
DB_PORT="$(read_env_value DB_PORT 5432)"
DB_NAME="$(read_env_value DB_NAME pbora)"
DB_USER="$(read_env_value DB_USER pbora)"
DB_PASSWORD="$(read_env_value DB_PASSWORD pbora)"
LAN_CIDR="$(read_env_value POSTGRES_ALLOWED_CIDR)"
EXTRA_ALLOWED_CIDRS="$(read_env_value POSTGRES_EXTRA_ALLOWED_CIDRS)"

if [[ -z "$LAN_CIDR" ]]; then
  IFS=. read -r ip1 ip2 ip3 _ <<<"$DB_HOST"
  LAN_CIDR="${ip1}.${ip2}.${ip3}.0/24"
fi

ALLOWED_CIDRS=("$LAN_CIDR")

if [[ -n "$EXTRA_ALLOWED_CIDRS" ]]; then
  IFS=',' read -r -a EXTRA_CIDR_ITEMS <<<"$EXTRA_ALLOWED_CIDRS"
  for cidr in "${EXTRA_CIDR_ITEMS[@]}"; do
    cidr="${cidr//[[:space:]]/}"
    [[ -n "$cidr" ]] && ALLOWED_CIDRS+=("$cidr")
  done
fi

if command -v docker >/dev/null 2>&1; then
  while IFS= read -r cidr; do
    [[ -n "$cidr" ]] && ALLOWED_CIDRS+=("$cidr")
  done < <(docker_cmd network inspect $(docker_cmd network ls -q 2>/dev/null) --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null | sort -u)
fi

if [[ ! "$DB_PORT" =~ ^[0-9]+$ ]] || (( DB_PORT < 1 || DB_PORT > 65535 )); then
  die "Invalid DB_PORT in $ENV_FILE: $DB_PORT"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_or_print() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
    return
  fi
  "$@"
}

run_root() {
  if [[ -n "$SUDO" ]]; then
    run_or_print "$SUDO" "$@"
  else
    run_or_print "$@"
  fi
}

backup_file() {
  local path="$1"
  local stamp
  stamp="$(date '+%Y%m%d-%H%M%S')"
  run_root cp "$path" "$path.pbora-bak-$stamp"
}

set_postgres_conf_value() {
  local path="$1"
  local key="$2"
  local value="$3"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: set $key = $value in $path"
    return
  fi
  if $SUDO grep -Eq "^[#[:space:]]*$key[[:space:]]*=" "$path"; then
    $SUDO sed -i -E "s|^[#[:space:]]*$key[[:space:]]*=.*|$key = $value|" "$path"
  else
    printf '\n%s = %s\n' "$key" "$value" | $SUDO tee -a "$path" >/dev/null
  fi
}

append_pg_hba_rule() {
  local path="$1"
  local cidr rule
  for cidr in "${ALLOWED_CIDRS[@]}"; do
    rule="host    $DB_NAME    $DB_USER    $cidr    scram-sha-256"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "DRY-RUN: ensure pg_hba rule in $path: $rule"
      continue
    fi
    if ! $SUDO grep -Fqx "$rule" "$path"; then
      printf '\n# PBORA Form 13 LAN/app-container access\n%s\n' "$rule" | $SUDO tee -a "$path" >/dev/null
    fi
  done
}

mapfile -t POSTGRES_CONFIGS < <(find /etc/postgresql -path '*/main/postgresql.conf' -type f 2>/dev/null | sort)
mapfile -t PG_HBA_CONFIGS < <(find /etc/postgresql -path '*/main/pg_hba.conf' -type f 2>/dev/null | sort)

if [[ "${#POSTGRES_CONFIGS[@]}" -eq 0 || "${#PG_HBA_CONFIGS[@]}" -eq 0 ]]; then
  die "Could not find /etc/postgresql/*/main PostgreSQL config files. Install/configure PostgreSQL first."
fi

log_info "Repairing PostgreSQL access for $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
log_info "Allowing CIDR(s): ${ALLOWED_CIDRS[*]}"

for config in "${POSTGRES_CONFIGS[@]}"; do
  log_info "Updating $config"
  backup_file "$config"
  set_postgres_conf_value "$config" "listen_addresses" "'*'"
  set_postgres_conf_value "$config" "port" "$DB_PORT"
done

for hba in "${PG_HBA_CONFIGS[@]}"; do
  log_info "Updating $hba"
  backup_file "$hba"
  append_pg_hba_rule "$hba"
done

if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q '^Status: active'; then
  log_info "Opening UFW for PostgreSQL to port $DB_PORT"
  for cidr in "${ALLOWED_CIDRS[@]}"; do
    run_root ufw allow from "$cidr" to any port "$DB_PORT" proto tcp
  done
else
  log_warn "UFW is not active or unavailable; skipped firewall rule."
fi

log_info "Restarting PostgreSQL"
if command -v systemctl >/dev/null 2>&1; then
  run_root systemctl restart postgresql
else
  run_root service postgresql restart
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_info "Dry run complete; no changes were made."
  exit 0
fi

log_info "Verifying PostgreSQL accepts TCP connections"
PGPASSWORD="$DB_PASSWORD" pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -c "select current_database(), current_user, inet_server_addr(), inet_server_port();"

log_info "PostgreSQL access repair complete. Restart the app with: ./update.sh postgres"
