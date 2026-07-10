# PBORA Form 13 LAN Deployment And Migration Instructions

This package deploys the PBORA Form 13 Flask app to an Ubuntu/PostgreSQL host and serves the LAN app at the server's current IP:

```text
http://<server-ip>:8000
```

Current deployment values:

- Ubuntu app server IP: detected by `./deploy.sh postgres`, `./update.sh postgres`, and `./healthcheck.sh postgres`
- PostgreSQL host from host shell scripts: `127.0.0.1`
- PostgreSQL host from the web container: `host.docker.internal`
- LAN gateway: detected when runtime env values are rendered
- PostgreSQL database/user/password: `pbora` / `pbora` / `pbora`
- Flask/Gunicorn port: `8000`
- Standard app login password for all seeded/imported users: `field.12345`
- Superadmin login: `joel@returnsform14.org` / `field.12345`
- GitHub repository: `https://github.com/Haroldke13/pbora_form13.git`

## 1. Push This Folder To GitHub

Run Git from this folder only:

```bash
cd /home/harold-coder/Downloads/form14-main__/PBORA_FORM_13
git init
git branch -M main
git remote add origin https://github.com/Haroldke13/pbora_form13.git
git add -A
git commit -m "Deploy PBORA Form 13 LAN PostgreSQL package"
git push -u origin main
```

Do not add a `.gitignore` or `.dockerignore` for this package. The deployment bundle is intended to include every file in `PBORA_FORM_13`.

If `origin` already exists, update it:

```bash
git remote set-url origin https://github.com/Haroldke13/pbora_form13.git
```

## 2. Configure Ubuntu Static Network

Confirm the network interface:

```bash
ip addr
ip route
```

Create or edit the Netplan file:

```bash
sudo nano /etc/netplan/01-pbora-lan.yaml
```

Example, replacing `eth0`, `<server-ip>`, `<prefix>`, `<gateway-ip>`, and DNS values with the real network values for that site:

```yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: no
      addresses:
        - <server-ip>/<prefix>
      routes:
        - to: default
          via: <gateway-ip>
      nameservers:
        addresses:
          - <dns-ip>
          - 1.1.1.1
          - 8.8.8.8
```

Apply and test:

```bash
sudo netplan apply
ip addr
ip route
ping -c 3 <gateway-ip>
```

Optional Compose-managed route setup:

```bash
# Preview the Ubuntu route commands without changing routes.
PBORA_ROUTE_DRY_RUN=1 ./scripts/configure_host_routes.sh

# Apply the route commands directly on the Ubuntu server.
sudo ./scripts/configure_host_routes.sh

# Or run the same route setup through Docker Compose.
sudo docker compose --env-file .env.production -f docker-compose.prod.postgres.yml --profile route-setup run --rm host-route-setup
```

The route setup resolves these values from `.env.production`:

```text
PBORA_NETWORK_INTERFACE=auto
PBORA_APP_HOST_IP=auto
PBORA_GATEWAY=auto
PBORA_ROUTE_PREFIX=19
PBORA_ROUTE_CIDR=10.107.0.0/19
PBORA_ASSIGN_HOST_IP=0
PBORA_REPLACE_LAN_ROUTE=1
PBORA_REPLACE_DEFAULT_ROUTE=1
PBORA_ROUTE_DRY_RUN=0
```

`one_command_deploy.sh postgres` runs this route setup automatically after it refreshes `.env.production`, before it starts the application containers.

## 3. Install Server Packages

```bash
sudo apt update
sudo apt install -y git curl ca-certificates gnupg lsb-release nano ufw postgresql postgresql-contrib postgresql-client python3 python3-venv
```

Install Docker:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and log back in after adding the user to the Docker group.

## 4. Create PostgreSQL Database

Open PostgreSQL:

```bash
sudo -u postgres psql
```

Run:

