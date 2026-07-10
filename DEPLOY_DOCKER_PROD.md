# Docker Production Deployment

This is the shortest command-first runbook for deploying this app from a Windows Server 2012 environment by using an Ubuntu VM as the Docker host.

Recommended network model:
- Windows Server 2012 hosts the Ubuntu VM.
- The Ubuntu VM gets its own static LAN IP, for example `192.168.1.50`.
- Staff access the app at `http://192.168.1.50:8000`.

## 1. Ubuntu VM setup

Run these inside the Ubuntu VM.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
newgrp docker
docker version
docker compose version
```

## 2. Copy the app onto the VM

Create the app directory:

```bash
sudo mkdir -p /srv/form14
sudo chown -R $USER:$USER /srv/form14
cd /srv/form14
```

Copy the project files into `/srv/form14` using your preferred method:
- `git clone`
- WinSCP
- shared folder
- USB or ZIP copy

After copying, confirm these files exist:

```bash
cd /srv/form14
ls -la Dockerfile docker-compose.prod.yml docker-compose.prod.postgres.yml .env.production.example app.py requirements.txt docker-entrypoint.sh
```

## 3. Prepare persistent directories

```bash
cd /srv/form14
mkdir -p instance/uploads backups
```

## 4. Create the real production env file

```bash
cd /srv/form14
cp .env.production.example .env.production
nano .env.production
```

Edit at least these values:
- `ALLOWED_HOSTS`
- `SECRET_KEY`
- `ADMIN_USER_EMAIL`
- `ADMIN_USER_PASSWORD`
- `NOMINATIM_USER_AGENT`

If your VM IP is `192.168.1.50`, use:

```env
ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.50
HOST_PORT=8000
```

## 4A. One-command helper scripts

These helper scripts are included in the repo for the Ubuntu VM:
- `./required.sh`
- `./deploy.sh`
- `./update.sh`
- `./backup.sh`
- `./restore.sh`
- `./healthcheck.sh`

Make them executable once:

```bash
cd /srv/form14
chmod +x required.sh deploy.sh update.sh backup.sh restore.sh healthcheck.sh
```

One-command host setup plus first deployment:

```bash
cd /srv/form14
./required.sh postgres
```

or:

```bash
cd /srv/form14
./required.sh sqlite
```

`required.sh` installs missing Ubuntu packages, Docker, Docker Compose, the PostgreSQL client tools, prepares directories, makes the helper scripts executable, and deploys the app once `.env.production` is ready.

First deployment:

```bash
cd /srv/form14
./deploy.sh sqlite
```

Recommended multi-user deployment:

```bash
cd /srv/form14
./deploy.sh postgres
```

Later application updates:

```bash
cd /srv/form14
./update.sh sqlite
```

or:

```bash
cd /srv/form14
./update.sh postgres
```

If `.env.production` does not exist yet, `./deploy.sh` will create it from the example and stop so you can edit real values before continuing.

One-command backup:

```bash
cd /srv/form14
./backup.sh postgres
```

or:

```bash
cd /srv/form14
./backup.sh sqlite
```

One-command restore:

```bash
cd /srv/form14
./restore.sh postgres backups/<your-backup-file>.sql
```

or:

```bash
cd /srv/form14
./restore.sh sqlite backups/<your-backup-file>.db
```

One-command health verification:

```bash
cd /srv/form14
./healthcheck.sh postgres
```

or:

```bash
cd /srv/form14
./healthcheck.sh sqlite
```

## 5. Fastest deployment: SQLite

Use this if you want the quickest single-container deployment.

Start:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Initialize database:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml exec web flask init-db
```

Check status:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker logs form14_web --tail 100
curl http://127.0.0.1:8000
```

## 6. Recommended deployment: PostgreSQL

Use this if several users will be logging in and editing data.

Before starting, edit `.env.production` and set a real PostgreSQL password:

```env
POSTGRES_PASSWORD=replace-with-a-strong-db-password
```

If you want to force the web app to use that same DB URL explicitly, uncomment and set:

```env
# INTERNAL_DATABASE_URL=postgresql://form14:replace-with-a-strong-db-password@db:5432/form14?sslmode=disable
```

Start:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml up -d --build
```

Initialize database:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml exec web flask init-db
```

Check status:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml ps
docker logs form14_web --tail 100
docker logs form14_db --tail 100
curl http://127.0.0.1:8000
```

## 7. Open firewall on the Ubuntu VM

If `ufw` is enabled:

```bash
sudo ufw allow 8000/tcp
sudo ufw status
```

## 8. Access from other computers on the network

From any work computer on the same LAN, open:

```text
http://192.168.1.50:8000
```

Replace `192.168.1.50` with your actual Ubuntu VM IP.

## 9. Restart after reboot

Both compose files use `restart: unless-stopped`, so containers should come back automatically after Docker starts.

Manual restart commands:

SQLite:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
```

PostgreSQL:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml up -d
```

## 10. Stop the stack

