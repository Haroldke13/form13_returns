# Git Differences Documentation - 2026-07-13

This document records the current git differences in `/home/pbora/form13_returns`
on 2026-07-13 and explains their expected impact on the application.

Sensitive runtime values are intentionally summarized instead of copied here.
Do not paste Cloudflare tunnel tokens, private keys, database passwords, API
keys, or OAuth client secrets into this file.

## Summary

- Primary change: add Cloudflare Tunnel support for `returnsform14.org` and
  `research.harolditdata.uk`.
- Follow-up fix on 2026-07-16: activated the `dummy tunnel` connector for
  `research.harolditdata.uk` and added local token-file based Docker sidecars
  for both Cloudflare tunnels.
- Production domain alignment: move visible public app settings from older
  `returnsform13.org`, Render, or LAN-only defaults toward
  `https://returnsform14.org`.
- Deployment reliability changes: make deployment warnings non-fatal, print the
  actual healthcheck URL, preserve explicit public domains during runtime env
  refresh, and clean up database/session state during SQLite-to-PostgreSQL
  migration.
- New runtime artifacts are present: self-signed TLS cert files and two backup
  export directories.

## Current Git Status

Tracked modified files:

- `.env.production`
- `.env.production.example`
- `README.md`
- `_deploy_common.sh`
- `healthcheck.sh`
- `ip_address_change.py`
- `scripts/migrate_sqlite_to_postgres.py`
- `test_ip_address_change.py`

Untracked files and directories:

- `.env.cloudflare.example`
- `.gitignore`
- `docker-compose.cloudflare.yml`
- `docs/CLOUDFLARE_TUNNEL.md`
- `scripts/cloudflare_tunnel_up.sh`
- `certs/server.crt`
- `certs/server.key`
- `backups/Monday, 13--07--2026, 09-00-51/`
- `backups/Monday, 13--07--2026, 09-00-52/`
- `documentation.md` itself, after this file was created.

## File-by-File Impact

### `.env.production`

Changed production runtime settings to better match the current LAN server and
the intended public Cloudflare domain.

Important changes:

- `PUBLIC_BASE_URL` now points to `https://returnsform14.org`.
- `PREFERRED_URL_SCHEME` is set to `https`.
- `PUBLIC_HOST_PORT` is set to `443`.
- `SESSION_COOKIE_SECURE` is enabled.
- `CANONICAL_HOSTNAME` is now `returnsform14.org`.
- `GOOGLE_OAUTH_REDIRECT_URI_FALLBACK` now points to the
  `returnsform14.org` callback path.
- `ALLOWED_HOSTS` includes the LAN server IP, Docker bridge IPs, localhost, the
  Render hostname, both `returnsform14.org` hostnames, and
  `research.harolditdata.uk`.
- PostgreSQL-related URLs and host values now point at the LAN server IP rather
  than `127.0.0.1` or `host.docker.internal`.
- Upload size was increased from 16 MB to 50 MB.
- Docker bind IP was narrowed from all interfaces to the LAN server IP.
- PostgreSQL allowed CIDR values were added for LAN and Docker bridge access.
- LAN route replacement was disabled while default route replacement remains
  enabled.

Impact on the app:

- Browser-generated absolute URLs and fallback OAuth redirects should now use
  the Cloudflare HTTPS domain instead of a LAN URL.
- Secure cookies now require HTTPS. This is correct for Cloudflare access, but
  plain HTTP direct-LAN testing may not keep sessions unless HTTPS is used.
- The app will accept requests for `returnsform14.org`,
  `www.returnsform14.org`, and `research.harolditdata.uk`.
- Database connectivity now assumes PostgreSQL is reachable at the LAN server
  IP from the app container.
- Users can upload larger files, up to the configured 50 MB limit.
- Direct Docker port binding is limited to the configured LAN IP, reducing
  accidental exposure on other host interfaces.

Risk notes:

- `.env.production` contains secrets and must not be published outside the
  intended private repository/server context.
- If the server LAN IP changes, runtime env refresh or manual env updates are
  required before app and database connectivity will be reliable.
- If Cloudflare Tunnel is not yet active, users should use the LAN HTTPS URL
  for testing.

### `.env.production.example`

Changed new-deployment defaults from the older `returnsform13.org` domain to
`returnsform14.org`.

Important changes:

- Default canonical host is `returnsform14.org`.
- Default allowed hosts include `returnsform14.org`, `www.returnsform14.org`,
  and `research.harolditdata.uk`.
- Default Google OAuth fallback callback uses the `returnsform14.org` domain.

Impact on the app:

