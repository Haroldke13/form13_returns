#!/bin/sh
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.production}"

log_info() {
  printf '[route-setup] %s\n' "$*"
}

die() {
  printf '[route-setup] ERROR: %s\n' "$*" >&2
  exit 1
}

strip_quotes() {
  printf '%s' "$1" | sed -E "s/^['\"](.*)['\"]$/\1/"
}

read_env_value() {
  key="$1"
  default_value="${2:-}"
  current_value="$(eval "printf '%s' \"\${$key-}\"")"
  if [ -n "$current_value" ]; then
    strip_quotes "$current_value"
    return
  fi
  if [ ! -f "$ENV_FILE" ]; then
    printf '%s' "$default_value"
    return
  fi
  file_value="$(awk -F= -v target="$key" '$1 == target {sub(/^[^=]*=/, "", $0); print $0}' "$ENV_FILE" | tail -n1 | sed 's/\r$//')"
  if [ -n "$file_value" ]; then
    strip_quotes "$file_value"
  else
    printf '%s' "$default_value"
  fi
}

is_auto_value() {
  normalized="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    ""|auto|detect|dynamic|\<server-ip\>|\<server_ip\>)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_truthy() {
  normalized="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required on the Ubuntu host."
}

detect_primary_ipv4() {
  route_ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
  if [ -n "$route_ip" ]; then
    printf '%s' "$route_ip"
    return
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

detect_default_gateway() {
  ip -4 route show default 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "via") {print $(i + 1); exit}}'
}

detect_default_interface() {
  ip -4 route show default 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}'
}

interface_has_ipv4() {
  iface="$1"
  app_ip="$2"
  ip -o -4 addr show dev "$iface" 2>/dev/null | awk '{print $4}' | grep -q "^$app_ip/"
}

resolve_network_interface() {
  requested_iface="$1"
  app_ip="$2"
  default_iface="$(detect_default_interface)"

  if ! is_auto_value "$requested_iface" && ip link show dev "$requested_iface" >/dev/null 2>&1; then
    if interface_has_ipv4 "$requested_iface" "$app_ip"; then
      printf '%s' "$requested_iface"
      return
    fi
    if [ -z "$default_iface" ]; then
      printf '%s' "$requested_iface"
      return
    fi
    log_info "Configured interface $requested_iface does not carry $app_ip; using default-route interface $default_iface."
  fi

  if [ -n "$default_iface" ]; then
    printf '%s' "$default_iface"
    return
  fi

  die "Could not detect the Ubuntu network interface. Set PBORA_NETWORK_INTERFACE in $ENV_FILE."
}

derive_route_cidr() {
  app_ip="$1"
  prefix="$2"
  IFS=. 
  set -- $app_ip
  IFS=' '
  if [ "$#" -ne 4 ]; then
    die "Cannot derive route CIDR from invalid PBORA_APP_HOST_IP=$app_ip"
  fi
  printf '%s.%s.%s.0/%s' "$1" "$2" "$3" "$prefix"
}

run_command() {
  if is_truthy "$PBORA_ROUTE_DRY_RUN"; then
    printf '[route-setup] DRY-RUN:'
    for arg in "$@"; do
      printf ' %s' "$arg"
    done
    printf '\n'
    return
  fi
  "$@"
}

require_command ip
require_command awk
require_command sed
require_command grep

PBORA_APP_HOST_IP="$(read_env_value PBORA_APP_HOST_IP auto)"
PBORA_GATEWAY="$(read_env_value PBORA_GATEWAY auto)"
PBORA_NETWORK_INTERFACE="$(read_env_value PBORA_NETWORK_INTERFACE auto)"
PBORA_ROUTE_PREFIX="$(read_env_value PBORA_ROUTE_PREFIX 24)"
PBORA_ROUTE_CIDR="$(read_env_value PBORA_ROUTE_CIDR auto)"
PBORA_ASSIGN_HOST_IP="$(read_env_value PBORA_ASSIGN_HOST_IP 0)"
PBORA_REPLACE_LAN_ROUTE="$(read_env_value PBORA_REPLACE_LAN_ROUTE 1)"
PBORA_REPLACE_DEFAULT_ROUTE="$(read_env_value PBORA_REPLACE_DEFAULT_ROUTE 1)"
PBORA_ROUTE_DRY_RUN="$(read_env_value PBORA_ROUTE_DRY_RUN 0)"

if is_auto_value "$PBORA_APP_HOST_IP"; then
  PBORA_APP_HOST_IP="$(detect_primary_ipv4)"
fi
[ -n "$PBORA_APP_HOST_IP" ] || die "Could not detect PBORA_APP_HOST_IP."

if is_auto_value "$PBORA_GATEWAY"; then
  PBORA_GATEWAY="$(detect_default_gateway)"
fi

PBORA_NETWORK_INTERFACE="$(resolve_network_interface "$PBORA_NETWORK_INTERFACE" "$PBORA_APP_HOST_IP")"

if is_auto_value "$PBORA_ROUTE_CIDR"; then
  PBORA_ROUTE_CIDR="$(derive_route_cidr "$PBORA_APP_HOST_IP" "$PBORA_ROUTE_PREFIX")"
fi

if ! is_truthy "$PBORA_ROUTE_DRY_RUN" && [ "$(id -u)" -ne 0 ]; then
  die "Route changes require root. Run with sudo, or set PBORA_ROUTE_DRY_RUN=1 to preview commands."
fi

log_info "Ubuntu interface: $PBORA_NETWORK_INTERFACE"
log_info "Ubuntu app IP: $PBORA_APP_HOST_IP"
log_info "LAN route CIDR: $PBORA_ROUTE_CIDR"
log_info "Gateway: ${PBORA_GATEWAY:-not configured}"

if is_truthy "$PBORA_ASSIGN_HOST_IP"; then
  run_command ip addr replace "$PBORA_APP_HOST_IP/$PBORA_ROUTE_PREFIX" dev "$PBORA_NETWORK_INTERFACE"
fi

if is_truthy "$PBORA_REPLACE_LAN_ROUTE"; then
  run_command ip route replace "$PBORA_ROUTE_CIDR" dev "$PBORA_NETWORK_INTERFACE" src "$PBORA_APP_HOST_IP"
fi

if is_truthy "$PBORA_REPLACE_DEFAULT_ROUTE"; then
  [ -n "$PBORA_GATEWAY" ] || die "PBORA_GATEWAY is required when PBORA_REPLACE_DEFAULT_ROUTE=1."
  run_command ip route replace default via "$PBORA_GATEWAY" dev "$PBORA_NETWORK_INTERFACE"
fi

log_info "Route setup complete."
ip route show