SQLite:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml down
```

PostgreSQL:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml down
```

## 11. Update the app later

After copying updated source code into `/srv/form14`:

SQLite:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.yml exec web flask init-db
```

PostgreSQL:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml exec web flask init-db
```

## 12. Backup quick commands

SQLite backup:

```bash
cd /srv/form14
cp instance/form14.db backups/form14-$(date +%F-%H%M%S).db
```

PostgreSQL backup:

```bash
cd /srv/form14
docker exec form14_db pg_dump -U form14 form14 > backups/form14-$(date +%F-%H%M%S).sql
```

Uploads backup:

```bash
cd /srv/form14
tar -czf backups/uploads-$(date +%F-%H%M%S).tar.gz instance/uploads
```

## 13. Troubleshooting

Show running containers:

```bash
docker ps
```

Show full compose status:

SQLite:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

PostgreSQL:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml ps
```

Tail app logs:

```bash
docker logs -f form14_web
```

Tail database logs:

```bash
docker logs -f form14_db
```

Validate the compose file before startup:

SQLite:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.yml config
```

PostgreSQL:

```bash
cd /srv/form14
docker compose --env-file .env.production -f docker-compose.prod.postgres.yml config
```

## 14. Optional Windows host port forwarding

Use this only if users must browse to the Windows Server IP instead of the Ubuntu VM IP.

Run these on Windows Server 2012 as Administrator:

```powershell
netsh interface portproxy add v4tov4 listenaddress=<WINDOWS_HOST_IP> listenport=8000 connectaddress=192.168.1.50 connectport=8000
netsh advfirewall firewall add rule name="FORM14 Docker 8000" dir=in action=allow protocol=TCP localport=8000
```

Then users can browse to:

```text
http://<WINDOWS_HOST_IP>:8000
```

The VM IP method is simpler and is still the recommended option.





Added the final admin helper scripts:

backup.sh
restore.sh
healthcheck.sh
I also updated DEPLOY_DOCKER_PROD.md to include them, and I set execute permissions plus verified all three with bash -n.

Practical usage on the Ubuntu VM:

cd /srv/form14
chmod +x backup.sh restore.sh healthcheck.sh
./backup.sh postgres
./healthcheck.sh postgres
./restore.sh postgres backups/<your-backup-file>.sql
SQLite variants also work:

./backup.sh sqlite
./healthcheck.sh sqlite
./restore.sh sqlite backups/<your-backup-file>.db
One important caution: restore.sh is intentionally destructive. It prompts for confirmation unless you pass --yes. For PostgreSQL, it resets the public schema before importing the backup, so use it carefully and only with the correct backup file.



Added the one-click bootstrapper here: [required.sh](/home/harold-coder/Downloads/form14-main/required.sh).

What it does:
- installs missing Ubuntu/Debian host prerequisites
- installs Docker Engine and Docker Compose plugin if missing
- installs `postgresql-client` tools on the VM
- pre-pulls the `postgres:16` image for the containerized PostgreSQL deployment
- enables and starts Docker
- adds the current user to the `docker` group if needed
- creates `instance/uploads` and `backups`
- makes all helper scripts executable
- copies `.env.production.example` to `.env.production` if missing
- deploys the app automatically once `.env.production` is ready

Use it like this on the Ubuntu VM:

```bash
cd /srv/form14
./required.sh postgres
```

Or for SQLite:

```bash
cd /srv/form14
./required.sh sqlite
```

