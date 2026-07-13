from pathlib import Path

import ip_address_change


OLD_IP = ip_address_change.DEFAULT_OLD_IP
NEW_IP = "192.168.1.50"


def test_replaces_hidden_and_text_files_while_skipping_self_backups_and_binary(tmp_path):
    (tmp_path / ".env").write_text(f"DB_HOST={OLD_IP}\n", encoding="utf-8")
    (tmp_path / "INSTRUCTIONS.md").write_text(
        f"Open http://{OLD_IP}:8000 and connect to {OLD_IP}.\n",
        encoding="utf-8",
    )

    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / ".env").write_text(f"DB_HOST={OLD_IP}\n", encoding="utf-8")

    script_path = tmp_path / "ip_address_change.py"
    script_path.write_text(f'DEFAULT_OLD_IP = "{OLD_IP}"\n', encoding="utf-8")

    binary_path = tmp_path / "logo.png"
    binary_path.write_bytes(b"\x89PNG\r\n" + OLD_IP.encode("ascii"))

    replacements, skipped_binary_hits = ip_address_change.find_replacements(
        root=tmp_path,
        old_ip=OLD_IP,
        include_backups=False,
        script_path=script_path.resolve(),
    )

    matched_paths = {path.relative_to(tmp_path) for path, _, _ in replacements}
    assert matched_paths == {Path(".env"), Path("INSTRUCTIONS.md")}
    assert [path.relative_to(tmp_path) for path in skipped_binary_hits] == []

    backup_root = ip_address_change.write_backups(tmp_path, replacements)
    ip_address_change.apply_replacements(replacements, OLD_IP, NEW_IP)

    assert f"DB_HOST={NEW_IP}" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert OLD_IP not in (tmp_path / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert f"DB_HOST={OLD_IP}" in (backups / ".env").read_text(encoding="utf-8")
    assert OLD_IP in script_path.read_text(encoding="utf-8")
    assert (backup_root / ".env").read_text(encoding="utf-8") == f"DB_HOST={OLD_IP}\n"


def test_render_runtime_env_removes_static_lan_db_dependency(monkeypatch):
    monkeypatch.setattr(ip_address_change, "runtime_ipv4_hosts", lambda: [])

    source = "\n".join(
        [
            "HOST_PORT=8000",
            f"PUBLIC_BASE_URL=http://{OLD_IP}:8000",
            f"ALLOWED_HOSTS={OLD_IP},localhost,127.0.0.1,returnsform14.org",
            f"PBORA_APP_HOST_IP={OLD_IP}",
            "PBORA_GATEWAY=192.0.2.1",
            f"PBORA_POSTGRES_HOST={OLD_IP}",
            "PBORA_POSTGRES_PORT=5432",
            f"PBORA_DATABASE_URL=postgresql+psycopg2://pbora:pbora@{OLD_IP}:5432/pbora",
            "DATABASE_DIALECT=postgresql",
            f"INTERNAL_DATABASE_URL=postgresql+psycopg2://pbora:pbora@{OLD_IP}:5432/pbora",
            f"DATABASE_URL=postgresql+psycopg2://pbora:pbora@{OLD_IP}:5432/pbora",
            f"DB_HOST={OLD_IP}",
            "DB_PORT=5432",
            "DB_NAME=pbora",
            "DB_USER=pbora",
            "DB_PASSWORD=pbora",
            f"POSTGRES_HOST={OLD_IP}",
            f"ANNUAL_RETURNS_DATABASE_URL=postgresql+psycopg2://pbora:pbora@{OLD_IP}:5432/pbora",
            "",
        ]
    )

    rendered = ip_address_change.render_runtime_env_text(
        source,
        app_host_ip=NEW_IP,
        gateway_ip="192.168.1.1",
    )

    assert f"PUBLIC_BASE_URL=http://{NEW_IP}:8000" in rendered
    assert f"ALLOWED_HOSTS={NEW_IP},localhost,127.0.0.1,returnsform14.org" in rendered
    assert "PBORA_NETWORK_INTERFACE=eth0" in rendered
    assert f"PBORA_APP_HOST_IP={NEW_IP}" in rendered
    assert f"DOCKER_BIND_IP={NEW_IP}" in rendered
    assert "PBORA_GATEWAY=192.168.1.1" in rendered
    assert "PBORA_POSTGRES_HOST=127.0.0.1" in rendered
    assert "DB_HOST=127.0.0.1" in rendered
    assert "POSTGRES_HOST=127.0.0.1" in rendered
    assert "INTERNAL_DATABASE_URL=postgresql+psycopg2://pbora:pbora@host.docker.internal:5432/pbora" in rendered
    assert "DATABASE_URL=postgresql+psycopg2://pbora:pbora@host.docker.internal:5432/pbora" in rendered
    assert "ANNUAL_RETURNS_DATABASE_URL=postgresql+psycopg2://pbora:pbora@host.docker.internal:5432/pbora" in rendered
    assert OLD_IP not in rendered


def test_render_runtime_env_preserves_explicit_public_domain(monkeypatch):
    monkeypatch.setattr(ip_address_change, "runtime_ipv4_hosts", lambda: [])

    source = "\n".join(
        [
            "HOST_PORT=8000",
            "PUBLIC_BASE_URL=https://returnsform14.org",
            "PUBLIC_HOST_PORT=443",
            "PREFERRED_URL_SCHEME=https",
            "ALLOWED_HOSTS=auto,returnsform14.org,www.returnsform14.org",
            "",
        ]
    )

    rendered = ip_address_change.render_runtime_env_text(
        source,
        app_host_ip="10.107.20.241",
        host_postgres_host="10.107.20.241",
        container_postgres_host="10.107.20.241",
    )

    assert "PUBLIC_BASE_URL=https://returnsform14.org" in rendered
    assert "ALLOWED_HOSTS=10.107.20.241,localhost,127.0.0.1,returnsform14.org,www.returnsform14.org" in rendered


def test_detect_primary_ipv4_prefers_eth0_over_docker_route(monkeypatch):
    def fake_command_output(args):
        if args == ["ip", "-o", "-4", "addr", "show", "dev", "eth0"]:
            return "2: eth0    inet 10.107.20.241/24 brd 10.107.20.255 scope global eth0"
        if args == ["ip", "-4", "route", "get", "1.1.1.1"]:
            return "1.1.1.1 dev docker0 src 172.17.0.1 uid 1000"
        if args == ["hostname", "-I"]:
            return "172.17.0.1 10.107.20.241"
        return ""

    monkeypatch.setattr(ip_address_change, "command_output", fake_command_output)

    assert ip_address_change.detect_primary_ipv4() == "10.107.20.241"


def test_render_runtime_env_sets_docker_bind_ip_to_app_host_ip():
    source = "\n".join(
        [
            "HOST_PORT=8000",
            "DOCKER_BIND_IP=0.0.0.0",
            "APP_HOST=0.0.0.0,10.107.20.241",
            "FLASK_RUN_HOST=0.0.0.0,10.107.20.241",
            "PBORA_POSTGRES_HOST=,10.107.20.241",
            "PBORA_DATABASE_URL=postgresql+psycopg2://pbora:pbora@,10.107.20.241:5432/pbora",
            "",
        ]
    )

    rendered = ip_address_change.render_runtime_env_text(
        source,
        app_host_ip="10.107.20.241",
        host_postgres_host="10.107.20.241",
        container_postgres_host="10.107.20.241",
    )

    assert "DOCKER_BIND_IP=10.107.20.241" in rendered
    assert "PBORA_NETWORK_INTERFACE=eth0" in rendered
    assert "APP_HOST=0.0.0.0" in rendered
    assert "FLASK_RUN_HOST=0.0.0.0" in rendered
    assert "PBORA_POSTGRES_HOST=10.107.20.241" in rendered
    assert "PBORA_DATABASE_URL=postgresql+psycopg2://pbora:pbora@10.107.20.241:5432/pbora" in rendered
    assert "@,10.107.20.241" not in rendered
