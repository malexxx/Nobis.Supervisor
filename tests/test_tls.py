from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

import main
import tls_service
from database import get_public_settings_dict, get_settings_dict


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _restore_tls_settings():
    before = get_settings_dict()
    yield
    keys = (
        "https_mode",
        "https_domain",
        "https_dns_provider",
        "https_email",
        "https_dns_token",
        "https_san_ips",
        "https_san_dns",
        "agua_password",
    )
    tls_service.save_tls_settings({k: before.get(k, "") for k in keys})


def test_tls_validators_reject_invalid_inputs():
    assert tls_service.validate_domain("Mon-Poele.DuckDNS.org.") == "mon-poele.duckdns.org"
    with pytest.raises(ValueError):
        tls_service.validate_domain("bad;rm -rf /")
    with pytest.raises(ValueError):
        tls_service.validate_domain("localhost")

    assert tls_service.validate_email("user@example.com") == "user@example.com"
    assert tls_service.validate_email("") == ""
    with pytest.raises(ValueError):
        tls_service.validate_email("not-an-email")

    assert tls_service.parse_san_ips("192.168.1.50, 10.0.0.1") == ["192.168.1.50", "10.0.0.1"]
    with pytest.raises(ValueError):
        tls_service.parse_san_ips("192.168.1.999")

    assert tls_service.parse_san_dns("nas.local, syno") == ["nas.local", "syno"]
    with pytest.raises(ValueError):
        tls_service.parse_san_dns("bad host;ls")


def test_acme_env_and_token_redaction():
    env_cf = tls_service._build_acme_provider_env("cloudflare", "my-secret-cf-token-12345")
    assert env_cf["CF_Token"] == "my-secret-cf-token-12345"

    env_ovh = tls_service._build_acme_provider_env("ovh", "AK123456:AS654321:CK999999")
    assert env_ovh["OVH_AK"] == "AK123456"
    assert env_ovh["OVH_AS"] == "AS654321"
    assert env_ovh["OVH_CK"] == "CK999999"
    assert env_ovh["OVH_END_POINT"] == "ovh-eu"

    with pytest.raises(ValueError):
        tls_service._build_acme_provider_env("ovh", "incomplete-token")

    sanitized = tls_service._sanitize_acme_output(
        "Error calling API with token my-secret-cf-token-12345 failed",
        "my-secret-cf-token-12345",
    )
    assert "my-secret-cf-token-12345" not in sanitized
    assert "***" in sanitized


def test_api_tls_status_and_token_masking(monkeypatch):
    tls_service.save_tls_settings({
        "https_mode": "http",
        "https_domain": "poele.duckdns.org",
        "https_dns_provider": "duckdns",
        "https_dns_token": "super-secret-duck-token",
    })
    pub = get_public_settings_dict()
    assert pub["https_dns_token"] == "********"

    res = client.get("/api/tls/status")
    assert res.status_code == 200
    body = res.json()
    assert body["https_domain"] == "poele.duckdns.org"
    assert body["has_dns_token"] is True
    assert "super-secret-duck-token" not in res.text
    provider_ids = [p["id"] for p in body["providers"]]
    assert "duckdns" in provider_ids
    assert "cloudflare" in provider_ids
    assert "ovh" in provider_ids


def test_api_tls_mode_switch_http_and_selfsigned(monkeypatch, tmp_path):
    monkeypatch.setenv("CERTS_DIR", str(tmp_path / "certs"))
    tls_service.save_tls_settings({"agua_password": ""})

    with patch("routers.tls.ensure_selfsigned_cert", return_value=(True, "Certificat auto-signé OK")):
        res = client.post(
            "/api/tls/mode",
            json={
                "mode": "selfsigned",
                "san_ips": "192.168.1.50",
                "san_dns": "synology.local",
                "regenerate": True,
                "restart": False,
            },
        )
        assert res.status_code == 200
        assert res.json()["mode"] == "selfsigned"
        s = get_settings_dict()
        assert s["https_mode"] == "selfsigned"
        assert s["https_san_ips"] == "192.168.1.50"
        assert s["https_san_dns"] == "synology.local"

        res_http = client.post("/api/tls/mode", json={"mode": "http", "restart": False})
        assert res_http.status_code == 200
        assert get_settings_dict()["https_mode"] == "http"


def test_api_tls_letsencrypt_failure_preserves_previous_mode():
    tls_service.save_tls_settings({
        "agua_password": "",
        "https_mode": "selfsigned",
        "https_dns_token": "",
    })

    with patch("routers.tls.issue_letsencrypt_cert", new=AsyncMock(return_value=(False, "Token DuckDNS invalide"))):
        res = client.post(
            "/api/tls/letsencrypt/issue",
            json={
                "domain": "mon-poele.duckdns.org",
                "dns_provider": "duckdns",
                "dns_token": "bad-token-123456",
                "email": "test@example.com",
                "restart": False,
            },
        )
        assert res.status_code == 502
        assert "Token DuckDNS invalide" in res.json()["detail"]
        # Le mode précédent doit être conservé intact
        assert get_settings_dict()["https_mode"] == "selfsigned"


def test_api_tls_letsencrypt_success_activates_mode():
    tls_service.save_tls_settings({
        "agua_password": "",
        "https_mode": "selfsigned",
        "https_dns_token": "",
    })

    with patch("routers.tls.issue_letsencrypt_cert", new=AsyncMock(return_value=(True, "Certificat prêt"))):
        res = client.post(
            "/api/tls/letsencrypt/issue",
            json={
                "domain": "mon-poele.duckdns.org",
                "dns_provider": "duckdns",
                "dns_token": "valid-token-123456",
                "email": "test@example.com",
                "restart": False,
            },
        )
        assert res.status_code == 200
        assert res.json()["mode"] == "letsencrypt"
        s = get_settings_dict()
        assert s["https_mode"] == "letsencrypt"
        assert s["https_domain"] == "mon-poele.duckdns.org"
        assert s["https_dns_provider"] == "duckdns"
        assert s["https_dns_token"] == "valid-token-123456"


def test_resolve_runtime_tls_force_http_and_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("CERTS_DIR", str(tmp_path / "certs"))
    tls_service.save_tls_settings({"https_mode": "letsencrypt"})

    monkeypatch.setenv("FORCE_HTTP", "true")
    mode, cert, key = tls_service.resolve_runtime_tls()
    assert (mode, cert, key) == ("http", None, None)

    monkeypatch.delenv("FORCE_HTTP", raising=False)
    # Certificat LE manquant + openssl absent -> repli sécurisé sur HTTP
    with patch("tls_service.find_openssl", return_value=None):
        mode2, cert2, key2 = tls_service.resolve_runtime_tls()
        assert (mode2, cert2, key2) == ("http", None, None)


def test_hsts_header_only_in_letsencrypt_mode(monkeypatch):
    monkeypatch.setenv("NOBIS_ACTIVE_TLS_MODE", "http")
    r_http = client.get("/health")
    assert "strict-transport-security" not in {k.lower() for k in r_http.headers}

    monkeypatch.setenv("NOBIS_ACTIVE_TLS_MODE", "selfsigned")
    r_ss = client.get("/health")
    assert "strict-transport-security" not in {k.lower() for k in r_ss.headers}

    monkeypatch.setenv("NOBIS_ACTIVE_TLS_MODE", "letsencrypt")
    r_le = client.get("/health")
    assert "max-age=31536000" in r_le.headers.get("Strict-Transport-Security", "")
