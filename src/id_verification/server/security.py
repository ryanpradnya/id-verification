"""API keys, encryption at rest, webhook signing, SSRF guard, PII masking."""

import hashlib
import hmac
import ipaddress
import secrets
import socket
from functools import lru_cache
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from id_verification.config import get_settings


def new_api_key() -> str:
    return "kyc_" + secrets.token_urlsafe(32)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha256(value: str) -> str:
    # Keys/tokens are 256-bit random, so a plain hash is enough (no slow KDF needed)
    # and lookup-by-hash avoids comparing secrets in application code.
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().data_encryption_key
    if not key:
        raise RuntimeError("DATA_ENCRYPTION_KEY is required by the server: run `id-verification gen-key`")
    return Fernet(key)


def encrypt(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt(token: bytes) -> bytes:
    return _fernet().decrypt(token)


def encrypt_str(s: str) -> str:
    return encrypt(s.encode()).decode()


def decrypt_str(s: str) -> str:
    return decrypt(s.encode()).decode()


def sign_webhook(secret: str, body: bytes, timestamp: int) -> str:
    """Header value for X-KYC-Signature. Receivers recompute HMAC over f"{t}.{body}"
    and reject timestamps older than ~5 minutes to stop replays."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def check_webhook_url(url: str, allow_http: bool = False) -> None:
    """Raise ValueError unless url is https to a public host.

    ponytail: resolves once here, httpx resolves again on send, so DNS rebinding can slip
    through. Pin the resolved IP in a custom transport if tenants become untrusted.
    """
    parts = urlsplit(url)
    if parts.scheme not in (("https", "http") if allow_http else ("https",)):
        raise ValueError("webhook url must use https")
    if not parts.hostname:
        raise ValueError("webhook url has no host")
    if allow_http:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError("webhook host does not resolve") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("webhook host resolves to a non-public address")


def check_redirect_url(url: str, allow_http: bool = False) -> None:
    """Raise ValueError unless url is an absolute https (or dev http) URL. The browser follows it,
    not the server, so no SSRF resolution check is needed; this only blocks javascript:/data: etc."""
    parts = urlsplit(url)
    if parts.scheme not in (("https", "http") if allow_http else ("https",)) or not parts.hostname:
        raise ValueError("redirect url must be an absolute https url")


def mask_nik(nik: str | None) -> str | None:
    if not nik:
        return nik
    return nik[:4] + "*" * max(0, len(nik) - 8) + nik[-4:]
