#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$APP_DIR/scripts/cloudflare_tunnel_up.sh" postgres both --no-build
