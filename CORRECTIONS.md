# Deployment Corrections Report

Date: 2026-07-13

Application: PBORA Form 13 Flask app

Primary LAN URL:

```text
https://10.107.20.241/
```

Direct container URL:

```text
https://10.107.20.241:8000/
```

This report records the hosting problems encountered during deployment, the root cause of each issue, the correction applied, and the code or command pattern to use if the issue reappears.

Sensitive values such as passwords, API keys, private keys, and tokens are intentionally omitted.

## Final Verified State

The server is currently reachable from the host itself and is configured for LAN access.

Verified state:

```text
Docker service: enabled and active
PostgreSQL service: enabled and active
nginx service: enabled and active
Flask container: form14_web healthy
App listener: 10.107.20.241:8000
nginx listeners: 0.0.0.0:80 and 0.0.0.0:443
PostgreSQL listener: 0.0.0.0:5432
Firewall default policy: allow incoming, allow outgoing, allow routed
```

Current important routes:

```text
default via 10.107.20.254 dev eth0
10.107.0.0/19 via 10.107.20.254 dev eth0 src 10.107.20.241
10.107.20.0/24 dev eth0 src 10.107.20.241
```

## 1. Docker Was Running, But User Access Failed

### Symptom

Docker appeared to be down because normal Docker commands failed with:

```text
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

### Root Cause

The Docker daemon was active, but the current shell did not have effective membership in the `docker` group. The socket was owned by `root:docker`.

### Correction

Add the deployment user to the `docker` group and refresh the shell group membership.

```bash
sudo usermod -aG docker pbora
newgrp docker
docker ps
```

For permanent service startup after reboot:

```bash
sudo systemctl enable --now docker
sudo systemctl enable --now docker.socket
```

### Notes

Existing open terminals may still fail until the user runs `newgrp docker` or logs out and back in.

## 2. Deployment Script Exited Early Under `set -e`

### Symptom

`./deploy.sh postgres` stopped immediately after refreshing `.env.production`. It exited before Compose validation and before container startup.

### Root Cause

The helper function `warn_if_placeholder_env` in `_deploy_common.sh` ended with a failed `[[ ... ]]` comparison when no placeholder value was present. Because the deploy script uses `set -e`, that non-zero comparison caused the whole deployment to exit.

### Correction Applied

The function now explicitly returns success after running its warning checks.

File:

```text
_deploy_common.sh
```

Code correction:

```bash
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
```

## 3. App Environment Was Not Fully LAN-Aligned

### Symptom

The app needed to be reachable by LAN users, but some runtime values had to be rendered to the actual server IP and network.

### Root Cause

The production environment uses dynamic networking values. These must match the server's active LAN IP, gateway, app URL, PostgreSQL host, Docker bind IP, and allowed hosts.

### Correction Applied

The runtime environment was refreshed and important values were set in `.env.production`.

Important production values:

```dotenv
ALLOWED_HOSTS=10.107.20.241,172.17.0.1,172.18.0.1,localhost,127.0.0.1,returnsform14.onrender.com,www.returnsform14.org,returnsform14.org,research.harolditdata.uk
PBORA_APP_HOST_IP=10.107.20.241
PBORA_GATEWAY=10.107.20.254
DB_HOST=10.107.20.241
POSTGRES_HOST=10.107.20.241
PUBLIC_BASE_URL=https://10.107.20.241
DOCKER_BIND_IP=10.107.20.241
PBORA_ROUTE_CIDR=10.107.0.0/19
POSTGRES_ALLOWED_CIDR=10.107.0.0/19
POSTGRES_EXTRA_ALLOWED_CIDRS=172.17.0.0/16,172.18.0.0/16
```

Command used by the deployment helper:

```bash
python3 ip_address_change.py --render-runtime-env --env-file .env.production --no-file-backup
```

## 4. PostgreSQL Needed Remote LAN and Container Access

### Symptom

The Flask app container needed to connect to PostgreSQL over TCP, and remote LAN database access was requested.

### Root Cause

PostgreSQL access depends on all of these layers being correct:

- `postgresql.conf` must listen on an external address.
- `pg_hba.conf` must allow the database/user/CIDR.
- The host firewall must allow the database port.
- The app environment must point to the reachable database host.

### Correction Applied

The project repair script was run:

```bash
sudo ENV_FILE=/home/pbora/form13_returns/.env.production \
  /home/pbora/form13_returns/scripts/fix_postgres_access.sh
