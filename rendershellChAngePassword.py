#!/usr/bin/env python3
"""Render shell utility for updating users_for_form14 account details via psql.

Uses INTERNAL_DATABASE_URL, generates password hashes locally, runs SELECT/UPDATE
through psql, and stores a local CSV audit log for shell-based admin changes.
"""

from __future__ import annotations

import argparse
import csv
import os
import secrets
import string
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

LOG_PATH = Path.home() / "Desktop" / "render_password_changes.csv"
PSQL_SEPARATOR = "\t"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def normalize_db_url(url: str) -> str:
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "sslmode=" not in url.lower():
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def get_database_url() -> str:
    db_url = os.getenv("INTERNAL_DATABASE_URL", "").strip()
    if not db_url:
        raise SystemExit("INTERNAL_DATABASE_URL is not set.")
    return normalize_db_url(db_url)


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return "'" + value.isoformat(sep=" ", timespec="seconds").replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(max(8, length - 2)))
    return f"{core}A1"


def ensure_log_file() -> None:
    if LOG_PATH.exists():
        return
    with LOG_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "changed_at_utc",
            "target",
            "user_id",
            "email",
            "full_name",
            "phone",
            "department",
            "role",
            "password_source",
            "generated_password",
        ])


def append_log(*, target: str, user_id: int, email: str | None, full_name: str | None,
               phone: str | None, department: str | None, role: str | None,
               password_source: str, generated_password: str | None) -> None:
    ensure_log_file()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            utc_now_iso(),
            target,
            user_id,
            email or "",
            full_name or "",
            phone or "",
            department or "",
            role or "",
            password_source,
            generated_password or "",
        ])


def run_psql(db_url: str, sql: str) -> str:
    result = subprocess.run(
        [
            "psql",
            db_url,
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-qAt",
            "-F",
            PSQL_SEPARATOR,
            "-c",
            sql,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit((result.stderr or result.stdout or "psql command failed").strip())
    return result.stdout.strip()


def fetch_user(db_url: str, selector_sql: str) -> dict[str, str] | None:
    sql = f"""
    SELECT id, email, full_name, phone, department, role, is_authorized, must_change_password, can_manage_all_records,
           COALESCE(to_char(password_changed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'), '')
    FROM users_for_form14
    WHERE {selector_sql}
    LIMIT 1;
    """
    output = run_psql(db_url, sql)
    if not output:
        return None
    parts = output.split(PSQL_SEPARATOR)
    keys = [
        "id",
        "email",
        "full_name",
        "phone",
        "department",
        "role",
        "is_authorized",
        "must_change_password",
        "can_manage_all_records",
        "password_changed_at",
    ]
    return dict(zip(keys, parts))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update a Render user account using INTERNAL_DATABASE_URL and psql.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", type=int, help="User id to update")
    target.add_argument("--email", help="User email to update")

    parser.add_argument("--full-name", help="Set full_name")
    parser.add_argument("--phone", help="Set phone")
    parser.add_argument("--department", help="Set department")
    parser.add_argument("--role", choices=["user", "admin", "designate"], help="Set role")
    parser.add_argument("--set-password", help="Set an explicit plaintext password")
    parser.add_argument("--generate-password", action="store_true", help="Generate a new password automatically")
    parser.add_argument("--password-length", type=int, default=14, help="Length for generated password")
    parser.add_argument("--must-change-password", choices=["true", "false"], help="Set must_change_password")
    parser.add_argument("--authorize", choices=["true", "false"], help="Set is_authorized")
    parser.add_argument("--can-manage-all-records", choices=["true", "false"], help="Set can_manage_all_records")
    parser.add_argument("--print-sql", action="store_true", help="Print the UPDATE statement")
    parser.add_argument("--dry-run", action="store_true", help="Show current row and planned changes without updating")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    db_url = get_database_url()

    if args.id is not None:
        selector_sql = f"id = {args.id}"
        target_label = f"id={args.id}"
    else:
        selector_sql = f"email = {sql_literal(args.email)}"
        target_label = f"email={args.email}"

    updates: dict[str, object] = {}
    generated_password = None
    password_source = "unchanged"

    if args.full_name is not None:
        updates["full_name"] = args.full_name.strip() or None
    if args.phone is not None:
        updates["phone"] = args.phone.strip() or None
    if args.department is not None:
        updates["department"] = args.department.strip() or None
    if args.role is not None:
        updates["role"] = args.role
    if args.must_change_password is not None:
        updates["must_change_password"] = args.must_change_password == "true"
    if args.authorize is not None:
        updates["is_authorized"] = args.authorize == "true"
    if args.can_manage_all_records is not None:
        updates["can_manage_all_records"] = args.can_manage_all_records == "true"

    if args.set_password and args.generate_password:
        raise SystemExit("Use either --set-password or --generate-password, not both.")

    if args.set_password:
        generated_password = args.set_password
        updates["password_hash"] = generate_password_hash(args.set_password)
        updates["password_changed_at"] = utc_now()
        password_source = "explicit"
    elif args.generate_password:
        generated_password = generate_password(args.password_length)
        updates["password_hash"] = generate_password_hash(generated_password)
        updates["password_changed_at"] = utc_now()
        password_source = "generated"

    if not updates:
        raise SystemExit("No changes provided. Add at least one field to update.")

    current_row = fetch_user(db_url, selector_sql)
    if not current_row:
        raise SystemExit(f"No user found for {target_label}.")

    assignments = ", ".join(f"{column} = {sql_literal(value)}" for column, value in updates.items())
    update_sql = f"UPDATE public.users_for_form14 SET {assignments} WHERE {selector_sql};"

    if args.print_sql or args.dry_run:
        print(f"Target: {target_label}")
        print("Current:", current_row)
        print("SQL:", update_sql)
        if generated_password:
            print(f"Generated password: {generated_password}")
        if args.dry_run:
            return

    run_psql(db_url, update_sql)
    updated_row = fetch_user(db_url, selector_sql)
    if not updated_row:
        raise SystemExit("Update ran, but the user could not be reloaded.")

    append_log(
        target=target_label,
        user_id=int(updated_row["id"]),
        email=updated_row.get("email"),
        full_name=updated_row.get("full_name"),
        phone=updated_row.get("phone"),
        department=updated_row.get("department"),
        role=updated_row.get("role"),
        password_source=password_source,
        generated_password=generated_password,
    )

    print("Updated user successfully.")
    print(f"Target: {target_label}")
    print(f"ID: {updated_row['id']}")
    print(f"Email: {updated_row['email']}")
    print(f"Full name: {updated_row['full_name']}")
    print(f"Phone: {updated_row['phone']}")
    print(f"Department: {updated_row['department']}")
    print(f"Role: {updated_row['role']}")
    print(f"Authorized: {updated_row['is_authorized']}")
    print(f"Must change password: {updated_row['must_change_password']}")
    print(f"Manage all records: {updated_row['can_manage_all_records']}")
    print(f"Password changed at: {updated_row['password_changed_at']}")
    if generated_password:
        print(f"New password: {generated_password}")
    print(f"Change log: {LOG_PATH}")


if __name__ == "__main__":
    main()
