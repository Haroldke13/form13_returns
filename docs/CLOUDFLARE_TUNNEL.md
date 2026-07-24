# Cloudflare Tunnel for returnsform14.org

This app can be exposed through Cloudflare Tunnel with the `cloudflared`
sidecar in `docker-compose.cloudflare.yml`. A second optional sidecar in
`docker-compose.cloudflare.dummy.yml` can run the Cloudflare tunnel named
`dummy tunnel` for `research.harolditdata.uk`. Tunnel tokens stay in the
untracked `.env.cloudflare` file.

## Cloudflare dashboard setup

1. Sign in to the Cloudflare account that owns `returnsform14.org`.
2. Go to Cloudflare One > Networks > Connectors > Cloudflare Tunnels.
3. Create a Cloudflared tunnel named `form14-returnsform14-org`.
4. Copy the Docker connector token from the tunnel setup screen.
5. Add published application hostnames:
   - Hostname: `returnsform14.org`
   - Service type: `HTTPS`
   - Service URL: `web:8000` (the HTTPS origin is `https://web:8000`)
   - TLS setting: enable `No TLS Verify`
6. Add a second published application for `www.returnsform14.org` with the
   same service settings.
7. In the Cloudflare account that owns `harolditdata.uk`, configure the
   separate tunnel named `dummy tunnel`.
8. Add a published application hostname:
   - Hostname: `research.harolditdata.uk`
   - Service type: `HTTPS`
   - Service URL: `web:8000` (the HTTPS origin is `https://web:8000`)
   - TLS setting: enable `No TLS Verify`

`web:8000` is the Docker service name and port used by the Flask container.
Production Gunicorn currently serves that port with the local self-signed
certificate mounted from `./certs`, so `No TLS Verify` is required.

## Server setup

Create the local tunnel env file:

```bash
cp .env.cloudflare.example .env.cloudflare
nano .env.cloudflare
```

Paste the token after `CLOUDFLARE_TUNNEL_TOKEN=`.
For the separate `dummy tunnel`, paste its token after
`CLOUDFLARE_DUMMY_TUNNEL_TOKEN=`.

The launcher writes those secrets to local token files before starting Docker:

```text
.cloudflared/returnsform14-tunnel.token
.cloudflared/dummy-tunnel.token
```

Both `.env.cloudflare` and `.cloudflared/` are ignored by git. Do not copy the
raw token values into tracked documentation.

Recommended `.env.production` public URL settings:

```env
PUBLIC_BASE_URL=https://returnsform14.org
PREFERRED_URL_SCHEME=https
SESSION_COOKIE_SECURE=true
CANONICAL_HOSTNAME=returnsform14.org
FORCE_CANONICAL_HOST=0
ALLOWED_HOSTS=auto,localhost,127.0.0.1,returnsform14.org,www.returnsform14.org,research.harolditdata.uk
GOOGLE_OAUTH_REDIRECT_URI_FALLBACK=https://returnsform14.org/auth/google/callback
```

Keep `FORCE_CANONICAL_HOST=0` until the tunnel is verified. You can switch it
to `1` later if all direct-IP access should redirect to the domain.

Start the PostgreSQL deployment with the tunnel:

```bash
./scripts/cloudflare_tunnel_up.sh postgres
```

Start only the `dummy tunnel` sidecar:

```bash
./scripts/cloudflare_tunnel_up.sh postgres dummy
```

Start both Cloudflare tunnel sidecars:

```bash
./scripts/cloudflare_tunnel_up.sh postgres both
```

Shortcut for the same action without rebuilding the app image:

```bash
./scripts/cloudflaretunnel.sh
```

For SQLite mode:

```bash
./scripts/cloudflare_tunnel_up.sh sqlite
```

Manual equivalent:

```bash
set -a
. ./.env.cloudflare
set +a
docker compose --env-file .env.production \
  -f docker-compose.prod.postgres.yml \
  -f docker-compose.cloudflare.yml \
  up -d --build
```

## Verify

```bash
docker logs form14_cloudflared --tail 100
docker logs form14_cloudflared_dummy --tail 100
curl -Ik https://returnsform14.org
curl -Ik https://www.returnsform14.org
curl -Ik https://research.harolditdata.uk
```

The tunnel connector should show as `Healthy` in Cloudflare One. If the
hostname returns a Cloudflare `1016` error, the DNS route exists but the tunnel
is not running or is not connected to the same Cloudflare account.
