import os
import sqlite3
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from dotenv import load_dotenv
from models import db as models_db

try:
    import google.auth
    from google.auth import load_credentials_from_file
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except Exception:  # pragma: no cover - optional until dependencies are installed
    google = None
    load_credentials_from_file = None
    InstalledAppFlow = None
    build = None
    MediaFileUpload = None


DEFAULT_GOOGLE_DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.file",)
BACKUP_BASENAME = "returnsform14_org_backup"
load_dotenv()


def utcnow():
    return datetime.now(timezone.utc)


def emit_progress(progress_callback=None, **payload):
    if progress_callback is None:
        return
    progress_callback(payload)


def build_backup_directory(backup_root: Path, timestamp: datetime) -> Path:
    folder_name = f"{timestamp.strftime('%A')}, {timestamp.strftime('%d--%m--%Y')}, {timestamp.strftime('%H-%M-%S')}"
    return backup_root / folder_name


def normalize_database_url(raw_url: str | None) -> str:
    if not raw_url:
        return "sqlite:///form14.sqlite"
    database_url = raw_url.strip()
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if database_url.startswith("postgresql://") and "sslmode=" not in database_url.lower():
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"
    return database_url


def is_production_environment() -> bool:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    render_flag = (os.getenv("RENDER") or "").strip().lower() in {"1", "true", "yes", "on"}
    flask_env_production = (os.getenv("FLASK_ENV_PRODUCTION") or "").strip().lower() in {"1", "true", "yes", "on"}
    return app_env == "production" or render_flag or flask_env_production or bool(render_hostname)


def resolve_backup_database_url(database_url: str | None = None) -> str:
    if database_url:
        return normalize_database_url(database_url)
    return normalize_database_url(os.getenv("INTERNAL_DATABASE_URL"))


def resolve_sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    raw_path = database_url.replace("sqlite:///", "", 1)
    path = Path(raw_path)
    return path if path.is_absolute() else Path.cwd() / path


def resolve_mysql_connection(database_url: str) -> dict[str, str | int] | None:
    normalized = str(database_url or "").strip().lower()
    if not normalized.startswith("mysql"):
        return None

    parsed = urlparse(database_url)
    database_name = (parsed.path or "").lstrip("/")
    if not database_name:
        raise RuntimeError("MySQL database URL is missing the database name.")

    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "username": parsed.username or "",
        "password": parsed.password or "",
        "database": database_name,
    }


def mysql_cli_env(password: str) -> dict[str, str]:
    command_env = os.environ.copy()
    if password:
        command_env["MYSQL_PWD"] = password
    return command_env


def _config_base_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_path(path_value: str | Path | None) -> Path | None:
    if path_value is None:
        return None
    text = str(path_value).strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (_config_base_dir() / path).resolve()
    return path


def _google_drive_scopes() -> tuple[str, ...]:
    scopes_text = os.getenv("GOOGLE_DRIVE_SCOPES", "").strip()
    if not scopes_text:
        return DEFAULT_GOOGLE_DRIVE_SCOPES
    scopes = tuple(scope.strip() for scope in scopes_text.split(",") if scope.strip())
    return scopes or DEFAULT_GOOGLE_DRIVE_SCOPES


def _inspect_credential_file(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"type": "unknown", "path": str(path)}
    return {
        "type": str(payload.get("type", "") or "unknown"),
        "path": str(path),
    }


def resolve_google_application_credentials_path(*, create_default: bool = False) -> Path | None:
    configured_path = _resolve_path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    if configured_path is not None:
        return configured_path
    default_path = _config_base_dir() / "application_default_credentials.json"
    if create_default or default_path.exists():
        return default_path
    return None


