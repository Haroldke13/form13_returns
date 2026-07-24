# Cloudflare Tunnel Fix - 2026-07-16

## Summary

`research.harolditdata.uk` was failing with Cloudflare `1033` because the
`dummy tunnel` had no active connector on this server. The app already listens
on `https://10.107.20.241:8000`; the missing piece was running a connector for
the existing Cloudflare tunnel.

## Changes Made

- Added `research.harolditdata.uk` to the Flask trusted host allowlists.
- Retrieved Cloudflare connector tokens from the logged-in Cloudflare One UI.
- Saved the local secrets in ignored runtime files:
  - `.env.cloudflare`
  - `.cloudflared/returnsform14-tunnel.token`
  - `.cloudflared/dummy-tunnel.token`
- Added `docker-compose.cloudflare.dummy.yml` for `dummy tunnel`.
- Updated `docker-compose.cloudflare.yml` and the dummy override to use
  `--token-file` rather than putting tokens in Docker command arguments.
- Updated `scripts/cloudflare_tunnel_up.sh` to support:
  - `primary`
  - `dummy`
  - `both`
  - `--no-build`
- Added `scripts/cloudflaretunnel.sh` as a shortcut for:

```bash
./scripts/cloudflare_tunnel_up.sh postgres both --no-build
```

## Secret Keys Added

The actual token values are intentionally not recorded in git-tracked files.
They are stored locally in `.env.cloudflare` under these keys:

```env
CLOUDFLARE_TUNNEL_TOKEN=<stored locally>
CLOUDFLARE_DUMMY_TUNNEL_TOKEN=<stored locally>
```

The launcher copies those values to:

```text
.cloudflared/returnsform14-tunnel.token
.cloudflared/dummy-tunnel.token
```

## Validation

Both Docker sidecars are running:

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep cloudflared
```

Expected public access:

```bash
curl -Ik https://returnsform14.org/
curl -Ik https://research.harolditdata.uk/
```

Both return `HTTP/2 302` to `/login`, which confirms Cloudflare reaches the
Flask app and the app accepts both hostnames.
