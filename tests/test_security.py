"""Server security helpers: webhook signing, SSRF guard, redirect guard, PII masking, encryption."""

import hashlib
import hmac

import pytest

pytest.importorskip("fastapi")  # needs the [server] extra

from id_verification.server import security  # noqa: E402


def test_webhook_signature_roundtrip():
    body = b'{"id":"x"}'
    header = security.sign_webhook("whsec", body, 1700000000)
    t, v1 = (p.split("=", 1)[1] for p in header.split(","))
    expected = hmac.new(b"whsec", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert t == "1700000000" and hmac.compare_digest(v1, expected)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest",
        "https://localhost/x",
        "ftp://example.com",
    ],
)
def test_ssrf_guard_blocks(url):
    with pytest.raises(ValueError):
        security.check_webhook_url(url)


def test_ssrf_guard_allows_public_ip():
    security.check_webhook_url("https://1.1.1.1/hook")


def test_mask_and_encrypt():
    assert security.mask_nik("7471091102020007") == "7471********0007"
    assert security.decrypt_str(security.encrypt_str("rahasia")) == "rahasia"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "//evil.example", "http://x.example/ok"])
def test_redirect_guard_blocks(url):
    with pytest.raises(ValueError):
        security.check_redirect_url(url)


def test_redirect_guard_allows_https():
    security.check_redirect_url("https://shop.example/kyc/done?u=1")
    security.check_redirect_url("http://localhost:3000/done", allow_http=True)
