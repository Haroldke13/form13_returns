#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/_deploy_common.sh"

RAW_MODE="postgres"
SKIP_PULL=0
UPDATE_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  ./update_one_command_deploy.sh [sqlite|postgres] [--no-pull] [--no-build]

Examples:
  ./update_one_command_deploy.sh
  ./update_one_command_deploy.sh postgres
  ./update_one_command_deploy.sh sqlite --no-pull
  ./update_one_command_deploy.sh postgres --no-pull --no-build

What this does:
  1. Pulls the latest committed GitHub update for the current branch.
  2. Rebuilds/restarts the existing Docker deployment through update.sh.
  3. Runs a one-time schema sync and return_date backfill.
  4. Verifies pbo_reports.return_date exists and has no missing values.

Notes:
  - By default Docker uses the layer cache while rebuilding the app image.
  - Use --no-build or PBORA_SKIP_DOCKER_BUILD=1 to reuse the existing app image.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|help)
      usage
      exit 0
      ;;
    --no-pull)
      SKIP_PULL=1
      shift
      ;;
    --no-build|--skip-build|--cached-image)
      UPDATE_ARGS+=("--no-build")
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

cd "$APP_DIR"

if [[ "$SKIP_PULL" -eq 0 ]]; then
  command -v git >/dev/null 2>&1 || die "git is required to pull the GitHub update."
  if [[ -d "$APP_DIR/.git" ]]; then
    current_branch="$(git branch --show-current)"
    [[ -n "$current_branch" ]] || die "Cannot determine current git branch."
    log_info "Pulling latest GitHub update for branch: $current_branch"
    git fetch origin "$current_branch"
    git pull --ff-only origin "$current_branch"
  else
    log_warn "No .git directory found; skipping GitHub pull."
  fi
else
  log_info "Skipping GitHub pull because --no-pull was supplied."
fi

log_info "Running existing Docker update flow for $MODE deployment."
"$APP_DIR/update.sh" "$MODE" "${UPDATE_ARGS[@]}"

log_info "Running one-time schema sync and return_date backfill."
docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T web flask sync-db-schema

docker_compose_cmd --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T web python - <<'PY'
from sqlalchemy import inspect, text

from app import app, backfill_missing_return_dates, db

with app.app_context():
    backfilled = backfill_missing_return_dates()
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns("pbo_reports")}
    if "return_date" not in columns:
        raise SystemExit("pbo_reports.return_date was not created")

    missing = db.session.execute(
        text(
            "SELECT COUNT(*) FROM pbo_reports "
            "WHERE return_date IS NULL OR TRIM(CAST(return_date AS TEXT)) = ''"
        )
    ).scalar_one()
    total = db.session.execute(text("SELECT COUNT(*) FROM pbo_reports")).scalar_one()
    placeholder_rows = db.session.execute(
        text("SELECT COUNT(*) FROM pbo_reports WHERE return_date = :placeholder"),
        {"placeholder": "9999-09-09"},
    ).scalar_one()

    if missing:
        raise SystemExit(f"return_date backfill incomplete: missing_rows={missing}")

    print(
        "return_date_ok "
        f"total_reports={total} "
        f"placeholder_rows={placeholder_rows} "
        f"newly_backfilled={backfilled}"
    )
PY

log_info "Running final healthcheck."
"$APP_DIR/healthcheck.sh" "$MODE"

echo
log_info "One-time deployment update complete."
