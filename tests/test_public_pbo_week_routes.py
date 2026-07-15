from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pbo_week_landing_page_is_public(client):
    response = client.get("/form/pbo-week", base_url="https://returnsform14.org")

    assert response.status_code == 200
    assert b"PBO WEEK 2026" in response.data


def test_pbo_week_registration_redirects_to_login(client):
    response = client.get("/form/pbo-week-registration", base_url="https://returnsform14.org")

    assert response.status_code in {302, 308}
    assert "/login" in response.headers["Location"]


def test_pbo_week_schedule_page_is_public(client):
    response = client.get("/programme-schedule", base_url="https://returnsform14.org")

    assert response.status_code == 200
    assert b"PBO Week 2026" in response.data


def test_qr_code_target_uses_returnsform14_domain():
    qrcode_script = (ROOT / "QRCODE.py").read_text(encoding="utf-8")

    assert 'url = "https://returnsform14.org/form/pbo-week"' in qrcode_script


def test_deploy_once_defaults_to_returnsform14_domain():
    deploy_script = (ROOT / "deploy_once.sh").read_text(encoding="utf-8")

    assert 'DOMAIN_NAME="${CANONICAL_HOSTNAME:-returnsform14.org}"' in deploy_script
    assert "./deploy_once.sh sqlite returnsform14.org" in deploy_script
