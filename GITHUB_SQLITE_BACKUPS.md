# GitHub SQLite Backup Automation

This change adds an automated backup path for the live Form 13/14 database.
After deployment, the server exports the live database into a timestamped SQLite
file in the repository root, verifies it, commits only that backup file, and
pushes it to GitHub.

## Active Behavior

- Backup files are created in the repo root with this format:
  `backup_DD_MM_YYYY_HH-MM-SS.sqlite`
- The first backup check runs after deployment, after the app healthcheck passes.
- A watcher cron job checks for row changes every minute.
- A fallback cron job checks every 4 hours.
- If the live database fingerprint matches the last successfully pushed backup,
  no SQLite file is exported and no Git commit is made.
- When row changes are detected, a backup is committed and pushed to
  `origin/main`.
- The export reads from the live database using a read-only PostgreSQL
  transaction and writes to a temporary SQLite file before copying it into the
  repo root.
- The app keeps serving users while the backup runs.
- Backup failures do not stop or roll back deployment.

## Files Added or Updated

- `scripts/export_live_db_to_sqlite.py`
  Fingerprints the live database row state, exports live tables into a SQLite
  file, and checks SQLite integrity.
- `scripts/github_sqlite_backup.sh`
  Orchestrates export, verification, Git commit, Git push, cron installation,
  row-change skipping, and one-shot backup runs.
- `_deploy_common.sh`
  Installs the backup scheduler and runs the first post-deploy backup from the
  standard deploy/update flow.
- `deploy.sh` and `update.sh`
  Call the backup scheduler after the app healthcheck succeeds.
- `one_command_deploy.sh`
  Applies the same post-deploy backup behavior for the one-command deploy path.
- `.gitignore`
  Ignores `logs/`, where backup logs and PID files are written.

## Current Schedule

Cron entries:

```cron
* * * * * cd /home/pbora/form13_returns && /home/pbora/form13_returns/scripts/github_sqlite_backup.sh --once >> /home/pbora/form13_returns/logs/github_sqlite_backup.log 2>&1
0 */4 * * * cd /home/pbora/form13_returns && /home/pbora/form13_returns/scripts/github_sqlite_backup.sh --once >> /home/pbora/form13_returns/logs/github_sqlite_backup.log 2>&1
```

The minute-level watcher starts a backup soon after a submitted record changes
the database. The 4-hour entry is a fallback check that keeps verifying the live
database state even during quiet periods. Both commands skip backup creation
when the database fingerprint has not changed.

## Manual Commands

Run one backup immediately:

```bash
./scripts/github_sqlite_backup.sh --once
```

Install or refresh the cron schedule:

```bash
./scripts/github_sqlite_backup.sh --install-cron
```

Check the installed schedule:

```bash
crontab -l | grep github_sqlite_backup
```

Watch backup logs:

```bash
tail -f logs/github_sqlite_backup.log
```

## Configuration

The deployment helper reads these optional `.env.production` values:

```bash
GITHUB_SQLITE_BACKUP_ENABLED=1
GITHUB_SQLITE_BACKUP_CONTAINER_NAME=form14_web
GITHUB_SQLITE_BACKUP_REMOTE=origin
GITHUB_SQLITE_BACKUP_BRANCH=main
GITHUB_SQLITE_BACKUP_PREFIX=backup
GITHUB_SQLITE_BACKUP_CRON_SCHEDULE="0 */4 * * *"
GITHUB_SQLITE_BACKUP_WATCH_CRON_SCHEDULE="* * * * *"
GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES=1
```

Set `GITHUB_SQLITE_BACKUP_ENABLED=0` to skip scheduler installation and the
first post-deploy backup.

## Safety Notes

- The backup script checks that the Git index is clean before staging a backup.
- It always stages the generated SQLite backup file when a database change is
  detected.
- With `GITHUB_SQLITE_BACKUP_INCLUDE_CHANGED_FILES=1`, it also stages other
  changed files, excluding sensitive/runtime paths such as `.env*`, `logs/`,
  `backups/`, `instance/`, certificates, and credential JSON files.
- It refuses to start if files are already staged before the backup job begins.
- It verifies `PRAGMA integrity_check` before committing.
- Runtime logs and the local database fingerprint state stay under `logs/` and
  are not tracked by Git.

## Verification From Deployment

On July 23, 2026, the deployed update completed successfully:

- App healthcheck passed for `https://returnsform14.org`.
- Cron was installed for the minute-level watcher and four-hour fallback checks.
- First post-deploy backup was created, verified, committed, and pushed:
  `backup_23_07_2026_13-54-08.sqlite`.
