import os
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import inspect, text


TEST_DB_PATH = pathlib.Path("/tmp") / f"form14_pytest_{uuid.uuid4().hex}.sqlite"
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("INTERNAL_DATABASE_URL", f"sqlite:///{TEST_DB_PATH}")
os.environ.setdefault("GOOGLE_DRIVE_ENABLED", "false")
os.environ.setdefault("GOOGLE_DRIVE_DISABLE_UPLOAD", "1")
os.environ.setdefault("ADMIN_USER_EMAIL", "superadmin@example.com")
os.environ.setdefault("ADMIN_USER_PASSWORD", "field.12345")
os.environ.setdefault("ALLOW_LEGACY_SCHEMA_BOOTSTRAP", "1")

import app as app_module  # noqa: E402
from models import BankAccount, PBOReport, Payment, UploadedFile, User, db  # noqa: E402


@pytest.fixture
def app():
    app_module.app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    return app_module.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_database(app):
    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            db.session.execute(text("PRAGMA foreign_keys=OFF"))
            inspector = inspect(db.engine)
            for table_name in inspector.get_table_names():
                quoted_table_name = '"' + table_name.replace('"', '""') + '"'
                db.session.execute(text(f"DROP TABLE IF EXISTS {quoted_table_name}"))
            db.session.commit()
            db.create_all()
            db.session.execute(text("PRAGMA foreign_keys=ON"))
            db.session.commit()
        else:
            db.session.remove()
            db.drop_all()
            db.create_all()
        app_module.ensure_default_admin()
        yield
        db.session.remove()


@pytest.fixture
def models():
    return {
        "db": db,
        "User": User,
        "PBOReport": PBOReport,
        "BankAccount": BankAccount,
        "Payment": Payment,
        "UploadedFile": UploadedFile,
    }


@pytest.fixture
def app_code():
    return app_module
