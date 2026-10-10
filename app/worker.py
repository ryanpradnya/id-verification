"""Celery worker: runs the verification graph, delivers signed webhooks, purges old images.

Run: celery -A app.worker worker --beat --loglevel=info
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import httpx
from celery import Celery
from sqlalchemy import select

from app import security, storage
from app.config import get_settings
from app.db import AuditEvent, Tenant, Verification, session, utcnow
from app.pipeline.graph import build_graph
from app.pipeline.llm import LLM, ProviderError
from app.schemas import verification_out

log = logging.getLogger(__name__)
settings = get_settings()

celery = Celery("kyc", broker=settings.redis_url)
celery.conf.update(
    task_acks_late=True,  # a crashed worker's job goes back to the queue
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={"purge-expired-images": {"task": "app.worker.purge_expired", "schedule": 24 * 3600}},
)


@lru_cache
def _graph():
    return build_graph(LLM(settings), settings)


@lru_cache
def _loop() -> asyncio.AbstractEventLoop:
    # One loop per worker process: LLM SDK async clients are bound to the loop they were created on.
    return asyncio.new_event_loop()


def _audit(db, vid: str, step: str, payload: dict) -> None:
    db.add(AuditEvent(verification_id=vid, step=step, payload=payload))


def _record(db, v: Verification, out: dict) -> None:
    ex, review, face = out.get("extraction"), out.get("review"), out.get("face") or {}
    data = ex.data if ex else None
    v.decision = out["decision"]
    v.reason_codes = out["reasons"]
    v.ktp_enc = security.encrypt_str(data.model_dump_json()) if data else None
    v.scores = {
        "face_similarity": face.get("face_similarity"),
        "liveness": face.get("liveness_score"),
        "llm_same_person": review.same_person if review else None,
    }
    _audit(db, v.id, "preprocess", {"issues": out.get("image_issues", []), "quality": out.get("quality", {})})
    if ex:
        flags = ex.model_dump(exclude={"data"})
        nik = security.mask_nik(data.nik) if data else None
        _audit(db, v.id, "ktp_extract", {**flags, "model": settings.llm_model, "nik": nik})
        _audit(db, v.id, "nik_rules", {"codes": out.get("nik_codes", [])})
    if face:
        _audit(db, v.id, "biometrics", face)
    if review:
        _audit(db, v.id, "llm_review", {**review.model_dump(), "model": settings.llm_model})
    _audit(db, v.id, "decide", {"decision": v.decision, "reasons": v.reason_codes})


@celery.task(bind=True, max_retries=3)
def process_verification(self, vid: str) -> None:
    with session() as db:
        v = db.get(Verification, vid)
        if v is None or v.status in ("completed", "failed"):
            return
        v.status = "processing"
        db.commit()
        try:
            state = {
                "ktp": storage.get(v.ktp_key),
                "selfie": storage.get(v.selfie_key),
                "today": datetime.now(UTC).date(),
            }
            out = _loop().run_until_complete(_graph().ainvoke(state))
        except Exception as e:
            log.warning("verification %s attempt %s failed: %s", vid, self.request.retries + 1, type(e).__name__)
            if self.request.retries < self.max_retries:
                v.status = "pending"
                db.commit()
                raise self.retry(exc=e, countdown=10 * 2**self.request.retries) from e
            # A provider outage is a system failure, never a rejection.
            v.status = "failed"
            v.error = "AI_PROVIDER_UNAVAILABLE" if isinstance(e, ProviderError) else "INTERNAL_ERROR"
            _audit(db, vid, "error", {"error": v.error, "type": type(e).__name__})
        else:
            _record(db, v, out)
            v.status = "completed"
        v.completed_at = utcnow()
        db.commit()
    deliver_webhook.delay(vid)


@celery.task(bind=True, max_retries=6)
def deliver_webhook(self, vid: str) -> None:
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
        attempt = self.request.retries + 1
        try:
            resp = httpx.post(
                url,
                content=body,
                headers={"Content-Type": "application/json", "X-KYC-Signature": sig},
                timeout=10,
                follow_redirects=False,
            )
            ok = resp.status_code < 300
            _audit(db, vid, "webhook", {"status_code": resp.status_code, "attempt": attempt})
        except httpx.HTTPError as e:
            ok = False
            _audit(db, vid, "webhook", {"error": type(e).__name__, "attempt": attempt})
        db.commit()
    if not ok and self.request.retries < self.max_retries:
        raise self.retry(countdown=30 * 2**self.request.retries)


@celery.task
def purge_expired() -> int:
    """Delete images and full KTP data past the retention window. Decisions and audit stay."""
    cutoff = utcnow() - timedelta(days=settings.retention_days)
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
