import pytest


def test_redirects_to_canonical_domain_for_other_hosts(client, monkeypatch):
    monkeypatch.setenv("CANONICAL_HOSTNAME", "returnsform13.com")
    monkeypatch.setenv("FORCE_CANONICAL_HOST", "1")
    client.application.config["TRUSTED_HOSTS"] = ["*"]
    client.application.trusted_hosts = ["*"]

    response = client.get("/", base_url="http://example.org")

    assert response.status_code == 308
    assert response.headers["Location"] == "http://returnsform13.com/"
