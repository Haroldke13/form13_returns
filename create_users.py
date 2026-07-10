import os

from dotenv import dotenv_values, load_dotenv

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError


def is_production_environment():
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    render_flag = (os.getenv("RENDER") or "").strip().lower() in {"1", "true", "yes", "on"}
    flask_env_production = (os.getenv("FLASK_ENV_PRODUCTION") or "").strip().lower() in {"1", "true", "yes", "on"}
    return app_env == "production" or render_flag or flask_env_production or bool(render_hostname)


def normalize_database_url(raw_url):
    if not raw_url:
        return raw_url

    database_url = raw_url.strip()
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    if database_url.startswith("postgresql://") and "sslmode=" not in database_url.lower():
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


def configure_runtime_database_url():
    load_dotenv()
    internal_database_url = normalize_database_url(os.getenv("INTERNAL_DATABASE_URL"))
    if internal_database_url:
        os.environ["INTERNAL_DATABASE_URL"] = internal_database_url


configure_runtime_database_url()

from models import User, db


def normalize_seed_email(raw_email):
    if not raw_email:
        return None
    return raw_email.strip().lower() or None


def generate_email_from_name(full_name):
    if not full_name:
        return None
    parts = [part for part in full_name.strip().split() if part]
    if len(parts) < 2:
        return None
    return f"{parts[0][0]}{parts[1]}".lower()


def resolve_seed_password(explicit_password, fallback_password):
    password = (explicit_password or "").strip()
    if password:
        return password
    return (fallback_password or "").strip() or "field.12345"


def get_login_name(email):
    normalized = normalize_seed_email(email)
    if not normalized:
        return None
    return normalized.split("@", 1)[0]


def seed_user_role_priority(role):
    normalized = (role or "").strip().lower()
    if normalized == "admin":
        return 2
    if normalized == "designate":
        return 1
    return 0


def select_preferred_existing_user(users):
    candidates = [user for user in users if user is not None]
    if not candidates:
        return None

    def sort_key(user):
        user_id = getattr(user, "id", None) or 0
        return (
            1 if getattr(user, "is_superadmin", False) else 0,
            1 if getattr(user, "can_manage_all_records", False) else 0,
            seed_user_role_priority(getattr(user, "role", None)),
            1 if getattr(user, "is_authorized", False) else 0,
            1 if getattr(user, "password_hash", None) else 0,
            1 if getattr(user, "password_changed_at", None) else 0,
            str(getattr(user, "password_changed_at", None) or ""),
            1 if getattr(user, "last_login_at", None) else 0,
            str(getattr(user, "last_login_at", None) or ""),
            1 if getattr(user, "full_name", None) else 0,
            1 if getattr(user, "department", None) else 0,
            -int(user_id),
        )

    return max(candidates, key=sort_key)


def find_existing_user_by_email(email):
    normalized = normalize_seed_email(email)
    if not normalized:
        return None
    matches = (
        User.query
        .filter(User.email.isnot(None))
        .filter(func.lower(User.email) == normalized)
        .all()
    )
    return select_preferred_existing_user(matches)


def parse_seed_users(source=None):
    source = source or os.environ
    users = []
    seen_emails = set()
    index = 1
    while True:
        name_key = f"user{index}_name"
        email_key = f"user{index}_email"
        password_key = f"user{index}_password"
        department_key = f"user{index}_department"
        phone_key = f"user{index}_phone"

        name = source.get(name_key)
        email = source.get(email_key)
        password = source.get(password_key)
        department = source.get(department_key)
        phone = source.get(phone_key)

        if not any([name, email, password]):
            break

        generated_email = generate_email_from_name(name or "")
        normalized_email = normalize_seed_email(email) or generated_email
        if not normalized_email:
            index += 1
            continue
        if normalized_email in seen_emails:
            print(f"Skipping duplicate seed email: {normalized_email}")
            index += 1
            continue

        resolved_phone = (phone or "").strip() or None
        fallback_password = source.get("USER_SEED_PASSWORD_DEFAULT", "field.12345")
        resolved_password = resolve_seed_password(password, fallback_password)

        users.append({
            "name": (name or "").strip() or None,
            "email": normalized_email,
            "password": resolved_password,
            "department": (department or "").strip() or None,
            "phone": resolved_phone,
        })
        seen_emails.add(normalized_email)
        index += 1

    return users


