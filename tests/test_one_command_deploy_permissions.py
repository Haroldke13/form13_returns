from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "one_command_deploy.sh"


def test_one_command_deploy_repairs_app_owned_runtime_directories():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "repair_path_permission()" in text
    assert 'ensure_writable_directory "$APP_DIR"' in text
    assert 'ensure_writable_directory "$APP_DIR/instance"' in text
    assert 'ensure_writable_directory "$APP_DIR/backups"' in text
    assert 'ensure_writable_directory "$CERT_DIR"' in text
    assert "ensure_user_editable_file()" in text
    assert 'ENV_EXAMPLE_FILE="${ENV_EXAMPLE_FILE:-$APP_DIR/.env.production.example}"' in text
    assert 'ensure_user_editable_file "$ENV_FILE" "$ENV_EXAMPLE_FILE"' in text


def test_one_command_deploy_bootstraps_docker_buildx_and_required_packages():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "ensure_required_system_packages()" in text
    assert "install_docker_stack()" in text
    assert "reinstall_docker_stack_packages()" in text
    assert "remove_conflicting_docker_packages()" in text
    assert "docker-buildx-plugin" in text
    assert "docker-compose-plugin" in text
    assert "docker buildx version" in text
    assert "python3-venv" in text
    assert "openssl" in text
    assert "docker.io" in text
    assert "podman-docker" in text
    assert "--reinstall" in text

    docker_stack_body = text.split("install_docker_stack() {", 1)[1].split("\n}", 1)[0]
    assert "return 0" not in docker_stack_body.split("reinstall_docker_stack_packages", 1)[0]


def test_one_command_deploy_repairs_docker_apt_keyring_before_apt_update():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "repair_docker_apt_repository_if_configured()" in text
    assert "/etc/apt/keyrings/docker.asc" in text
    assert "/etc/apt/sources.list.d/docker.sources" in text
    assert "/etc/apt/sources.list.d/docker.list" in text

    install_packages_body = text.split("install_ubuntu_packages() {", 1)[1].split("\n}", 1)[0]
    assert install_packages_body.index("repair_docker_apt_repository_if_configured") < install_packages_body.index("sudo_apt_update_once")

    apt_update_body = text.split("sudo_apt_update_once() {", 1)[1].split("\n}", 1)[0]
    assert apt_update_body.index("repair_docker_apt_repository_if_configured") < apt_update_body.index("sudo apt-get update")


def test_one_command_deploy_applies_host_routes_automatically_before_app_start():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "configure_host_routes()" in text
    assert "--profile route-setup" in text
    assert "run --rm host-route-setup" in text
    assert "docker-compose.prod.postgres.yml" in text

    render_index = text.index("--render-runtime-env")
    route_index = text.index("\nconfigure_host_routes\n")
    compose_up_index = text.index("up -d --build")

    assert render_index < route_index < compose_up_index


def test_one_command_deploy_runs_noninteractive_sudo_for_prod_and_dev():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'PBORA_DEPLOY_ENV="${PBORA_DEPLOY_ENV:-production}"' in text
    assert 'production) DEFAULT_SUDO_PASSWORD="pbora"' in text
    assert 'development|dev) DEFAULT_SUDO_PASSWORD="Programmer.95"' in text
    assert "prime_sudo()" in text
    assert "sudo -S -p '' -v" in text


def test_one_command_deploy_sets_up_postgres_firewall_and_access_checks():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "ensure_host_postgres()" in text
    assert "sudo -u postgres psql" in text
    assert "repair_postgres_access()" in text
    assert "scripts/fix_postgres_access.sh" in text
    assert "open_lan_firewall()" in text
    assert "ufw allow from \"$PBORA_ROUTE_CIDR\" to any port \"$HOST_PORT\" proto tcp" in text


def test_one_command_deploy_runs_healthcheck_and_browser_access_test_after_start():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "run_app_healthcheck()" in text
    assert '"$APP_DIR/healthcheck.sh" "$MODE"' in text
    assert "test_browser_access()" in text
    assert 'curl -sS --max-time 20 -H "Host: $APP_IP"' in text
    assert "BadHost" in text


def test_one_command_deploy_refreshes_allowed_hosts_before_start():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "refresh_runtime_environment()" in text
    assert "ALLOWED_HOSTS" in text
    assert "PBORA_APP_HOST_IP" in text