- Fresh deployments created from the example file will start with the correct
  public domain assumptions.
- Reduces the chance of deploying a new server with stale `returnsform13.org`
  settings.

### `README.md`

Replaced the old direct-DNS/Caddy-first instructions with Cloudflare Tunnel
instructions.

Important changes:

- Documents Cloudflare Tunnel as the preferred domain setup for
  `returnsform14.org`.
- Points operators to `docs/CLOUDFLARE_TUNNEL.md`.
- Shows quick start commands for `.env.cloudflare` and
  `./scripts/cloudflare_tunnel_up.sh postgres`.
- Explains that the Cloudflare published application should use service type
  `HTTPS`, URL `web:8000`, and `No TLS Verify`.
- Keeps the old direct-DNS/Caddy path as a legacy option.

Impact on the app:

- No runtime impact.
- Operational impact is significant: future deployers are directed toward the
  tunnel-based public access path rather than public-IP DNS.

### `_deploy_common.sh`

Added an explicit successful return from `warn_if_placeholder_env`.

Impact on the app:

- No direct runtime impact.
- Deployment scripts are less likely to fail under `set -e` when no placeholder
  warning is emitted.
- This improves deployment reliability because warning checks should warn only,
  not stop deployment.

### `healthcheck.sh`

Changed the final printed LAN URL from a hardcoded `http://server-ip:port` to
the resolved `$URL`.

Impact on the app:

- No runtime behavior change in Flask.
- Healthcheck output now matches HTTPS and configured `PUBLIC_BASE_URL`
  behavior more accurately.
- This avoids misleading operators when certificates or Cloudflare HTTPS are in
  use.

### `ip_address_change.py`

Updated runtime env rendering behavior.

Important changes:

- Host and container PostgreSQL defaults changed to `auto`.
- Added logic to preserve an explicit public domain URL such as
  `https://returnsform14.org`.
- IP-based, localhost, or auto public URLs can still be regenerated from the
  detected server IP.
- Container PostgreSQL host now follows the detected primary host IP when set
  to auto.

Impact on the app:

- Running the runtime environment refresh should no longer overwrite
  `PUBLIC_BASE_URL=https://returnsform14.org` with a LAN IP URL.
- Cloudflare public URL settings are therefore safer during repeated deploys.
- PostgreSQL URLs are more likely to use the actual reachable host IP on this
  LAN deployment.

Risk notes:

- If a deployment intentionally wants a LAN-IP public URL, it should use
  `PUBLIC_BASE_URL=auto` or an IP-based value.
- If PostgreSQL is only reachable through `host.docker.internal` in another
  environment, that environment should explicitly set the DB host instead of
  relying on auto detection.

### `scripts/migrate_sqlite_to_postgres.py`

Improved migration environment and session cleanup.

Important changes:

- Saves the previous `SKIP_SCHEMA_CHECK` value before importing the app.
- Restores or removes `SKIP_SCHEMA_CHECK` after import.
- Removes the SQLAlchemy session before starting the SQLite import.

Impact on the app:

- Reduces environment-variable leakage after migration helper imports app code.
- Reduces risk of stale sessions or connections interfering with the import.
- Helps repeatable SQLite-to-PostgreSQL migration runs.

### `test_ip_address_change.py`

Added a focused test proving that explicit public domain URLs are preserved
during runtime env rendering.

Impact on the app:

- No runtime impact.
- Protects the Cloudflare domain behavior from regression.

## New Cloudflare Tunnel Files

### `docker-compose.cloudflare.yml`

Adds a `cloudflared` Docker Compose sidecar.

Behavior:

- Uses the `cloudflare/cloudflared:latest` image.
- Starts `cloudflared tunnel run` with `--no-autoupdate`.
- Requires `CLOUDFLARE_TUNNEL_TOKEN`.
- Waits for the `web` service healthcheck before starting.
- Restarts unless stopped.

Impact on the app:

- Allows Cloudflare to reach the existing Flask/Gunicorn container through the
  Docker network.
- Avoids requiring inbound public ports on the server.
- Requires outbound connectivity from the server/container to Cloudflare.

Operational note:

- The actual tunnel token must be stored in `.env.cloudflare`, not in git.

### `.env.cloudflare.example`

Template for the Cloudflare tunnel token.

Impact on the app:

- No runtime impact until copied to `.env.cloudflare` and populated.
- Provides a safe, non-secret example file for operators.

### `.gitignore`

Adds ignore rules for:

- `.env.cloudflare`
- local Cloudflare credential files
- Python bytecode/cache files

Impact on the app:

- No runtime impact.
- Reduces risk of committing tunnel credentials or generated Python cache files.