```

Expected PostgreSQL config pattern:

```conf
listen_addresses = '*'
port = 5432
```

Expected `pg_hba.conf` access pattern:

```conf
host    pbora    pbora    10.107.0.0/19    scram-sha-256
host    pbora    pbora    172.17.0.0/16    scram-sha-256
host    pbora    pbora    172.18.0.0/16    scram-sha-256
```

Verification command:

```bash
PGPASSWORD='<database-password>' psql \
  -h 10.107.20.241 \
  -p 5432 \
  -U pbora \
  -d pbora \
  -c 'select current_database(), current_user, inet_server_addr(), inet_server_port();'
```

Expected result:

```text
current_database = pbora
current_user = pbora
inet_server_addr = 10.107.20.241
inet_server_port = 5432
```

## 5. Firewall Initially Allowed Only Specific Ports

### Symptom

LAN users needed broad access, and later the request was to allow all traffic in and out.

### Root Cause

UFW had specific allow rules for app/database ports, but default policy did not initially allow all traffic.

### Correction Applied

UFW was changed to allow incoming, outgoing, and routed traffic.

```bash
sudo ufw default allow incoming
sudo ufw default allow outgoing
sudo ufw default allow routed
sudo ufw reload
sudo ufw status verbose
```

Expected policy line:

```text
Default: allow (incoming), allow (outgoing), allow (routed)
```

### Safer Alternative

For production security, a safer policy is to deny default inbound traffic and allow only required LAN services:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 10.107.0.0/19 to any port 80 proto tcp
sudo ufw allow from 10.107.0.0/19 to any port 443 proto tcp
sudo ufw allow from 10.107.0.0/19 to any port 8000 proto tcp
sudo ufw allow from 10.107.0.0/19 to any port 5432 proto tcp
sudo ufw reload
```

The current requested state is fully open.

## 6. Docker App Needed Rebuild and Restart

### Symptom

The app needed to be refreshed after environment and database access corrections.

### Root Cause

The Flask container uses `.env.production`, Docker port publishing, certificates, and database settings. After those values were changed, the container needed to be recreated.

### Correction Applied

Run the production PostgreSQL deployment:

```bash
cd /home/pbora/form13_returns
./deploy.sh postgres
```

Direct Compose command:

```bash
docker compose --env-file .env.production \
  -f docker-compose.prod.postgres.yml \
  up -d --build
```

Verification:

```bash
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
./healthcheck.sh postgres
```

Expected status:

```text
form14_web Up ... (healthy) 10.107.20.241:8000->8000/tcp
```

## 7. Upload Limit Mismatch Between nginx and Flask

### Symptom

Uploads could still fail even though nginx allowed larger request bodies.

### Root Cause

nginx was configured with:

```nginx
client_max_body_size 50m;
```

But Flask was later overwriting `MAX_CONTENT_LENGTH` from the environment. The env value was still 16 MB.

### Correction Applied

Set Flask to the same 50 MB limit as nginx:

```dotenv
MAX_CONTENT_LENGTH=52428800
MAX_REQUEST_MB=50
```

### Code Hardening Recommendation

The app currently assigns `MAX_CONTENT_LENGTH` in more than one place. A cleaner single-source version would be:

```python
request_mb = max(int(os.getenv("MAX_REQUEST_MB", "50") or 50), 50)
default_max_content_length = request_mb * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("MAX_CONTENT_LENGTH", str(default_max_content_length))
)
```

This avoids accidentally lowering the limit later in startup.

## 8. Phone on WiFi Could Not Access the App

### Symptom

The app worked from the server, but a phone on WiFi could not access:

```text
https://10.107.20.241/
```

### Root Cause

The host had a broad direct route:

```text
10.107.0.0/19 dev eth0 scope link src 10.107.20.241
```

That made the server try to ARP directly for WiFi clients such as `10.107.22.x`. If WiFi clients are on a routed VLAN/subnet, the server must send replies through the gateway instead.

Failed neighbor entries were visible for `10.107.22.x` addresses.

### Correction Applied

Replace the broad direct LAN route with a route via the gateway:

```bash
sudo ip route replace 10.107.0.0/19 via 10.107.20.254 dev eth0 src 10.107.20.241
```

Verify route selection:

```bash
ip route get 10.107.22.138
```

Expected result:

```text
10.107.22.138 via 10.107.20.254 dev eth0 src 10.107.20.241
```

Same-subnet clients should remain direct:

```bash
ip route get 10.107.20.244
```

Expected result:

```text
10.107.20.244 dev eth0 src 10.107.20.241
```

### Persistent Deployment Correction

Disable the deployment route step that reintroduced the incorrect direct route:

