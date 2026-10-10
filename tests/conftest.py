import io
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

# Settings are read at import time, so configure before importing the app.
_tmp = tempfile.mkdtemp()
os.environ.update(
    DATA_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    REDIS_URL="redis://localhost:6399/0",
    LLM_MODEL="openai:test-model",
    OPENAI_API_KEY="test",
    WEBHOOK_ALLOW_HTTP="false",
)

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from app import security  # noqa: E402
from app.db import Base, Tenant, engine, session  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"sample image {name} missing (tests/fixtures is gitignored, see README)")
    return path.read_bytes()


def jpeg(w: int = 640, h: int = 480) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (120, 140, 160)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def db_schema():
    Base.metadata.drop_all(engine())
    Base.metadata.create_all(engine())
    yield


@pytest.fixture
def fake_infra(monkeypatch):
    """In-memory storage, no Redis, no broker. Returns (blobs, enqueued ids)."""
    import app.main as main
    import app.worker as worker

    blobs, queued = {}, []
    monkeypatch.setattr("app.storage.put", lambda k, d: blobs.__setitem__(k, d))
    monkeypatch.setattr("app.storage.get", lambda k: blobs[k])
    monkeypatch.setattr("app.storage.delete", lambda k: blobs.pop(k, None))
    monkeypatch.setattr(main, "rate_hit", lambda key: 1)
    monkeypatch.setattr(worker.process_verification, "delay", lambda vid: queued.append(vid))
    monkeypatch.setattr(worker.deliver_webhook, "delay", lambda vid: None)
    return blobs, queued


def make_tenant(name: str = "acme", webhook_url: str | None = None) -> tuple[Tenant, str]:
    key = security.new_api_key()
    with session() as db:
        t = Tenant(
            name=name,
            api_key_hash=security.sha256(key),
            webhook_url=webhook_url,
            webhook_secret_enc=security.encrypt_str("whsec"),
        )
        db.add(t)
        db.commit()
    return t, key
