# PBORA_FORM_13 Runtime Bundle

This folder was created from `/home/harold-coder/Downloads/form14-main__` as a deployable copy for the root `app.py` Flask application.

## Core files required by `app.py`

- `app.py`
- `models.py`
- `mergePBOrecords_2.py`
- `analysis_view.py`
- `analysis2_view.py`
- `data.py`
- `database_backup.py`
- `create_users.py`
- `templates/`
- `static/`
- `requirements.txt`
- `requirements2.0.txt`

## Runtime data and generated assets included

- `.env`
- `.env.production`
- `.env.production.example`
- `.env.render.postgres.example`
- `application_default_credentials.json`
- `client_secret_*.json`
- `instance/`
- `backups/`
- `db_bootstrap/`
- `form14.db`
- `form14.sqlite`
- `returnsform14_org_backup.sqlite`
- `returnsform14_org_backup.sqlite.bak_*`
- `field_help_intent_dataset.json`
- `form14_data_analysis_*.json`
- `static/extracted_xls/`
- `JPEG/`, `JPEG_NO_BG/`, `JPEG_NO_BG_OPENAI/`, `JPEG_NO_BG_OPENAI_RAW/`, `MEMI/`
- Supporting `.xlsx`, `.docx`, `.html`, `.jpg`, `.png`, `.wav`, `.sh`, `.md`, and helper script files from the root app folder

## Deployment files included

- `Dockerfile`
- `docker-compose.yml`
- `docker-compose.prod.yml`
- `docker-compose.prod.postgres.yml`
- `docker-entrypoint.sh`
- `render.yaml`
- `required.sh`
- `deploy.sh`
- `update.sh`
- `backup.sh`
- `restore.sh`
- `healthcheck.sh`
- `_deploy_common.sh`
- `.github/`

## Files intentionally not copied

- `.git/`
- `.gitignore`
- `.dockerignore`
- `.venv/`
- `.venv-1/`
- `.openai-image-venv/`
- `.pytest_cache/`
- `__pycache__/`
- `*.pyc`
- `.agents/`
- `.codex`
- `.sixth/`
- `.vscode/`
- nested duplicate `form14/`
- generated archive `form14.zip`

No `.gitignore` or `.dockerignore` was created in this bundle.

## Verification commands

From inside this folder:

```bash
python3 -m py_compile app.py models.py mergePBOrecords_2.py analysis_view.py analysis2_view.py data.py database_backup.py create_users.py
INTERNAL_DATABASE_URL=sqlite:///instance/form14.sqlite SQLITE_BOOTSTRAP_SOURCE_URL=sqlite:///returnsform14_org_backup.sqlite python3 -c "import app; print(app.app.name)"
python3 scripts/verify_pbora_postgres.py --env-file .env.production --init-app-tables
```

The second command uses the copied SQLite database so the import check does not depend on an external MySQL server.

## PBORA PostgreSQL configuration

The clone is configured for a PBORA PostgreSQL host whose LAN IP can change:

- App/LAN host: detected and written by `ip_address_change.py --render-runtime-env`
- LAN gateway: detected and written by `ip_address_change.py --render-runtime-env`
- PostgreSQL host from host shell scripts: `127.0.0.1`
- PostgreSQL host from the web container: `host.docker.internal`
- PostgreSQL port: `5432`
- Database: `pbora`
- User: `pbora`
- SQLAlchemy URL: `postgresql+psycopg2://pbora:pbora@host.docker.internal:5432/pbora`
- SSL mode: `disable`

`docker-compose.prod.postgres.yml` uses the external PostgreSQL database. It does not start a local `postgres:16` container.

For full LAN deployment steps, see `INSTRUCTIONS.md`.
