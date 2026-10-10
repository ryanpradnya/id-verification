"""Lite self-hosting backends: local encrypted storage, in-process queue, SQLite migrations."""

import os
import sqlite3
import subprocess
import sys
import threading

import pytest

pytest.importorskip("fastapi")  # needs the [server] extra

from id_verification.server import jobs, queue, storage  # noqa: E402
from id_verification.server.db import Verification, session  # noqa: E402
from tests.conftest import make_tenant  # noqa: E402


def test_local_storage_encrypts_and_stays_in_root(tmp_path, monkeypatch):
    local = storage.LocalStorage(tmp_path)
    monkeypatch.setattr(storage, "backend", lambda: local)
    storage.put("t/v/ktp.jpg", b"secret image")
    assert b"secret image" not in (tmp_path / "t/v/ktp.jpg").read_bytes()  # Fernet ciphertext on disk
    assert storage.get("t/v/ktp.jpg") == b"secret image"
    storage.delete("t/v/ktp.jpg")
    assert not (tmp_path / "t/v/ktp.jpg").exists()
    with pytest.raises(ValueError):
        local.put("../escape.jpg", b"x")


def test_local_queue_retries_then_chains_webhook(monkeypatch):
    calls, chained, done = [], [], threading.Event()

    def flaky(vid, attempt):
        calls.append(attempt)
        if attempt == 0:
            raise jobs.Retry(0.01)

    monkeypatch.setitem(queue.JOBS, "process_verification", flaky)
    monkeypatch.setattr(queue, "enqueue", lambda job, vid: (chained.append((job, vid)), done.set()))
    q = queue.LocalQueue(1)
    q.submit("process_verification", "v1")
    assert done.wait(5)
    q.stop()
    assert calls == [0, 1] and chained == [("deliver_webhook", "v1")]


def test_local_queue_recovers_unfinished_work_on_start(monkeypatch):
    tenant, _ = make_tenant()
    with session() as db:
        for vid, status in (("p", "pending"), ("r", "processing"), ("c", "completed")):
            db.add(Verification(id=vid, tenant_id=tenant.id, ktp_key="k", selfie_key="s", status=status))
        db.commit()
    seen, both = [], threading.Event()

    def record(vid, attempt):
        seen.append(vid)
        if len(seen) == 2:
            both.set()

    monkeypatch.setitem(queue.JOBS, "process_verification", record)
    monkeypatch.setattr(queue, "enqueue", lambda job, vid: None)
    monkeypatch.setattr(jobs, "purge_expired", lambda: 0)
    q = queue.LocalQueue(1)
    q.start()
    assert both.wait(5)
    q.stop()
    assert sorted(seen) == ["p", "r"]


def test_migrations_build_the_schema_on_fresh_sqlite(tmp_path):
    env = {**os.environ, "DATA_DIR": str(tmp_path), "DATABASE_URL": ""}
    subprocess.run([sys.executable, "-c", "from id_verification.server import migrate; migrate()"], env=env, check=True)
    con = sqlite3.connect(tmp_path / "idv.db")
    cols = {t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")} for t in ("verifications", "capture_sessions")}
    assert "skipped_checks" in cols["verifications"] and "redirect_url" in cols["capture_sessions"]
