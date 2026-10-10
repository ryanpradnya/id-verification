"""Background jobs as plain functions: run a verification, deliver its webhook, purge old data.

They know nothing about the queue. A job raises Retry(after) to be run again later;
server.queue runs them in-process (lite) or on Celery (REDIS_URL set).
"""

import asyncio
import logging
import threading
import time
from datetime import timedelta
from functools import lru_cache

import httpx
from sqlalchemy import select

from id_verification.config import get_settings
from id_verification.core.llm import ProviderError
from id_verification.core.verifier import Verifier, scores
from id_verification.server import security, storage
from id_verification.server.db import AuditEvent, Tenant, Verification, session, utcnow
from id_verification.server.schemas import verification_out

log = logging.getLogger(__name__)

VERIFY_MAX_RETRIES = 3
WEBHOOK_MAX_RETRIES = 6


class Retry(Exception):  # noqa: N818  control flow, not an error
    def __init__(self, after: float):
        self.after = after


@lru_cache
def _verifier() -> Verifier:
    return Verifier(get_settings())


@lru_cache
def _loop() -> asyncio.AbstractEventLoop:
    # One loop thread per process: LLM SDK async clients are bound to the loop they were created on,
    # and several worker threads share them.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="idv-loop", daemon=True).start()
    return loop


def _audit(db, vid: str, step: str, payload: dict) -> None:
    db.add(AuditEvent(verification_id=vid, step=step, payload=payload))


def _record(db, v: Verification, out: dict, model: str) -> None:
    ex, review, face = out.get("extraction"), out.get("review"), out.get("face") or {}
    data = ex.data if ex else None
    v.decision = out["decision"]
    v.reason_codes = out["reasons"]
    v.skipped_checks = out["skipped_checks"]
    v.ktp_enc = security.encrypt_str(data.model_dump_json()) if data else None
    v.scores = scores(out)
    _audit(db, v.id, "preprocess", {"issues": out.get("image_issues", []), "quality": out.get("quality", {})})
    if ex:
        flags = ex.model_dump(exclude={"data"})
        nik = security.mask_nik(data.nik) if data else None
        _audit(db, v.id, "ktp_extract", {**flags, "model": model, "nik": nik})
        _audit(db, v.id, "nik_rules", {"codes": out.get("nik_codes", [])})
    if face:
        _audit(db, v.id, "biometrics", face)
    if review:
        _audit(db, v.id, "llm_review", {**review.model_dump(), "model": model})
    _audit(db, v.id, "decide", {"decision": v.decision, "reasons": v.reason_codes, "skipped": v.skipped_checks})


def process_verification(vid: str, attempt: int = 0) -> None:
    with session() as db:
        v = db.get(Verification, vid)
        if v is None or v.status in ("completed", "failed"):
            return
        v.status = "processing"
        db.commit()
        verifier = _verifier()
        try:
            coro = verifier.run(storage.get(v.ktp_key), storage.get(v.selfie_key))
            out = asyncio.run_coroutine_threadsafe(coro, _loop()).result()
        except Exception as e:
            log.warning("verification %s attempt %s failed: %s", vid, attempt + 1, type(e).__name__)
            if attempt < VERIFY_MAX_RETRIES:
                v.status = "pending"
                db.commit()
                raise Retry(10 * 2**attempt) from e
            # A provider outage is a system failure, never a rejection.
            v.status = "failed"
            v.error = "AI_PROVIDER_UNAVAILABLE" if isinstance(e, ProviderError) else "INTERNAL_ERROR"
            _audit(db, vid, "error", {"error": v.error, "type": type(e).__name__})
        else:
            _record(db, v, out, verifier.settings.llm_model)
            v.status = "completed"
        v.completed_at = utcnow()
        db.commit()


def deliver_webhook(vid: str, attempt: int = 0) -> None:
    settings = get_settings()
    with session() as db:
        v = db.get(Verification, vid)
        tenant = db.get(Tenant, v.tenant_id)
        url = v.callback_url or tenant.webhook_url
        if not url:
            return
        try:
            security.check_webhook_url(url, settings.webhook_allow_http)
        except ValueError as e:
            _audit(db, vid, "webhook", {"error": str(e)})
            db.commit()
            return
        body = verification_out(v).model_dump_json().encode()
        sig = security.sign_webhook(security.decrypt_str(tenant.webhook_secret_enc), body, int(time.time()))
        try:
            resp = httpx.post(
                url,
                content=body,
                headers={"Content-Type": "application/json", "X-KYC-Signature": sig},
                timeout=10,
                follow_redirects=False,
            )
            ok = resp.status_code < 300
            _audit(db, vid, "webhook", {"status_code": resp.status_code, "attempt": attempt + 1})
        except httpx.HTTPError as e:
            ok = False
            _audit(db, vid, "webhook", {"error": type(e).__name__, "attempt": attempt + 1})
        db.commit()
    if not ok and attempt < WEBHOOK_MAX_RETRIES:
        raise Retry(30 * 2**attempt)


def purge_expired() -> int:
    """Delete images and full KTP data past the retention window. Decisions and audit stay."""
    cutoff = utcnow() - timedelta(days=get_settings().retention_days)
    with session() as db:
        rows = db.scalars(
            select(Verification).where(Verification.created_at < cutoff, Verification.images_deleted_at.is_(None))
        ).all()
        for v in rows:
            for key in (v.ktp_key, v.selfie_key):
                storage.delete(key)
            v.ktp_enc = None
            v.images_deleted_at = utcnow()
        db.commit()
    log.info("purged %d verifications", len(rows))
    return len(rows)
