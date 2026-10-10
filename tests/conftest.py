import io
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SPECIMEN = ROOT / "src/id_verification/core/assets/specimen_ktp.jpg"

# Settings are cached on first use, so configure before anything reads them. Lite mode: no Redis, no S3.
_tmp = tempfile.mkdtemp()
os.environ.update(
    DATA_DIR=_tmp,
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    MODELS_DIR=os.environ.get("MODELS_DIR", str(ROOT / "models")),
    LLM_MODEL="openai:test-model",
    OPENAI_API_KEY="test",
    WEBHOOK_ALLOW_HTTP="false",
    # empty, not unset: a developer's .env must not switch the tests to Redis / S3
    REDIS_URL="",
    S3_BUCKET="",
    LLM_BASE_URL="",
)
try:
    from cryptography.fernet import Fernet

    os.environ["DATA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
except ImportError:  # core-only install
    pass

HAS_SERVER = "DATA_ENCRYPTION_KEY" in os.environ


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
    if not HAS_SERVER:
        yield
        return
    from id_verification.server.db import Base, engine

    Base.metadata.drop_all(engine())
    Base.metadata.create_all(engine())
    yield


@pytest.fixture
def fake_infra(monkeypatch):
    """In-memory storage, no rate limit, no background queue. Returns (blobs, enqueued (job, id) pairs)."""
    from id_verification.server import app, queue, storage

    blobs, queued = {}, []
    monkeypatch.setattr(storage, "put", lambda k, d: blobs.__setitem__(k, d))
    monkeypatch.setattr(storage, "get", lambda k: blobs[k])
    monkeypatch.setattr(storage, "delete", lambda k: blobs.pop(k, None))
    monkeypatch.setattr(app, "rate_hit", lambda key: 1)
    monkeypatch.setattr(queue, "enqueue", lambda job, vid: queued.append((job, vid)))
    return blobs, queued


def make_tenant(name: str = "acme", webhook_url: str | None = None):
    from id_verification.server import security
    from id_verification.server.db import Tenant, session

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
