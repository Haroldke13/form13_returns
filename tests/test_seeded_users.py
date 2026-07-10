import uuid
from pathlib import Path

from dotenv import dotenv_values

import create_users as create_users_module


def load_seeded_users():
    project_root = Path(__file__).resolve().parents[1]
    seed_file = project_root / ".env.users"
    if not seed_file.exists():
        seed_file = project_root / ".env"
    source = {
        key: value
        for key, value in dotenv_values(seed_file).items()
        if value is not None
    }
    return source, create_users_module.parse_seed_users(source)


def login(client, email, password, department=""):
    return client.post(
        "/login",
        data={"email": email, "password": password, "department": department},
        follow_redirects=True,
    )


def test_all_seeded_users_can_login_and_submit_form(app, client, models, monkeypatch):
    db = models["db"]
    User = models["User"]
    PBOReport = models["PBOReport"]

    seed_source, seed_users = load_seeded_users()
    assert seed_users, "Expected at least one seeded user from .env.users"

    monkeypatch.setattr(create_users_module, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.notify_user_login", lambda user: None)

    with app.app_context():
        create_users_module.ensure_superadmin_from_env()
        created, updated = create_users_module.create_or_update_users(
            seed_users,
            create_users_module.parse_admin_emails(seed_source),
        )
        assert created + updated >= len(seed_users)

    for index, entry in enumerate(seed_users, start=1):
        login_response = login(
            client,
            entry["email"],
            entry["password"],
            entry["department"] or "",
        )
        assert login_response.status_code == 200
        assert b"Logged in successfully." in login_response.data, entry["email"]

        token = f"SEED-{index}-{uuid.uuid4().hex[:10]}".upper()
        registration = f"REG-{index:03d}-{uuid.uuid4().hex[:6]}".upper()
        pbo_name = f"SEEDED USER NGO {index} {uuid.uuid4().hex[:8]}".upper()

        submit_response = client.post(
            "/",
            data={
                "submission_token": token,
                "pbo_name": pbo_name,
                "pbo_registration_number": registration,
            },
            follow_redirects=False,
        )

        assert submit_response.status_code == 302, entry["email"]

        with app.app_context():
            user = User.query.filter_by(email=entry["email"]).first()
            assert user is not None, entry["email"]

            report = PBOReport.query.filter_by(submission_token=token).first()
            assert report is not None, token
            assert report.user_id == user.id, entry["email"]
            assert report.workflow_status == "submitted", entry["email"]
            assert report.review_status == "pending", entry["email"]

        client.get("/logout", follow_redirects=True)
