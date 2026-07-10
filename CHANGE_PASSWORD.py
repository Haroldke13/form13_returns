#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_PASSWORD = "field.12345"


def load_app(env_file: Path):
    if env_file.exists():
        load_dotenv(env_file, override=True)
    os.environ["SKIP_SCHEMA_CHECK"] = "1"
    import app as form14_app

    return form14_app


def reset_passwords(env_file: Path, password: str, email: str | None = None) -> tuple[int, int]:
    form14_app = load_app(env_file)
    from models import User, db

    with form14_app.app.app_context():
        query = User.query.order_by(User.id)
        if email:
            query = query.filter(User.email == email.strip().lower())

        matched = 0
        changed = 0
        for user in query.all():
            matched += 1
            if not user.password_hash or not user.check_password(password):
                user.set_password(password, mark_changed=True)
                changed += 1
            user.must_change_password = False
            user.failed_login_attempts = 0
            user.last_failed_login_at = None

        db.session.commit()
        return matched, changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset PBORA Form 13 user passwords.")
    parser.add_argument("--env-file", default=".env.production")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--email", default=None, help="Reset one login/email only. Omit to reset every user.")
    args = parser.parse_args()

    matched, changed = reset_passwords(Path(args.env_file), args.password, args.email)
    print(f"matched_users={matched}")
    print(f"changed_passwords={changed}")
    print(f"password={args.password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