def resolve_google_drive_oauth_client_secret_path() -> Path | None:
    configured_path = _resolve_path(os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET_FILE"))
    if configured_path is not None:
        return configured_path
    matches = sorted(_config_base_dir().glob("client_secret_*.json"))
    return matches[0] if matches else None


def google_drive_credential_details() -> dict[str, str]:
    authorized_user_path = resolve_google_application_credentials_path()
    if authorized_user_path and authorized_user_path.exists():
        details = _inspect_credential_file(authorized_user_path)
        details["source"] = "GOOGLE_APPLICATION_CREDENTIALS"
        return details

    return {"type": "default", "source": "google.auth.default", "path": ""}


def describe_google_drive_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    lowered_message = message.lower()
    scopes = ", ".join(_google_drive_scopes())
    if "invalid_scope" in lowered_message:
        return (
            "Google Drive authorization is missing the configured Drive scope. "
            f"Re-create the credential with {scopes}, then retry the backup."
        )
    if "invalid_grant" in lowered_message:
        return (
            "Google Drive authorization is invalid for the configured OAuth credential. "
            "The current application_default_credentials.json is an authorized-user token and it usually fails this way "
            "when the refresh token expired, was revoked, or belongs to a different Google consent session. "
            "Reauthorize the credential and replace the JSON file used by GOOGLE_APPLICATION_CREDENTIALS."
        )
    return message


def is_google_drive_auth_error_message(message: str) -> bool:
    lowered_message = str(message or "").strip().lower()
    return "google drive authorization" in lowered_message


def google_drive_refresh_command() -> str:
    client_secret_path = resolve_google_drive_oauth_client_secret_path()
    if client_secret_path and client_secret_path.exists():
        return f"python3 {shlex.quote(str(Path(__file__).resolve()))} --reauthorize-google-drive"

    target_path = resolve_google_application_credentials_path(create_default=True)
    scopes = ",".join(
        [
            "https://www.googleapis.com/auth/cloud-platform",
            *_google_drive_scopes(),
        ]
    )
    return (
        f"gcloud auth application-default login --scopes={scopes} "
        f"&& cp ~/.config/gcloud/application_default_credentials.json {shlex.quote(str(target_path))}"
    )


def google_drive_auth_guidance(*, error_message: str = "") -> dict[str, str] | None:
    credential_details = google_drive_credential_details()
    credential_type = str(credential_details.get("type", "") or "").strip()
    if credential_type != "authorized_user":
        return None

    friendly_error = str(error_message or "").strip()
    if friendly_error and is_google_drive_auth_error_message(friendly_error):
        return {
            "title": "Google Drive credential needs refresh",
            "message": friendly_error,
            "refresh_command": google_drive_refresh_command(),
            "recommendation": (
                "Recreate the authorized-user OAuth token and write it to the file pointed to by "
                "GOOGLE_APPLICATION_CREDENTIALS."
            ),
        }

    return {
        "title": "Authorized-user credential detected",
        "message": (
            "This backup target is using application_default_credentials.json through "
            "GOOGLE_APPLICATION_CREDENTIALS."
        ),
        "refresh_command": google_drive_refresh_command(),
        "recommendation": (
            "Refresh the authorized-user OAuth token and keep GOOGLE_APPLICATION_CREDENTIALS pointed at that file."
        ),
    }


def build_drive_service():
    if build is None or MediaFileUpload is None:
        raise RuntimeError(
            "Google Drive libraries are not installed. Run `pip install -r requirements.txt` first."
        )

    creds = _build_google_drive_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _build_google_drive_credentials():
    scopes = list(_google_drive_scopes())
    authorized_user_path = resolve_google_application_credentials_path()
    if authorized_user_path and authorized_user_path.exists():
        if load_credentials_from_file is None:
            raise RuntimeError("Google Drive client libraries are not installed.")
        credentials, _ = load_credentials_from_file(str(authorized_user_path), scopes=scopes)
        return credentials

    if google is None:
        raise RuntimeError("Google Drive client libraries are not installed.")
    credentials, _ = google.auth.default(scopes=scopes)
    return credentials


def reauthorize_google_drive_credentials(*, open_browser: bool = True) -> Path:
    client_secret_path = resolve_google_drive_oauth_client_secret_path()
    if not client_secret_path or not client_secret_path.exists():
        raise RuntimeError(
            "Google OAuth client secret JSON was not found. Set GOOGLE_DRIVE_OAUTH_CLIENT_SECRET_FILE "
            "or place client_secret_*.json beside database_backup.py."
        )
    if InstalledAppFlow is None:
        raise RuntimeError("google-auth-oauthlib is not installed.")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=list(_google_drive_scopes()),
    )
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=open_browser,
        authorization_prompt_message="Open this URL in your browser to authorize Google Drive backup access: {url}",
        success_message="Google Drive backup authorization completed. You can close this window.",
    )
    target_path = resolve_google_application_credentials_path(create_default=True)
    if target_path is None:
        raise RuntimeError("Unable to resolve the authorized-user credential output path.")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(credentials.to_json(), encoding="utf-8")
    return target_path


