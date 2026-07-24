import argparse
import hashlib
import json
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text


def normalized_database_url() -> str:
    url = (
        os.environ.get("INTERNAL_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("PBORA_DATABASE_URL")
        or ""
    ).strip()
    if not url:
        raise SystemExit("No database URL is configured in the container environment.")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    if url.startswith("postgresql://") or url.startswith("postgresql+"):
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        existing_keys = {key.lower() for key in query_params}
        if "sslmode" not in existing_keys:
            query_params["sslmode"] = [
                os.environ.get("DB_SSL_MODE")
                or os.environ.get("POSTGRES_SSL_MODE")
                or "disable"
            ]
            url = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(query_params, doseq=True),
                    parsed.fragment,
                )
            )

    return url


def serialize_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, sort_keys=True)
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            value = value.tz_localize(None)
        return value.isoformat(sep=" ")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def normalize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized = dataframe.copy()
    for column_name in normalized.columns:
        series = normalized[column_name]
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            normalized[column_name] = series.dt.tz_localize(None).astype(str)
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            normalized[column_name] = series.astype(str)
            continue
        if pd.api.types.is_object_dtype(series):
            normalized[column_name] = series.map(serialize_value)
    return normalized


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def database_state_fingerprint() -> dict:
    database_url = normalized_database_url()
    is_postgresql = database_url.startswith("postgresql://") or database_url.startswith("postgresql+")
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            table_states = []
            for table_name in sorted(inspector.get_table_names()):
                columns = [column["name"] for column in inspector.get_columns(table_name)]
                qualified_table = quote_identifier(table_name)

                if is_postgresql:
                    row = connection.execute(
                        text(
                            "SELECT COUNT(*)::bigint AS row_count, "
                            "COALESCE(MAX((xmin::text)::bigint), 0)::bigint AS max_row_xmin "
                            f"FROM {qualified_table}"
                        )
                    ).mappings().one()
                    table_states.append(
                        {
                            "table": table_name,
                            "columns": columns,
                            "row_count": int(row["row_count"] or 0),
                            "max_row_xmin": int(row["max_row_xmin"] or 0),
                        }
                    )
                    continue

                timestamp_columns = [
                    column_name
                    for column_name in columns
                    if column_name.lower() in {"updated_at", "created_at", "modified_at", "last_modified_at"}
                    or column_name.lower().endswith("_updated_at")
                ]
                value_columns = ["COUNT(*) AS row_count"]
                if "id" in columns:
                    value_columns.append(f"MAX({quote_identifier('id')}) AS max_id")
                for column_name in timestamp_columns[:3]:
                    value_columns.append(f"MAX({quote_identifier(column_name)}) AS max_{column_name}")

                row = connection.execute(
                    text(f"SELECT {', '.join(value_columns)} FROM {qualified_table}")
                ).mappings().one()
                table_states.append(
                    {
                        "table": table_name,
                        "columns": columns,
                        **{key: str(value) for key, value in row.items()},
                    }
                )
    finally:
        engine.dispose()

    stable_payload = {"tables": table_states}
    fingerprint = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "fingerprint": fingerprint,
        "table_count": len(table_states),
        "tables": table_states,
    }


def export_database(target: Path) -> None:
    target.unlink(missing_ok=True)

    database_url = normalized_database_url()
    is_postgresql = database_url.startswith("postgresql://") or database_url.startswith("postgresql+")
    engine = create_engine(database_url, pool_pre_ping=True)
    sqlite_connection = sqlite3.connect(target)

    try:
        sqlite_connection.execute("PRAGMA journal_mode=OFF")
        sqlite_connection.execute("PRAGMA synchronous=OFF")
        with engine.connect() as source_connection:
            transaction = source_connection.begin()
            try:
                if is_postgresql:
                    source_connection.execute(text("SET TRANSACTION READ ONLY"))

                inspector = inspect(source_connection)
                table_names = sorted(inspector.get_table_names())
                print(f"exporting_tables={len(table_names)}", flush=True)

                for table_name in table_names:
                    table = Table(table_name, MetaData(), autoload_with=source_connection)
                    row_count = 0
                    wrote_table = False
                    for chunk in pd.read_sql(select(table), source_connection, chunksize=5000):
                        chunk = normalize_dataframe(chunk)
                        chunk.to_sql(
                            table_name,
                            sqlite_connection,
                            if_exists="replace" if not wrote_table else "append",
                            index=False,
                        )
                        row_count += len(chunk)
                        wrote_table = True

                    if not wrote_table:
                        columns = [column["name"] for column in inspector.get_columns(table_name)]
                        pd.DataFrame(columns=columns).to_sql(
                            table_name,
                            sqlite_connection,
                            if_exists="replace",
                            index=False,
                        )

                    print(f"{table_name}\t{row_count}", flush=True)
            except Exception:
                transaction.rollback()
                raise
            else:
                transaction.commit()

        sqlite_connection.commit()
        integrity = sqlite_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"SQLite integrity check failed: {integrity}")
        print(f"integrity_check={integrity}", flush=True)
    finally:
        sqlite_connection.close()
        engine.dispose()

    print(f"target={target}", flush=True)
    print(f"bytes={target.stat().st_size}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or fingerprint the configured live database.")
    parser.add_argument("--fingerprint", action="store_true", help="Print a JSON database row-change fingerprint.")
    args = parser.parse_args()

    if args.fingerprint:
        print(json.dumps(database_state_fingerprint(), sort_keys=True))
        return

    target_text = os.environ.get("TARGET_SQLITE", "").strip()
    if not target_text:
        raise SystemExit("TARGET_SQLITE is required.")
    export_database(Path(target_text))


if __name__ == "__main__":
    main()