```dotenv
PBORA_REPLACE_LAN_ROUTE=0
```

The current netplan file already has the correct default gateway:

```yaml
network:
  ethernets:
    eth0:
      addresses:
        - 10.107.20.241/24
      routes:
        - to: default
          via: 10.107.20.254
  version: 2
```

If an explicit persistent route is ever required, use a gateway route, not an on-link route:

```yaml
routes:
  - to: default
    via: 10.107.20.254
  - to: 10.107.0.0/19
    via: 10.107.20.254
```

## 9. HTTPS Certificate Warning on Phones

### Symptom

Phones may show a browser warning even after network access is fixed.

### Root Cause

The LAN HTTPS certificate is locally generated/self-signed unless a trusted internal certificate authority or public DNS certificate is used.

### Correction

For immediate use, open:

```text
https://10.107.20.241/
```

Then accept the browser's certificate warning if prompted.

For a permanent trusted solution, use an internal DNS name and install a trusted certificate:

```nginx
server {
    listen 443 ssl default_server;
    server_name returnsform13.org _;

    ssl_certificate /home/pbora/form13_returns/certs/server.crt;
    ssl_certificate_key /home/pbora/form13_returns/certs/server.key;

    client_max_body_size 50m;

    location / {
        proxy_pass https://10.107.20.241:8000;
        proxy_ssl_verify off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

## 10. nginx Reverse Proxy Needed to Remain Aligned With App Port

### Symptom

The public LAN entrypoint must use nginx on `443`, while the Flask container listens with TLS on `8000`.

### Root Cause

The app container itself serves HTTPS on port `8000`, so nginx must proxy to `https://10.107.20.241:8000` and disable upstream certificate verification for the local self-signed upstream certificate.

### Correct nginx Pattern

```nginx
location / {
    proxy_pass https://10.107.20.241:8000;
    proxy_ssl_verify off;
    proxy_http_version 1.1;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host $host;

    proxy_connect_timeout 30s;
    proxy_send_timeout 360s;
    proxy_read_timeout 360s;
}
```

Verification:

```bash
curl -k -I --max-time 10 https://10.107.20.241/
curl -I --max-time 10 http://10.107.20.241/
curl -k -I --max-time 10 https://10.107.20.241:8000/
```

Expected results:

```text
http://10.107.20.241/       -> 301 redirect to HTTPS
https://10.107.20.241/      -> 302 redirect to /login
https://10.107.20.241:8000/ -> 302 redirect to /login
```

## 11. Useful Troubleshooting Commands

Check Docker:

```bash
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
docker logs --tail 80 form14_web
```

Check listeners:

```bash
ss -ltnp
```

Check routes:

```bash
ip -4 route show
ip route get <phone-ip-address>
```

Check nginx:

```bash
sudo nginx -T
sudo tail -n 80 /var/log/nginx/access.log
sudo tail -n 80 /var/log/nginx/error.log
```

Check firewall:

```bash
sudo ufw status verbose
```

Check PostgreSQL:

```bash
PGPASSWORD='<database-password>' pg_isready \
  -h 10.107.20.241 \
  -p 5432 \
  -U pbora \
  -d pbora
```

Check app health:

```bash
cd /home/pbora/form13_returns
./healthcheck.sh postgres
```

## 12. If Phone Access Still Fails

Use the phone browser to open:

```text
https://10.107.20.241/
```

Then check whether the phone request reached nginx:

```bash
sudo tail -n 50 /var/log/nginx/access.log
```

If no phone request appears in nginx logs, the problem is outside the Flask app and outside Docker. Check:

- The phone's WiFi IP address.
- Whether the WiFi network uses guest/client isolation.
- Whether the phone is on a different VLAN that cannot route to `10.107.20.241`.
- Whether the gateway `10.107.20.254` allows traffic between WiFi and the server VLAN.
- Whether the phone is using mobile data or VPN instead of the work WiFi.

If a request appears in nginx logs but the browser fails, check:

- Certificate warning acceptance.
- nginx error log.
- Flask container logs.
- Host header and `ALLOWED_HOSTS`.

## Summary of Main Corrections

Main corrected items:

```text
Docker access: user added to docker group, service enabled.
Deploy helper: warn_if_placeholder_env now returns 0.
App env: LAN IP, database host, public URL, allowed hosts, and upload limits corrected.
PostgreSQL: remote LAN/container access enabled and verified.
Firewall: default incoming, outgoing, and routed traffic allowed.
Routing: WiFi/VLAN clients now route via gateway instead of direct ARP.
Deployment: Flask container rebuilt and verified healthy.
```
