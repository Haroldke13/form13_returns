#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


DEFAULT_DATABASE_URL = "postgresql+psycopg2://pbora:pbora@10.107.20.200:5432/pbora"


def redact_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    if not parsed.hostname:
        return database_url
    username = parsed.username or ""
    auth = username
    if parsed.password is not None:
        auth = f"{auth}:***" if auth else "***"
    netloc = f"{auth}@{parsed.hostname}" if auth else parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def with_default_sslmode(database_url: str) -> str:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgresql"):
        return database_url
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "sslmode" not in {key.lower() for key in query}:
        query["sslmode"] = [os.getenv("DB_SSL_MODE") or os.getenv("POSTGRES_SSL_MODE") or "disable"]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query, doseq=True), parsed.fragment))


def database_url_from_env() -> str:
    if os.getenv("INTERNAL_DATABASE_URL"):
        return os.environ["INTERNAL_DATABASE_URL"]
    if os.getenv("PBORA_DATABASE_URL"):
        return os.environ["PBORA_DATABASE_URL"]

    host = os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST") or "10.107.20.200"
    port = os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT") or "5432"
    name = os.getenv("DB_NAME") or os.getenv("POSTGRES_DB") or "pbora"
    user = os.getenv("DB_USER") or os.getenv("POSTGRES_USER") or "pbora"
    password = os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or "pbora"
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def connect_and_check(database_url: str):
    engine = create_engine(
        with_default_sslmode(database_url),
        connect_args={"connect_timeout": int(os.getenv("SQLALCHEMY_CONNECT_TIMEOUT", "5"))},
        pool_pre_ping=True,
    )
    with engine.begin() as connection:
        row = connection.execute(
            text("select current_database(), current_user, inet_server_addr()::text, inet_server_port()")
        ).one()
        connection.execute(text("create temporary table pbora_connection_smoke_test (id integer primary key)"))
        connection.execute(text("insert into pbora_connection_smoke_test (id) values (1)"))
        smoke_count = connection.execute(text("select count(*) from pbora_connection_smoke_test")).scalar_one()
    return engine, row, smoke_count


def init_app_tables(env_file: Path):
    load_dotenv(env_file, override=False)
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    previous_skip_schema = os.environ.get("SKIP_SCHEMA_CHECK")
    os.environ["SKIP_SCHEMA_CHECK"] = "1"
    import app as form14_app
    if previous_skip_schema is None:
        os.environ.pop("SKIP_SCHEMA_CHECK", None)
    else:
        os.environ["SKIP_SCHEMA_CHECK"] = previous_skip_schema

    with form14_app.app.app_context():
        form14_app.db.create_all()
        form14_app.db.session.commit()
        form14_app.db.session.remove()
        inspector = inspect(form14_app.db.engine)
        return inspector.get_table_names()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PBORA PostgreSQL connectivity and app table creation.")
    parser.add_argument("--env-file", default=".env.production", help="Environment file to load before testing.")
    parser.add_argument("--url", default=None, help="Override database URL for this check.")
    parser.add_argument("--init-app-tables", action="store_true", help="Run db.create_all() and verify app tables.")
    args = parser.parse_args()

    env_file = Path(args.env_file)
    if env_file.exists():
        load_dotenv(env_file, override=False)
    elif args.env_file:
        print(f"env_file_missing={env_file}")

    database_url = args.url or database_url_from_env() or DEFAULT_DATABASE_URL
    print(f"database_url={redact_database_url(with_default_sslmode(database_url))}")
    engine, row, smoke_count = connect_and_check(database_url)
    print(f"connection=ok database={row[0]} user={row[1]} server={row[2]} port={row[3]}")
    print(f"temporary_table_create=ok rows={smoke_count}")

    if args.init_app_tables:
        table_names = init_app_tables(env_file)
        print(f"app_table_init=ok table_count={len(table_names)}")
        print("sample_tables=" + ",".join(table_names[:10]))

    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