def verify_google_drive_configuration() -> dict[str, Any]:
    service = build_drive_service()
    folder_id = (os.getenv("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    payload: dict[str, Any] = {
        "credential_details": google_drive_credential_details(),
        "folder_id": folder_id,
    }
    if folder_id:
        folder_meta = service.files().get(
            fileId=folder_id,
            fields="id,name",
            supportsAllDrives=True,
        ).execute()
        payload["folder"] = folder_meta
    return payload


def upload_to_drive(service, file_path: Path, folder_id: str | None):
    metadata = {"name": file_path.name}
    if folder_id:
        metadata["parents"] = [folder_id]
    media = MediaFileUpload(str(file_path), resumable=False)
    return service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()


def _strip_timezone_value(value):
    if isinstance(value, pd.Timestamp):
        return value.tz_localize(None) if value.tzinfo is not None else value
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    return value


def _serialize_sqlite_value(value):
    value = _strip_timezone_value(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return value


def normalize_excel_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized = dataframe.copy()

    for column_name in normalized.columns:
        series = normalized[column_name]

        if isinstance(series.dtype, pd.DatetimeTZDtype):
            normalized[column_name] = series.dt.tz_localize(None)
            continue

        if pd.api.types.is_object_dtype(series):
            normalized[column_name] = series.map(_strip_timezone_value)

    return normalized


def get_project_table_names() -> list[str]:
    return sorted(models_db.metadata.tables.keys())


def load_table_dataframe(connection, table_name: str) -> pd.DataFrame:
    table = Table(table_name, MetaData(), autoload_with=connection)
    return normalize_excel_dataframe(pd.read_sql(select(table), connection))


def export_relational_database_to_sqlite(
    database_url: str,
    backup_dir: Path,
    table_names: list[str],
    progress_callback=None,
) -> Path | None:
    if resolve_sqlite_path(database_url) is not None:
        return None

    sqlite_path = backup_dir / f"{BACKUP_BASENAME}.sqlite"
    engine = create_engine(database_url)
    sqlite_connection = sqlite3.connect(sqlite_path)

    try:
        with engine.connect() as connection:
            total_tables = len(table_names)
            for index, table_name in enumerate(table_names, start=1):
                emit_progress(
                    progress_callback,
                    stage="sqlite_mirror",
                    detail=f"Mirroring {table_name} to SQLite",
                    tables_completed=index - 1,
                    tables_total=total_tables,
                    current_table=table_name,
                )
                dataframe = load_table_dataframe(connection, table_name)
                for column_name in dataframe.columns:
                    if pd.api.types.is_object_dtype(dataframe[column_name]):
                        dataframe[column_name] = dataframe[column_name].map(_serialize_sqlite_value)
                dataframe.to_sql(table_name, sqlite_connection, if_exists="replace", index=False)
    finally:
        sqlite_connection.close()
        engine.dispose()

    return sqlite_path


def export_database_tables(database_url: str, backup_root: Path, progress_callback=None) -> list[Path]:
    engine = create_engine(database_url)
    inspector = inspect(engine)
    available_tables = set(inspector.get_table_names())
    table_names = [table_name for table_name in get_project_table_names() if table_name in available_tables]
    backup_timestamp = utcnow()
    backup_dir = build_backup_directory(backup_root, backup_timestamp)
    backup_dir.mkdir(parents=True, exist_ok=True)
    emit_progress(
        progress_callback,
        stage="prepare",
        detail=f"Preparing backup folder {backup_dir.name}",
        backup_dir=str(backup_dir),
        tables_total=len(table_names),
        tables_completed=0,
    )

    exported_files: list[Path] = []
    sqlite_path = resolve_sqlite_path(database_url)
    if sqlite_path and sqlite_path.exists():
        db_copy = backup_dir / f"{BACKUP_BASENAME}.sqlite"
        emit_progress(
            progress_callback,
            stage="sqlite_copy",
            detail=f"Copying SQLite database to {db_copy.name}",
            current_file=db_copy.name,
        )
        shutil.copy2(sqlite_path, db_copy)
        exported_files.append(db_copy)
    else:
        sqlite_mirror_path = export_relational_database_to_sqlite(
            database_url,
            backup_dir,
            table_names,
            progress_callback=progress_callback,
        )
        if sqlite_mirror_path is not None:
            exported_files.append(sqlite_mirror_path)

    manifest_rows = []
    try:
        with engine.connect() as connection:
            total_tables = len(table_names)
            for index, table_name in enumerate(table_names, start=1):
                emit_progress(
                    progress_callback,
                    stage="export_tables",
                    detail=f"Exporting table {table_name} to Excel",
                    tables_completed=index - 1,
                    tables_total=total_tables,
                    current_table=table_name,
                )
                dataframe = load_table_dataframe(connection, table_name)
                output_path = backup_dir / f"{table_name}.xlsx"
                dataframe.to_excel(output_path, index=False)
                exported_files.append(output_path)
                manifest_rows.append({
                    "table_name": table_name,
                    "row_count": len(dataframe),
                    "column_count": len(dataframe.columns),
                    "exported_file": output_path.name,
                })
    finally:
        engine.dispose()

    manifest_dataframe = normalize_excel_dataframe(pd.DataFrame(manifest_rows))
    manifest_path = backup_dir / f"{BACKUP_BASENAME}_manifest.xlsx"
    emit_progress(
        progress_callback,
        stage="manifest",
        detail=f"Writing manifest {manifest_path.name}",
        current_file=manifest_path.name,
        tables_completed=len(table_names),
        tables_total=len(table_names),
    )
    manifest_dataframe.to_excel(manifest_path, index=False)
    exported_files.append(manifest_path)
    return exported_files


def create_postgres_sql_dump(database_url: str, backup_dir: Path, progress_callback=None) -> Path | None:
    if not database_url.startswith("postgresql://"):
        return None
    dump_path = backup_dir / f"{BACKUP_BASENAME}.dump"
    emit_progress(
        progress_callback,
        stage="pg_dump",
        detail=f"Creating Postgres dump {dump_path.name}",
        current_file=dump_path.name,
    )
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(dump_path), database_url],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return dump_path


def create_mysql_sql_dump(database_url: str, backup_dir: Path, progress_callback=None) -> Path | None:
    mysql_config = resolve_mysql_connection(database_url)
    if mysql_config is None:
        return None

    dump_path = backup_dir / f"{BACKUP_BASENAME}.mysql.sql"
    emit_progress(
        progress_callback,
        stage="mysqldump",
        detail=f"Creating MySQL dump {dump_path.name}",
        current_file=dump_path.name,
    )
    with dump_path.open("wb") as dump_file:
        process = subprocess.run(
            [
                "mysqldump",
                "--host",
                str(mysql_config["host"]),
                "--port",
                str(mysql_config["port"]),
                "--user",
                str(mysql_config["username"]),
                "--single-transaction",
                "--skip-lock-tables",
                "--routines",
                "--events",
                "--default-character-set=utf8mb4",
                str(mysql_config["database"]),
            ],
            stdout=dump_file,
            stderr=subprocess.PIPE,
            env=mysql_cli_env(str(mysql_config["password"])),
        )
    if process.returncode != 0:
        dump_path.unlink(missing_ok=True)
        return None
    return dump_path


def create_backup_archive(backup_dir: Path, progress_callback=None) -> Path:
    archive_path = backup_dir / f"{BACKUP_BASENAME}.zip"
    emit_progress(
        progress_callback,
        stage="archive",
        detail=f"Creating archive {archive_path.name}",
        current_file=archive_path.name,
    )
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for file_path in backup_dir.iterdir():
            if file_path.is_file() and file_path.name != archive_path.name:
                bundle.write(file_path, arcname=file_path.name)
    return archive_path


def perform_backup_cycle(database_url: str | None = None, backup_root: str | Path | None = None, progress_callback=None):
    resolved_url = resolve_backup_database_url(database_url)
    resolved_root = Path(backup_root or os.getenv("DB_BACKUP_DIR", "./backups"))
    files = export_database_tables(resolved_url, resolved_root, progress_callback=progress_callback)
    backup_dir = max((path.parent for path in files), key=lambda path: path.stat().st_mtime)
    dump_path = create_postgres_sql_dump(resolved_url, backup_dir, progress_callback=progress_callback)
    if dump_path:
        files.append(dump_path)
    mysql_dump_path = create_mysql_sql_dump(resolved_url, backup_dir, progress_callback=progress_callback)
    if mysql_dump_path:
        files.append(mysql_dump_path)
    archive_path = create_backup_archive(backup_dir, progress_callback=progress_callback)
    files.append(archive_path)
    emit_progress(
        progress_callback,
        stage="local_complete",
        detail=f"Created {len(files)} local backup files",
        files_total=len(files),
        files_completed=0,
        backup_dir=str(backup_dir),
        archive_path=str(archive_path),
    )
    return {
        "database_url": resolved_url,
        "backup_dir": backup_dir,
        "files": files,
        "archive_path": archive_path,
        "dump_path": dump_path,
        "mysql_dump_path": mysql_dump_path,
    }


def run_backup_once(database_url: str | None = None, progress_callback=None):
    resolved_database_url = resolve_backup_database_url(database_url)
    drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    credential_details = google_drive_credential_details()
    guidance = google_drive_auth_guidance()

    emit_progress(
        progress_callback,
        stage="starting",
        detail="Starting backup job",
        drive_credential_type=credential_details.get("type"),
        drive_credential_source=credential_details.get("source"),
        drive_refresh_command=(guidance or {}).get("refresh_command"),
        drive_auth_recommendation=(guidance or {}).get("recommendation"),
    )
    result = perform_backup_cycle(
        database_url=resolved_database_url,
        backup_root=os.getenv("DB_BACKUP_DIR", "./backups"),
        progress_callback=progress_callback,
    )
    files = result["files"]
    print(f"Created {len(files)} backup files in {result['backup_dir'].parent.resolve()}")
    result["local_backup_succeeded"] = True
    result["drive_upload_succeeded"] = False
    result["drive_upload_error"] = None
    result["drive_upload_guidance"] = guidance
    result["drive_refresh_command"] = (guidance or {}).get("refresh_command")
    result["drive_credential_details"] = credential_details

    if os.getenv("GOOGLE_DRIVE_DISABLE_UPLOAD", "0") == "1":
        print("Google Drive upload disabled by GOOGLE_DRIVE_DISABLE_UPLOAD=1")
        emit_progress(
            progress_callback,
            stage="complete",
            detail="Backup finished with local files only",
            drive_credential_type=credential_details.get("type"),
            drive_credential_source=credential_details.get("source"),
            drive_refresh_command=(guidance or {}).get("refresh_command"),
            drive_auth_recommendation=(guidance or {}).get("recommendation"),
        )
        return result

    try:
        service = build_drive_service()
        total_files = len(files)
        for index, file_path in enumerate(files, start=1):
            emit_progress(
                progress_callback,
                stage="uploading",
                detail=f"Uploading {file_path.name} to Google Drive",
                current_file=file_path.name,
                files_completed=index - 1,
                files_total=total_files,
            )
            uploaded_file = upload_to_drive(service, file_path, drive_folder_id)
            print(f"Uploaded {file_path.name} to Google Drive")
            emit_progress(
                progress_callback,
                stage="uploading",
                detail=f"Uploaded {file_path.name} to Google Drive",
                current_file=file_path.name,
                files_completed=index,
                files_total=total_files,
                latest_drive_upload=uploaded_file,
            )
        result["drive_upload_succeeded"] = True
        emit_progress(
            progress_callback,
            stage="complete",
            detail="Backup finished and uploaded to Google Drive",
            drive_credential_type=credential_details.get("type"),
            drive_credential_source=credential_details.get("source"),
            drive_refresh_command=None,
            drive_auth_recommendation=None,
        )
    except Exception as exc:
        friendly_error = describe_google_drive_error(exc)
        guidance = google_drive_auth_guidance(error_message=friendly_error)
        result["drive_upload_error"] = friendly_error
        result["drive_upload_guidance"] = guidance
        result["drive_refresh_command"] = (guidance or {}).get("refresh_command")
        suppress_traceback = os.getenv("GOOGLE_DRIVE_SUPPRESS_TRACEBACK", "1") == "1"
        expected_network_error_markers = (
            "temporary failure in name resolution",
            "unable to find the server",
            "could not resolve host",
            "transporterror",
            "name or service not known",
        )
        error_text = str(exc).lower()
        is_expected_network_error = any(marker in error_text for marker in expected_network_error_markers)
        if not (suppress_traceback and is_expected_network_error):
            print(
                f"Local backup succeeded, but Google Drive upload failed: {friendly_error}",
                file=sys.stderr,
            )
        if not suppress_traceback and not is_expected_network_error:
            print(traceback.format_exc(), file=sys.stderr)
        emit_progress(
            progress_callback,
            stage="upload_failed",
            detail="Local backup succeeded, but Google Drive upload failed",
            error=friendly_error,
            drive_credential_type=credential_details.get("type"),
            drive_credential_source=credential_details.get("source"),
            drive_refresh_command=(guidance or {}).get("refresh_command"),
            drive_auth_recommendation=(guidance or {}).get("recommendation"),
        )

    return result


def main():
    interval_seconds = int(os.getenv("DB_BACKUP_INTERVAL_SECONDS", "3600"))
    run_once = "--once" in sys.argv
    reauthorize_drive = "--reauthorize-google-drive" in sys.argv
    check_drive_auth = "--check-google-drive-auth" in sys.argv
    open_browser = "--no-browser" not in sys.argv

    if reauthorize_drive:
        saved_path = reauthorize_google_drive_credentials(open_browser=open_browser)
        print(f"Saved refreshed Google Drive credential to {saved_path}")
        return

    if check_drive_auth:
        try:
            print(json.dumps(verify_google_drive_configuration(), indent=2))
            return
        except Exception as exc:
            guidance = google_drive_auth_guidance(error_message=describe_google_drive_error(exc)) or {}
            print(
                json.dumps(
                    {
                        "error": describe_google_drive_error(exc),
                        "credential_details": google_drive_credential_details(),
                        "refresh_command": guidance.get("refresh_command"),
                        "recommendation": guidance.get("recommendation"),
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

    if run_once:
        run_backup_once()
        return

    while True:
        started_at = utcnow().isoformat(timespec="seconds")
        try:
            print(f"[{started_at}] Running database backup")
            run_backup_once()
        except Exception as exc:
            print(f"[{started_at}] Backup failed: {exc}", file=sys.stderr)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