```sql
CREATE USER pbora WITH ENCRYPTED PASSWORD 'pbora';
CREATE DATABASE pbora OWNER pbora;

ALTER ROLE pbora SET client_encoding TO 'UTF8';
ALTER ROLE pbora SET default_transaction_isolation TO 'read committed';
ALTER ROLE pbora SET timezone TO 'Africa/Nairobi';

GRANT ALL PRIVILEGES ON DATABASE pbora TO pbora;
\c pbora
GRANT ALL ON SCHEMA public TO pbora;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pbora;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO pbora;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO pbora;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO pbora;
\q
```

Allow LAN/app-container connections:

```bash
sudo ./scripts/fix_postgres_access.sh
```

Test:

```bash
PGPASSWORD=pbora psql -h 127.0.0.1 -p 5432 -U pbora -d pbora -c "select current_database(), current_user;"
```

## 5. Clone The App On The Ubuntu Server

```bash
cd /opt
sudo git clone https://github.com/Haroldke13/pbora_form13.git pbora-form-13
sudo chown -R "$USER:$USER" /opt/pbora-form-13
cd /opt/pbora-form-13
```

Confirm `.env.production` includes:

```text
PBORA_RUNTIME_ENV_AUTO_UPDATE=1
PUBLIC_BASE_URL=auto
ALLOWED_HOSTS=auto,localhost,127.0.0.1,returnsform14.onrender.com,www.returnsform14.org,returnsform14.org
PBORA_NETWORK_INTERFACE=auto
PBORA_APP_HOST_IP=auto
PBORA_GATEWAY=auto
PBORA_ROUTE_PREFIX=19
PBORA_ROUTE_CIDR=10.107.0.0/19
PBORA_ASSIGN_HOST_IP=0
PBORA_REPLACE_LAN_ROUTE=1
PBORA_REPLACE_DEFAULT_ROUTE=1
PBORA_ROUTE_DRY_RUN=0
INTERNAL_DATABASE_URL=postgresql+psycopg2://pbora:pbora@host.docker.internal:5432/pbora
DB_HOST=127.0.0.1
DB_NAME=pbora
DB_USER=pbora
DB_PASSWORD=pbora
DB_SSL_MODE=disable
EXTERNAL_POSTGRES=1
RUN_SCHEMA_SYNC_ON_STARTUP=1
PORT=8000
HOST_PORT=8000
WEB_CONCURRENCY=2
GUNICORN_THREADS=8
GUNICORN_TIMEOUT=360
ADMIN_USER_PASSWORD=field.12345
USER_SEED_PASSWORD_DEFAULT=field.12345
```

## 6. Migrate SQLite Backup Into PostgreSQL

The migration source is:

```text
returnsform14_org_backup.sqlite
```

From `/opt/pbora-form-13`, install Python dependencies and run the importer:

```bash
python3 -m venv /tmp/pbora-migrate-venv
. /tmp/pbora-migrate-venv/bin/activate
pip install -r requirements.txt
python3 scripts/migrate_sqlite_to_postgres.py --env-file .env.production --source returnsform14_org_backup.sqlite --replace-existing --sync-env-users --reset-user-password field.12345
deactivate
```

What this does:

- Creates/syncs the PostgreSQL schema.
- Imports rows from `returnsform14_org_backup.sqlite`.
- Preserves non-empty legacy SQLite IDs and resets PostgreSQL sequences.
- Handles the `users_for_form14` / `pbo_reports` foreign-key cycle safely.
- Creates or updates users from `.env.production`.
- Resets every user password, including the superadmin, to `field.12345`.
- Writes an `admin_settings` marker named `sqlite_postgres_migration_completed`.

Expected important counts after the current migration:

```text
users_for_form14=54
pbo_reports=3179
field_change_logs=31634
pbo_staff_biodata=29034
```

The user count is `54` because the backup contains `52` users and `.env.production` creates two additional configured users.

## 7. Verify PostgreSQL And Passwords

Connectivity/table check:

```bash
python3 scripts/verify_pbora_postgres.py --env-file .env.production --init-app-tables
```

