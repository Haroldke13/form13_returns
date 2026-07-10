DEPLOYMENT NOTES DEPLOYMENT NOTES 
DEPLOYMENT NOTES DEPLOYMENT NOTES 

Implemented in [one_command_deploy.sh](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/one_command_deploy.sh:1).

After Git clone, the intended production run is now:

```bash
chmod +x *.sh scripts/*.sh
./one_command_deploy.sh postgres
```

For development sudo mode:

```bash
PBORA_DEPLOY_ENV=development ./one_command_deploy.sh postgres
```

What the one-command deploy now does end to end:

- primes sudo non-interactively: production default `pbora`, development default `Programmer.95`
- installs Ubuntu packages, PostgreSQL, Docker Engine, Compose, Buildx
- refreshes `.env.production` from the live server network
- runs the automatic route setup for `10.107.0.0/19`
- creates/updates local PostgreSQL user/database `pbora`
- runs [scripts/fix_postgres_access.sh](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/scripts/fix_postgres_access.sh:1)
- opens UFW app access from `10.107.0.0/19` to port `8000` when UFW is active
- migrates SQLite backup into PostgreSQL
- builds and starts Docker Compose
- runs [healthcheck.sh](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/healthcheck.sh:1)
- runs a browser-style HTTP access check with the server IP as the Host header and fails clearly on `BadHost`, `host not allowed`, or `Bad token`

I also fixed the LAN host issue in [`.env.production`](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/.env.production:6):

- `ALLOWED_HOSTS=auto,...`
- `PBORA_APP_HOST_IP=auto`
- `PBORA_GATEWAY=auto`
- `PUBLIC_BASE_URL=auto`
- `DOCKER_BIND_IP=0.0.0.0`
- `FORCE_CANONICAL_HOST=0`

That prevents the app from trusting only an old hard-coded IP and prevents canonical-domain redirect behavior from breaking LAN access.

Updated the runbook at [INSTRUCTIONS.md](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/INSTRUCTIONS.md:286) to make `one_command_deploy.sh postgres` the post-clone path.

Verified locally without mutating the host:

- `13 passed` for focused deployment/runtime tests
- `bash -n one_command_deploy.sh` passed
- Compose config passed with and without route profile
- route dry-run prints `ip route replace 10.107.0.0/19 ...`

I did not execute the full deploy on this workstation because it would install packages, modify routes, touch PostgreSQL, and start containers on the local machine rather than the target Ubuntu VM.


Yes. The `wlo1` / `10.107.22.138` commands were just the dry-run result from this current laptop/session. They are not hard-coded for production.

On production with `eth0` and server IP `10.107.20.241`, the same route setup should resolve to:

```bash
ip route replace 10.107.0.0/19 dev eth0 src 10.107.20.241
ip route replace default via <detected-gateway> dev eth0
```

If the production gateway is `10.107.20.254`, then it becomes:

```bash
ip route replace default via 10.107.20.254 dev eth0
```

Why it adapts:

- `one_command_deploy.sh` runs `ip_address_change.py --render-runtime-env` first, so `.env.production` gets rewritten from the production server’s live network.
- `PBORA_NETWORK_INTERFACE=auto` lets the route script choose the real default-route interface, which should be `eth0` on that server.
- `PBORA_ROUTE_CIDR=10.107.0.0/19` is already set, so clients from `10.107.0.0` through `10.107.31.255` are in scope.
- Docker binds the app to the detected server IP via `DOCKER_BIND_IP`, so production should become `10.107.20.241:8000`.

Before a real deploy, preview on the production server with:

```bash
cd /home/harold-coder/Downloads/form14-main__/PBORA_FORM_133
PBORA_ROUTE_DRY_RUN=1 PBORA_APP_HOST_IP=10.107.20.241 PBORA_GATEWAY=10.107.20.254 PBORA_NETWORK_INTERFACE=eth0 ./scripts/configure_host_routes.sh
```

One condition: if UFW is active, port `8000` must be allowed from the `/19` LAN:

```bash
sudo ufw allow from 10.107.0.0/19 to any port 8000 proto tcp
```



Implemented.

`one_command_deploy.sh` now automatically runs the Compose route setup during `postgres` deployment after `.env.production` is refreshed and before the app containers start:

```bash
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml --profile route-setup run --rm host-route-setup
```

The active route target is now the broader PBORA LAN:

```bash
ip route replace 10.107.0.0/19 dev wlo1 src 10.107.22.138
ip route replace default via 10.107.22.254 dev wlo1
```

Changed files:
- [one_command_deploy.sh](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/one_command_deploy.sh:299)
- [docker-compose.prod.postgres.yml](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/docker-compose.prod.postgres.yml:2)
- [scripts/configure_host_routes.sh](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/scripts/configure_host_routes.sh:145)
- [`.env.production`](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/.env.production:315)
- [`.env.production.example`](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/.env.production.example:24)
- [INSTRUCTIONS.md](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/INSTRUCTIONS.md:87)

Verified:
- `pytest -q -p no:cacheprovider tests/test_one_command_deploy_permissions.py tests/test_docker_runtime_config.py` -> `8 passed`
- Compose config validates with and without `--profile route-setup`
- `PBORA_ROUTE_DRY_RUN=1 scripts/configure_host_routes.sh` prints the expected `/19` route command
- `bash -n one_command_deploy.sh` and `sh -n scripts/configure_host_routes.sh` pass

I did not apply the live route changes from this shell; only dry-run verification was executed.



Added the route setup path to [docker-compose.prod.postgres.yml](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/docker-compose.prod.postgres.yml:2). It now has an opt-in `host-route-setup` service under the `route-setup` profile, using host networking, `NET_ADMIN`, and [scripts/configure_host_routes.sh](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/scripts/configure_host_routes.sh:1).

The dry-run resolves the current Ubuntu route commands to:

```bash
ip route replace 10.107.22.0/24 dev wlo1 src 10.107.22.138
ip route replace default via 10.107.22.254 dev wlo1
```

To apply through Compose:

```bash
sudo docker compose --env-file .env.production -f docker-compose.prod.postgres.yml --profile route-setup run --rm host-route-setup
```

Also added `iproute2` to [Dockerfile](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/Dockerfile:11), route variables to the env files, and documented the direct Ubuntu/Compose commands in [INSTRUCTIONS.md](/home/harold-coder/Downloads/form14-main__/PBORA_FORM_133/INSTRUCTIONS.md:87).

Verified:
- `pytest -q -p no:cacheprovider tests/test_docker_runtime_config.py` -> `3 passed`
- `docker compose --env-file .env.production -f docker-compose.prod.postgres.yml config --quiet` -> OK
- `docker compose --env-file .env.production -f docker-compose.prod.postgres.yml --profile route-setup config --quiet` -> OK
- Dry-run route script printed the expected `ip route replace` commands.

I did not apply the real route changes; I only verified dry-run so the host network was not mutated.