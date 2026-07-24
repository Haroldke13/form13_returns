# PBORA Form 13

## Cloudflare Tunnel domain setup

The preferred domain setup for this server is Cloudflare Tunnel at
`returnsform14.org`, with `research.harolditdata.uk` available through the
separate Cloudflare tunnel named `dummy tunnel`. Use
[docs/CLOUDFLARE_TUNNEL.md](docs/CLOUDFLARE_TUNNEL.md) to create the
Cloudflare connector tokens, configure `returnsform14.org`,
`www.returnsform14.org`, and `research.harolditdata.uk`, and start the
`cloudflared` sidecar.

Quick start after the Cloudflare tunnel token has been copied:

```bash
cp .env.cloudflare.example .env.cloudflare
nano .env.cloudflare
./scripts/cloudflare_tunnel_up.sh postgres
```

To start only the research hostname tunnel sidecar:

```bash
./scripts/cloudflare_tunnel_up.sh postgres dummy
```

To start both Cloudflare tunnel sidecars from the saved local tokens:

```bash
./scripts/cloudflaretunnel.sh
```

The Cloudflare published application should use service type `HTTPS`, URL
`web:8000`, and `No TLS Verify` enabled, because the app container uses the
local certificate files mounted from `./certs`.

## Legacy direct DNS setup

`deploy_once.sh` still supports the older Caddy/direct-DNS deployment path for
servers that have a public IP address. For the current LAN-hosted server,
Cloudflare Tunnel avoids exposing inbound ports.