def parse_admin_emails(source=None):
    source = source or os.environ
    raw = source.get("ADMIN_SEEDED_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def create_or_update_users(seed_users, admin_emails):
    created = 0
    updated = 0
    for entry in seed_users:
        email = entry["email"]
        user = find_existing_user_by_email(email)
        is_admin = email in admin_emails
        if user is None:
            user = User(
                email=email,
                full_name=entry["name"],
                department=entry["department"],
                phone=entry["phone"],
                role="admin" if is_admin else "user",
                is_authorized=True,
                is_superadmin=False,
                can_manage_all_records=False,
                must_change_password=False,
            )
            user.set_password(entry["password"], mark_changed=False)
            try:
                db.session.add(user)
                db.session.commit()
                created += 1
            except IntegrityError:
                db.session.rollback()
                print(f"Skipped duplicate user: {email}")
            continue

        updated_fields = False
        preserve_user_managed_credentials = (not is_admin) and (
            user.password_changed_at is not None or user.must_change_password is False
        )
        if entry["name"] and user.full_name != entry["name"] and not preserve_user_managed_credentials:
            user.full_name = entry["name"]
            updated_fields = True
        if entry["department"] and user.department != entry["department"] and not preserve_user_managed_credentials:
            user.department = entry["department"]
            updated_fields = True
        if entry["phone"] and user.phone != entry["phone"] and not preserve_user_managed_credentials:
            user.phone = entry["phone"]
            updated_fields = True
        if is_admin and user.role != "admin":
            user.role = "admin"
            updated_fields = True
        if user.is_authorized is not True:
            user.is_authorized = True
            updated_fields = True
        if (
            entry["password"]
            and not preserve_user_managed_credentials
            and (not user.password_hash or not user.check_password(entry["password"]))
        ):
            user.set_password(entry["password"], mark_changed=False)
            updated_fields = True
        if updated_fields:
            updated += 1

    db.session.commit()
    return created, updated


def ensure_superadmin_from_env():
    admin_email = (os.getenv("ADMIN_USER_EMAIL") or "").strip().lower()
    admin_password = os.getenv("ADMIN_USER_PASSWORD")
    admin_name = (os.getenv("ADMIN_USER_NAME") or "").strip() or None
    admin_department = (os.getenv("ADMIN_USER_DEPARTMENT") or "").strip() or "Superadmin"
    if not admin_email or not admin_password:
        print("Skipping superadmin seed: ADMIN_USER_EMAIL or ADMIN_USER_PASSWORD missing.")
        return

    user = find_existing_user_by_email(admin_email)
    if user is None:
        user = User(
            email=admin_email,
            full_name=admin_name or "Superadmin",
            department=admin_department,
            role="admin",
            is_authorized=True,
            is_superadmin=True,
            can_manage_all_records=True,
            must_change_password=False,
        )
        user.set_password(admin_password, mark_changed=False)
        db.session.add(user)
        db.session.commit()
        print(f"Created superadmin: {admin_email}")
        return

    updated = False
    if user.role != "admin":
        user.role = "admin"
        updated = True
    if not user.is_authorized:
        user.is_authorized = True
        updated = True
    if not user.is_superadmin:
        user.is_superadmin = True
        updated = True
    if admin_name and user.full_name != admin_name:
        user.full_name = admin_name
        updated = True
    if admin_department and user.department != admin_department:
        user.department = admin_department
        updated = True
    if not user.can_manage_all_records:
        user.can_manage_all_records = True
        updated = True
    if not user.password_hash or not user.check_password(admin_password):
        user.set_password(admin_password, mark_changed=False)
        updated = True
    if updated:
        db.session.commit()
        print(f"Updated superadmin: {admin_email}")


def seed_users_from_runtime(seed_source=None, initialize_schema=False, app_instance=None, initialize_database_fn=None):
    load_dotenv()
    resolved_seed_source = seed_source
    if resolved_seed_source is None and os.path.exists(".env.users"):
        seed_source = {
            key: value
            for key, value in dotenv_values(".env.users").items()
            if value is not None
        }
        load_dotenv(".env.users", override=True)
        resolved_seed_source = seed_source
    elif resolved_seed_source is None:
        resolved_seed_source = dict(os.environ)

    if app_instance is None or initialize_database_fn is None:
        from app import app as imported_app, initialize_database as imported_initialize_database
        app_instance = app_instance or imported_app
        initialize_database_fn = initialize_database_fn or imported_initialize_database

    admin_emails = parse_admin_emails(resolved_seed_source)
    with app_instance.app_context():
        if initialize_schema:
            initialize_database_fn(reset=False, seed_users=False)
        ensure_superadmin_from_env()
        seed_users = parse_seed_users(resolved_seed_source)
        if not seed_users:
            print("No seed users found in .env. Superadmin/database initialization complete.")
            return 0, 0
        if (resolved_seed_source.get("USER_SEED_RESET", "") or "").strip() == "1":
            print("USER_SEED_RESET=1 detected, but destructive deletes are disabled. Existing users will be preserved and updated in place.")
        elif (resolved_seed_source.get("USER_SEED_SYNC", "") or "").strip() == "1":
            print("USER_SEED_SYNC=1 detected, but destructive deletes are disabled. Existing users not in the seed list will be preserved.")
        created, updated = create_or_update_users(seed_users, admin_emails)
    return created, updated


def main():
    created, updated = seed_users_from_runtime(initialize_schema=True)

    print(f"Seed users complete. Created: {created}, Updated: {updated}.")


if __name__ == "__main__":
    main()
