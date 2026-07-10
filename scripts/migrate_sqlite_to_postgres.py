#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from sqlalchemy import MetaData, Table, create_engine, inspect, literal_column, select, text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PASSWORD = "field.12345"
DEFERRED_FK_COLUMNS = {
    "users_for_form14": ("authorized_by_id", "report_id"),
}
FIRST_TABLES = ("users_for_form14", "import_batches", "pbo_reports")
MIGRATION_MARKER_KEY = "sqlite_postgres_migration_completed"


def load_form14_app(env_file: Path):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    if env_file.exists():
        load_dotenv(env_file, override=True)
    os.environ["SKIP_SCHEMA_CHECK"] = "1"
    import app as form14_app

    return form14_app


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


def app_tables(form14_app):
    return [
        table
        for table in form14_app.db.metadata.tables.values()
        if not table.name.startswith("sqlite_")
    ]


def ordered_target_tables(form14_app, source_table_names: set[str]):
    tables_by_name = {
        table.name: table
        for table in app_tables(form14_app)
        if table.name in source_table_names
    }
    ordered = [tables_by_name[name] for name in FIRST_TABLES if name in tables_by_name]
    ordered.extend(
        table
        for table in app_tables(form14_app)
        if table.name in tables_by_name and table.name not in FIRST_TABLES
    )
    return ordered


def quote_table(connection, table_name: str) -> str:
    return connection.dialect.identifier_preparer.quote(table_name)


def count_rows(connection, table_name: str) -> int:
    quoted = quote_table(connection, table_name)
    return connection.execute(text(f"select count(*) from {quoted}")).scalar_one()


def target_row_counts(connection, tables):
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    counts = {}
    for table in tables:
        if table.name in existing:
            counts[table.name] = count_rows(connection, table.name)
    return counts


def ensure_target_empty_or_replace(connection, tables, replace_existing: bool):
    counts = target_row_counts(connection, tables)
    nonempty = {name: count for name, count in counts.items() if count}
    if not nonempty:
        return {"replaced": False, "previous_rows": counts}
    if not replace_existing:
        raise RuntimeError(
            "Target PostgreSQL database already has rows. "
            "Rerun with --replace-existing if this is the intended migration target. "
            f"Non-empty tables: {nonempty}"
        )
    names = ", ".join(quote_table(connection, table.name) for table in tables if table.name in counts)
    connection.execute(text(f"truncate table {names} restart identity cascade"))
    return {"replaced": True, "previous_rows": counts}


def source_select_for_import(form14_app, source_connection, source_table, target_table):
    return form14_app._source_select_for_import(
        source_connection,
        source_table,
        target_table,
    )


def insert_table_rows(
    form14_app,
    source_connection,
    target_connection,
    target_table,
    batch_size: int,
    deferred_updates: dict[str, list[dict]],
) -> int:
    source_table = Table(target_table.name, MetaData(), autoload_with=source_connection)
    statement, target_columns, use_sqlite_rowid_as_id, source_rowid_label = source_select_for_import(
        form14_app,
        source_connection,
        source_table,
        target_table,
    )
    if not target_columns:
        return 0

    deferred_columns = tuple(
        column
        for column in DEFERRED_FK_COLUMNS.get(target_table.name, ())
        if column in target_columns
    )
    inserted = 0
    batch = []
    for row in source_connection.execute(statement).mappings():
        payload = {column_name: row.get(column_name) for column_name in target_columns}
        if (
            use_sqlite_rowid_as_id
            and payload.get("id") in (None, "")
            and row.get(source_rowid_label) is not None
        ):
            payload["id"] = int(row.get(source_rowid_label))

        deferred_payload = {"id": payload.get("id")}
        for column_name in deferred_columns:
            deferred_payload[column_name] = payload.get(column_name)
            payload[column_name] = None
        if deferred_columns and deferred_payload.get("id") is not None:
            deferred_updates.setdefault(target_table.name, []).append(deferred_payload)

        batch.append(payload)
        if len(batch) >= batch_size:
            target_connection.execute(target_table.insert(), batch)
            inserted += len(batch)
            batch = []

    if batch:
        target_connection.execute(target_table.insert(), batch)
        inserted += len(batch)
    return inserted


def restore_deferred_fk_columns(connection, deferred_updates: dict[str, list[dict]]) -> int:
    restored = 0
    for table_name, rows in deferred_updates.items():
        for row in rows:
            row_id = row.get("id")
            if row_id is None:
                continue
            assignments = {
                column_name: value
                for column_name, value in row.items()
                if column_name != "id" and value is not None
            }
            if not assignments:
                continue
            set_clause = ", ".join(f"{column_name} = :{column_name}" for column_name in assignments)
            params = dict(assignments)
            params["id"] = row_id
            connection.execute(
                text(f"update {quote_table(connection, table_name)} set {set_clause} where id = :id"),
                params,
            )
            restored += 1
    return restored


