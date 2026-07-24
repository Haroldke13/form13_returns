from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker-entrypoint.sh"
POSTGRES_COMPOSE = ROOT / "docker-compose.prod.postgres.yml"
CF_COMPOSE = ROOT / "docker-compose.cloudflare.yml"
CF_DUMMY_COMPOSE = ROOT / "docker-compose.cloudflare.dummy.yml"
ROUTE_SCRIPT = ROOT / "scripts" / "configure_host_routes.sh"


def test_docker_runtime_expands_gunicorn_settings_in_entrypoint():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert '"${WEB_CONCURRENCY:-2}"' not in dockerfile
    assert 'CMD ["gunicorn"]' in dockerfile
    assert 'WEB_CONCURRENCY:-2' in entrypoint
    assert 'GUNICORN_THREADS:-8' in entrypoint
    assert 'GUNICORN_TIMEOUT:-360' in entrypoint


def test_postgres_compose_has_opt_in_host_route_setup_service():
    compose = POSTGRES_COMPOSE.read_text(encoding="utf-8")

    assert "host-route-setup:" in compose
    assert "route-setup" in compose
    assert "network_mode: host" in compose
    assert "NET_ADMIN" in compose
    assert "privileged: true" in compose
    assert "/app/scripts/configure_host_routes.sh" in compose
    assert "PBORA_ROUTE_CIDR" in compose
    assert "PBORA_REPLACE_DEFAULT_ROUTE" in compose


def test_route_defaults_target_pbora_lan_supernet():
    env_example = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    env_render_example = (ROOT / ".env.render.postgres.example").read_text(encoding="utf-8")

    assert "PBORA_ROUTE_CIDR=10.107.0.0/19" in env_example
    assert "PBORA_ROUTE_CIDR=10.107.0.0/19" in env_render_example


def test_host_route_script_uses_ubuntu_ip_route_commands():
    script = ROUTE_SCRIPT.read_text(encoding="utf-8")

    assert "ip route replace" in script
    assert 'ip route replace "$PBORA_ROUTE_CIDR"' in script
    assert 'ip route replace default via "$PBORA_GATEWAY"' in script
    assert "PBORA_ROUTE_DRY_RUN" in script
    assert "PBORA_ASSIGN_HOST_IP" in script


def test_cloudflare_tunnel_sidecars_use_token_files_not_token_args():
    compose = CF_COMPOSE.read_text(encoding="utf-8")
    dummy_compose = CF_DUMMY_COMPOSE.read_text(encoding="utf-8")

    assert "--token-file" in compose
    assert "--token-file" in dummy_compose
    assert "--token\n" not in compose
    assert "--token\n" not in dummy_compose
    assert "returnsform14-tunnel.token" in compose
    assert "dummy-tunnel.token" in dummy_compose