Row-count check:

```bash
PGPASSWORD=pbora psql -h 127.0.0.1 -p 5432 -U pbora -d pbora -c "select 'users_for_form14' as table_name, count(*) from users_for_form14 union all select 'pbo_reports', count(*) from pbo_reports union all select 'field_change_logs', count(*) from field_change_logs union all select 'pbo_staff_biodata', count(*) from pbo_staff_biodata;"
```

Password check from the app:

```bash
python3 -c "import os; from dotenv import load_dotenv; load_dotenv('.env.production', override=True); os.environ['SKIP_SCHEMA_CHECK']='1'; import app; from models import User; ctx=app.app.app_context(); ctx.push(); users=User.query.order_by(User.id).all(); bad=[u.email for u in users if not u.check_password('field.12345')]; superadmin=User.query.filter_by(email='joel@returnsform14.org').first(); print(f'user_count={len(users)} bad_password_count={len(bad)} superadmin_password_ok={bool(superadmin and superadmin.check_password(\"field.12345\"))} superadmin_is_superadmin={bool(superadmin and superadmin.is_superadmin)}'); ctx.pop()"
```

Expected:

```text
bad_password_count=0
superadmin_password_ok=True
superadmin_is_superadmin=True
```

## 8. Deploy With Docker

From `/opt/pbora-form-13`:

```bash
chmod +x *.sh scripts/*.sh
./one_command_deploy.sh postgres
```

`one_command_deploy.sh postgres` runs the full Ubuntu VM deployment path after
Git clone: package installation, Docker Engine and Compose setup, runtime env
rendering, host route setup for `10.107.0.0/19`, local PostgreSQL creation,
PostgreSQL listener/firewall repair, SQLite-to-PostgreSQL import, Docker build,
container startup, `healthcheck.sh`, and an HTTP browser-style access check.

The default sudo password mode is production:

```bash
./one_command_deploy.sh postgres
```

For the development VM password mode:

```bash
PBORA_DEPLOY_ENV=development ./one_command_deploy.sh postgres
```

Manual fallback:

```bash
chmod +x deploy.sh required.sh healthcheck.sh backup.sh restore.sh update.sh docker-entrypoint.sh
./deploy.sh postgres
```

If Docker still requires sudo in the first shell session:

```bash
sudo docker compose --env-file .env.production -f docker-compose.prod.postgres.yml up -d --build
sudo docker compose --env-file .env.production -f docker-compose.prod.postgres.yml exec -T web flask init-db
./healthcheck.sh postgres
```

Open:

```text
http://<server-ip>:8000
```

## 9. Open Firewall For LAN Computers

```bash
sudo ufw allow from <lan-cidr> to any port 8000 proto tcp
sudo ufw allow from <lan-cidr> to any port 5432 proto tcp
sudo ufw enable
sudo ufw status
```

If the app shows an internal server error and PostgreSQL checks time out, repair
the database listener, `pg_hba.conf`, and firewall on the Ubuntu/PostgreSQL host:

```bash
sudo ./scripts/fix_postgres_access.sh
./update.sh postgres
./healthcheck.sh postgres
```

The repair script allows the configured LAN CIDR and detected Docker bridge
subnets so the Flask container can reach the host PostgreSQL service.

From any LAN computer:

```text
http://<server-ip>:8000
```

## 10. Capacity For About 50 LAN Computers

The production compose file uses:

```text
WEB_CONCURRENCY=2
GUNICORN_THREADS=8
```

That gives 16 request-handling threads. If the server has enough CPU and memory and users are waiting during busy periods, raise workers gradually:

```text
WEB_CONCURRENCY=3
GUNICORN_THREADS=8
```

Then redeploy:

```bash
./deploy.sh postgres
```

Keep PostgreSQL on the same Ubuntu host unless you intentionally move it. The web container connects through `host.docker.internal`, while host-side maintenance scripts use `127.0.0.1`; keep `DB_SSL_MODE=disable` for this private/local path.
