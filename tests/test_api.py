"""API: auth, tenant isolation, idempotency, uploads, hosted capture sessions."""

import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import CaptureSession, session, utcnow
from app.main import app
from tests.conftest import jpeg, make_tenant


@pytest.fixture
def client(fake_infra):
    return TestClient(app)


def files():
    return {"ktp": ("ktp.jpg", jpeg(), "image/jpeg"), "selfie": ("selfie.jpg", jpeg(), "image/jpeg")}


def test_requires_api_key(client):
    assert client.post("/v1/verifications", files=files()).status_code == 401
    r = client.post("/v1/verifications", files=files(), headers={"X-API-Key": "kyc_wrong"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_multipart_create_and_get(client, fake_infra):
    blobs, queued = fake_infra
    _, key = make_tenant()
    r = client.post("/v1/verifications", files=files(), data={"external_ref": "u-1"}, headers={"X-API-Key": key})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending" and body["external_ref"] == "u-1" and body["ktp"] is None
    assert queued == [body["id"]]
    assert len(blobs) == 2 and all(v.startswith(b"\xff\xd8") for v in blobs.values())  # re-encoded JPEG

    got = client.get(f"/v1/verifications/{body['id']}", headers={"X-API-Key": key})
    assert got.status_code == 200 and got.json()["id"] == body["id"]


def test_json_base64_create(client):
    _, key = make_tenant()
    b64 = base64.b64encode(jpeg()).decode()
    r = client.post("/v1/verifications", json={"ktp_base64": b64, "selfie_base64": b64}, headers={"X-API-Key": key})
    assert r.status_code == 202, r.text
    bad = client.post("/v1/verifications", json={"ktp_base64": "!!", "selfie_base64": b64}, headers={"X-API-Key": key})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_IMAGE"


def test_rejects_non_image(client):
    _, key = make_tenant()
    f = {"ktp": ("ktp.jpg", b"<?php echo 1; ?>", "image/jpeg"), "selfie": ("s.jpg", jpeg(), "image/jpeg")}
    r = client.post("/v1/verifications", files=f, headers={"X-API-Key": key})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_IMAGE"


def test_rejects_private_callback(client):
    _, key = make_tenant()
    r = client.post(
        "/v1/verifications", files=files(), data={"callback_url": "https://127.0.0.1/x"}, headers={"X-API-Key": key}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CALLBACK_URL"


def test_tenant_isolation(client):
    _, key_a = make_tenant("a")
    _, key_b = make_tenant("b")
    vid = client.post("/v1/verifications", files=files(), headers={"X-API-Key": key_a}).json()["id"]
    assert client.get(f"/v1/verifications/{vid}", headers={"X-API-Key": key_b}).status_code == 404


def test_idempotency_key(client, fake_infra):
    _, queued = fake_infra
    _, key = make_tenant()
    h = {"X-API-Key": key, "Idempotency-Key": "abc"}
    first = client.post("/v1/verifications", files=files(), headers=h)
    second = client.post("/v1/verifications", files=files(), headers=h)
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(queued) == 1


def _session(client, key) -> str:
    r = client.post("/v1/sessions", json={"external_ref": "u-9"}, headers={"X-API-Key": key})
    assert r.status_code == 201, r.text
    return r.json()["capture_url"].split("/capture/")[1]


def test_capture_flow_single_use(client, fake_infra):
    _, queued = fake_infra
    _, key = make_tenant()
    token = _session(client, key)

    page = client.get(f"/capture/{token}")
    assert page.status_code == 200
    assert "script-src 'self'" in page.headers["content-security-policy"]
    csrf = page.cookies.get("csrf")
    assert csrf and csrf in page.text

    assert client.post(f"/capture/{token}/submit", files=files()).status_code == 403  # no CSRF header
    ok = client.post(f"/capture/{token}/submit", files=files(), headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200, ok.text
    assert len(queued) == 1

    again = client.post(f"/capture/{token}/submit", files=files(), headers={"X-CSRF-Token": csrf})
    assert again.status_code == 410
    assert client.get(f"/capture/{token}").status_code == 410


def test_capture_expired(client):
    _, key = make_tenant()
    token = _session(client, key)
    with session() as db:
        db.execute(update(CaptureSession).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert client.get(f"/capture/{token}").status_code == 410
