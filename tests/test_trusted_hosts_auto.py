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