Important behavior:
- On the first run, if `.env.production` does not exist, it creates it from [.env.production.example](/home/harold-coder/Downloads/form14-main/.env.production.example#L1) and stops so you can edit real values.
- After editing `.env.production`, rerun `./required.sh postgres` and it will continue through deployment.

I also updated [DEPLOY_DOCKER_PROD.md](/home/harold-coder/Downloads/form14-main/DEPLOY_DOCKER_PROD.md) to include `required.sh`, and I set execute permission plus verified the script with `bash -n`.

Small clarification: this script does not install a separate host PostgreSQL server package for the app, because your production path uses the PostgreSQL Docker container from [docker-compose.prod.postgres.yml](/home/harold-coder/Downloads/form14-main/docker-compose.prod.postgres.yml#L1). It installs the VM-side necessities and prepares that PostgreSQL container path correctly.



Yes, this is now ready for a local-network deployment, with a few normal pre-go-live conditions.

What “ready” means here:
- the production compose files exist and validate
- the env template exists
- the deployment runbook exists
- the one-command helpers exist for setup, deploy, update, backup, restore, and healthcheck
- I fixed the last practical gap so the helper scripts can still work on a first-time VM even before the current shell fully picks up Docker group membership

Your main entry point on the Ubuntu VM is now:

```bash
cd /srv/form14
./required.sh postgres
```

What still must be done by you before real go-live:
- edit `.env.production` after the first `required.sh` run creates it
- replace `SECRET_KEY`
- replace `ADMIN_USER_EMAIL`
- replace `ADMIN_USER_PASSWORD`
- replace `POSTGRES_PASSWORD`
- set `ALLOWED_HOSTS` to the real VM IP or internal DNS name
- keep the VM on a static IP
- allow TCP `8000` on the Ubuntu VM firewall
- test from another PC on the same LAN

My recommendation:
- use `postgres`, not `sqlite`, for office/network use
- run `./healthcheck.sh postgres` after deployment
- run `./backup.sh postgres` before first real user rollout and before every later update
- take a VM snapshot before production use

Core files:
- [required.sh](/home/harold-coder/Downloads/form14-main/required.sh)
- [deploy.sh](/home/harold-coder/Downloads/form14-main/deploy.sh)
- [update.sh](/home/harold-coder/Downloads/form14-main/update.sh)
- [backup.sh](/home/harold-coder/Downloads/form14-main/backup.sh)
- [restore.sh](/home/harold-coder/Downloads/form14-main/restore.sh)
- [healthcheck.sh](/home/harold-coder/Downloads/form14-main/healthcheck.sh)
- [.env.production.example](/home/harold-coder/Downloads/form14-main/.env.production.example)
- [docker-compose.prod.postgres.yml](/home/harold-coder/Downloads/form14-main/docker-compose.prod.postgres.yml)
- [DEPLOY_DOCKER_PROD.md](/home/harold-coder/Downloads/form14-main/DEPLOY_DOCKER_PROD.md)

If you want, I can do one final polish pass and generate a single `GO_LIVE_CHECKLIST.docx` for the admin/operator.





Created the final operator doc here: [GO_LIVE_CHECKLIST.docx](/home/harold-coder/Downloads/form14-main/GO_LIVE_CHECKLIST.docx).

It’s focused on:
- pre-deployment readiness
- `.env.production` checks
- one-command deployment with `./required.sh postgres`
- immediate validation and LAN testing
- first-use testing
- backup and restore readiness
- post-go-live operations
- final sign-off

At this point you have a complete local-network deployment pack:
- [GO_LIVE_CHECKLIST.docx](/home/harold-coder/Downloads/form14-main/GO_LIVE_CHECKLIST.docx)
- [DEPLOY_DOCKER_PROD.md](/home/harold-coder/Downloads/form14-main/DEPLOY_DOCKER_PROD.md)
- [deployment keywords.docx](/home/harold-coder/Downloads/form14-main/deployment%20keywords.docx)
- [deployment_list_of_commands.docx](/home/harold-coder/Downloads/form14-main/deployment_list_of_commands.docx)
- [required.sh](/home/harold-coder/Downloads/form14-main/required.sh)
- [deploy.sh](/home/harold-coder/Downloads/form14-main/deploy.sh)
- [update.sh](/home/harold-coder/Downloads/form14-main/update.sh)
- [backup.sh](/home/harold-coder/Downloads/form14-main/backup.sh)
- [restore.sh](/home/harold-coder/Downloads/form14-main/restore.sh)
- [healthcheck.sh](/home/harold-coder/Downloads/form14-main/healthcheck.sh)

The practical next step on the Ubuntu VM is still:

```bash
cd /srv/form14
./required.sh postgres
```

Then edit `.env.production` if it gets created on first run, and rerun the same command.


Made the deployment pack materially better without changing your workflow.

What improved:
- Added a shared helper layer in [_deploy_common.sh](/home/harold-coder/Downloads/form14-main/_deploy_common.sh) so the scripts now behave consistently.
- Upgraded [required.sh](/home/harold-coder/Downloads/form14-main/required.sh), [deploy.sh](/home/harold-coder/Downloads/form14-main/deploy.sh), [update.sh](/home/harold-coder/Downloads/form14-main/update.sh), [backup.sh](/home/harold-coder/Downloads/form14-main/backup.sh), [restore.sh](/home/harold-coder/Downloads/form14-main/restore.sh), and [healthcheck.sh](/home/harold-coder/Downloads/form14-main/healthcheck.sh) with:
  - consistent logging and error handling
  - shared mode resolution for `sqlite` / `postgres`
  - shared `docker` / `sudo docker` fallback
  - shared env-file reading and compose validation
  - warnings if placeholder secrets are still in `.env.production`
  - readiness waiting for PostgreSQL and HTTP before continuing
  - automatic healthcheck after deploy/update
  - automatic safety backup before restore
  - LAN URL output after successful healthcheck/deploy

Practical result:
- `./required.sh postgres` is now safer and more operator-friendly.
- `./update.sh postgres` now verifies the app after updating.
- `./restore.sh postgres ...` now creates a pre-restore backup first.
- `./healthcheck.sh postgres` is a better real-world verification step.

I also ran a full shell syntax check on all of them:
- `_deploy_common.sh`
- `required.sh`
- `deploy.sh`
- `update.sh`
- `backup.sh`
- `restore.sh`
- `healthcheck.sh`

So yes, the deployment script pack is now noticeably stronger than before.