### `scripts/cloudflare_tunnel_up.sh`

New helper script for starting the app with the Cloudflare overlay.

Behavior:

- Defaults to PostgreSQL mode.
- Supports SQLite mode.
- Requires `.env.production`.
- Requires `.env.cloudflare` with `CLOUDFLARE_TUNNEL_TOKEN`.
- Validates the merged Docker Compose configuration before startup.
- Starts the base production compose file plus `docker-compose.cloudflare.yml`.

Impact on the app:

- Provides a repeatable command for tunnel deployments:
  `./scripts/cloudflare_tunnel_up.sh postgres`
- Reduces manual compose command errors.

### `docs/CLOUDFLARE_TUNNEL.md`

New operational runbook for configuring Cloudflare Tunnel.

Includes:

- Cloudflare dashboard steps.
- Published application setup for `returnsform14.org` and
  `www.returnsform14.org`.
- Required service settings: type `HTTPS`, URL `web:8000`, and `No TLS Verify`.
- Local token setup.
- Deployment and verification commands.

Impact on the app:

- No runtime impact.
- Provides the procedure required before public domain access can work.

Operator note:

- The Cloudflare account is expected to be accessed using the Google login
  supplied by the operator: `joelhonyango@gmail.com`.

## New Runtime Artifacts

### `certs/server.crt` and `certs/server.key`

Self-signed local TLS certificate files are present and untracked.

Impact on the app:

- Production Gunicorn uses these files when mounted into the container.
- Cloudflare should connect to the origin as HTTPS.
- Because the certificate is self-signed, Cloudflare Tunnel must use
  `No TLS Verify` for the published application origin.

Risk notes:

- `certs/server.key` is a private key and should not be committed.
- If these files are deleted, the deployment helper can regenerate local certs,
  but Cloudflare origin settings must still match the actual app behavior.

### Backup directories

Two untracked backup directories exist:

- `backups/Monday, 13--07--2026, 09-00-51/`
- `backups/Monday, 13--07--2026, 09-00-52/`

Each contains exported workbook files, a SQLite backup, a zip archive, and a
backup manifest.

Impact on the app:

- No immediate runtime impact unless an operator restores from them.
- They are useful operational recovery artifacts.

Risk notes:

- Backup files may contain production data.
- Do not commit or share them unless that is an intentional backup archival
  decision.

## Validation Run Today

The following checks passed:

```bash
env CLOUDFLARE_TUNNEL_TOKEN=dummy-token docker compose --env-file .env.production -f docker-compose.prod.postgres.yml -f docker-compose.cloudflare.yml config --quiet
env CLOUDFLARE_TUNNEL_TOKEN=dummy-token docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.cloudflare.yml config --quiet
bash -n scripts/cloudflare_tunnel_up.sh
/tmp/pbora-migrate-venv/bin/python -m pytest test_ip_address_change.py::test_render_runtime_env_preserves_explicit_public_domain
```

Result:

- PostgreSQL compose merge with Cloudflare overlay is valid.
- SQLite compose merge with Cloudflare overlay is valid.
- Cloudflare helper script syntax is valid.
- Focused runtime-env regression test passed.

Not run:

- Full test suite.
- Live Cloudflare tunnel connection, because the real tunnel token is not
  present in the workspace.

## Expected Deployment Flow

1. In Cloudflare, create a Cloudflared tunnel for `returnsform14.org`.
2. Add published application hostnames for `returnsform14.org` and
   `www.returnsform14.org`.
3. Use service type `HTTPS`, URL `web:8000`, and enable `No TLS Verify`.
4. Create `.env.cloudflare` from `.env.cloudflare.example`.
5. Paste the real `CLOUDFLARE_TUNNEL_TOKEN` into `.env.cloudflare`.
6. Start the deployment:

```bash
./scripts/cloudflare_tunnel_up.sh postgres
```

7. Verify:

```bash
docker logs form14_cloudflared --tail 100
curl -Ik https://returnsform14.org
curl -Ik https://www.returnsform14.org
```

## Overall App Impact

These differences move the app toward a safer public access model:

- Public users access the app through Cloudflare on
  `https://returnsform14.org`.
- The local server does not need public inbound HTTP/HTTPS ports for domain
  traffic.
- The app keeps secure-cookie behavior aligned with HTTPS.
- Runtime env refresh is less likely to undo the public domain configuration.
- Deployment and migration helpers are more robust.

The main remaining dependency is external: the Cloudflare tunnel must be
created in the correct Cloudflare account and the real token must be placed in
the untracked `.env.cloudflare` file.
