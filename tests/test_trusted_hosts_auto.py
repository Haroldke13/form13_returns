from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_auto_trusted_hosts_include_current_server_ip(app_code):
    hosts = app_code.resolve_trusted_hosts(
        ["auto", "localhost", "127.0.0.1", "returnsform14.org"],
        app_host_ip="auto",
        runtime_hosts=["10.107.22.138", "127.0.0.1"],
    )

    assert "10.107.22.138" in hosts
    assert "localhost" in hosts
    assert "127.0.0.1" in hosts
    assert "returnsform14.org" in hosts
    assert "auto" not in hosts


def test_production_env_example_allows_research_cloudflare_hostname():
    env_text = (ROOT / ".env.production.example").read_text(encoding="utf-8")

    assert "research.harolditdata.uk" in env_text