def reset_postgres_sequences(connection, tables) -> list[str]:
    reset_tables = []
    for table in tables:
        if "id" not in table.c:
            continue
        sequence_name = connection.execute(
            text("select pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table.name, "column_name": "id"},
        ).scalar()
        if not sequence_name:
            continue
        quoted = quote_table(connection, table.name)
        connection.execute(
            text(
                f"""
                select setval(
                    :sequence_name,
                    coalesce((select max(id) from {quoted}), 1),
                    (select count(*) > 0 from {quoted})
                )
                """
            ),
            {"sequence_name": sequence_name},
        )
        reset_tables.append(table.name)
    return reset_tables


def record_marker(connection, source_path: Path, imported_rows: dict[str, int], password_reset_count: int | None):
    payload = json.dumps(
        {
            "source_path": str(source_path.resolve()),
            "imported_rows": {key: value for key, value in imported_rows.items() if value},
            "password_reset_count": password_reset_count,
        },
        sort_keys=True,
    )
    connection.execute(
        text(
            """
            insert into admin_settings (key, value, updated_by_id, updated_at)
            values (:key, :value, null, now())
            on conflict (key)
            do update set value = excluded.value, updated_by_id = null, updated_at = now()
            """
        ),
        {"key": MIGRATION_MARKER_KEY, "value": payload},
    )


def import_sqlite(form14_app, source_path: Path, replace_existing: bool, batch_size: int):
    if form14_app.db.engine.dialect.name != "postgresql":
        raise RuntimeError(f"Target database must be PostgreSQL, got {form14_app.db.engine.dialect.name!r}.")
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    source_engine = create_engine(sqlite_url(source_path))
    try:
        with source_engine.connect() as source_connection:
            source_table_names = set(inspect(source_connection).get_table_names())
            tables = ordered_target_tables(form14_app, source_table_names)
            if not tables:
                raise RuntimeError(f"No app tables from {source_path} match the target metadata.")
            deferred_updates: dict[str, list[dict]] = {}
            imported_rows: dict[str, int] = {}
            with form14_app.db.engine.begin() as target_connection:
                replace_result = ensure_target_empty_or_replace(target_connection, tables, replace_existing)
                for target_table in tables:
                    imported_rows[target_table.name] = insert_table_rows(
                        form14_app,
                        source_connection,
                        target_connection,
                        target_table,
                        batch_size,
                        deferred_updates,
                    )
                restored_deferred_fk_rows = restore_deferred_fk_columns(target_connection, deferred_updates)
                reset_sequences = reset_postgres_sequences(target_connection, tables)
            return {
                "source_path": str(source_path.resolve()),
                "replace_result": replace_result,
                "imported_rows": imported_rows,
                "imported_row_total": sum(imported_rows.values()),
                "imported_table_count": sum(1 for value in imported_rows.values() if value),
                "restored_deferred_fk_rows": restored_deferred_fk_rows,
                "reset_sequences": reset_sequences,
            }
    finally:
        source_engine.dispose()


def sync_env_users(form14_app, env_file: Path):
    import create_users

    seed_source = {
        key: value
        for key, value in dotenv_values(env_file).items()
        if value is not None
    }
    return create_users.seed_users_from_runtime(
        seed_source=seed_source,
        initialize_schema=False,
        app_instance=form14_app.app,
        initialize_database_fn=form14_app.initialize_database,
    )


def reset_all_user_passwords(form14_app, password: str) -> int:
    from models import User, db

    changed = 0
    for user in User.query.order_by(User.id).all():
        if not user.password_hash or not user.check_password(password):
            user.set_password(password, mark_changed=True)
            changed += 1
        if user.must_change_password:
            user.must_change_password = False
        if user.failed_login_attempts:
            user.failed_login_attempts = 0
            user.last_failed_login_at = None
    db.session.commit()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate PBORA SQLite backup rows into PostgreSQL.")
    parser.add_argument("--env-file", default=".env.production", help="Environment file for the target database.")
    parser.add_argument("--source", default="returnsform14_org_backup.sqlite", help="SQLite backup file to import.")
    parser.add_argument("--replace-existing", action="store_true", help="Truncate app tables before import if rows exist.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--sync-env-users", action="store_true", help="Create/update users from the env file after import.")
    parser.add_argument("--reset-user-password", default=None, help="Set every user account to this password after import.")
    args = parser.parse_args()

    env_file = (PROJECT_ROOT / args.env_file).resolve() if not Path(args.env_file).is_absolute() else Path(args.env_file)
    source_path = (PROJECT_ROOT / args.source).resolve() if not Path(args.source).is_absolute() else Path(args.source)

    form14_app = load_form14_app(env_file)
    with form14_app.app.app_context():
        form14_app.initialize_database(
            reset=False,
            seed_users=False,
            apply_schema_changes=True,
            run_postgresql_audit=False,
            sync_default_admin=False,
        )
        import_result = import_sqlite(
            form14_app,
            source_path=source_path,
            replace_existing=args.replace_existing,
            batch_size=args.batch_size,
        )
        seed_result = None
        if args.sync_env_users:
            seed_result = sync_env_users(form14_app, env_file)
        password_reset_count = None
        if args.reset_user_password:
            password_reset_count = reset_all_user_passwords(form14_app, args.reset_user_password)
        with form14_app.db.engine.begin() as connection:
            record_marker(connection, source_path, import_result["imported_rows"], password_reset_count)

    print(
        json.dumps(
            {
                "import": import_result,
                "seed_users": seed_result,
                "password_reset_count": password_reset_count,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
