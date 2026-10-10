"""SQLAlchemy models and session. Sync engine: FastAPI runs sync deps in a threadpool,
and the Celery worker is sync anyway."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo; treat naive values as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    webhook_url: Mapped[str | None] = mapped_column(Text)
    webhook_secret_enc: Mapped[str] = mapped_column(Text)  # Fernet
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    external_ref: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    callback_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|completed|failed
    decision: Mapped[str | None] = mapped_column(String(20))  # approved|rejected
    reason_codes: Mapped[list] = mapped_column(JSONType, default=list)
    ktp_enc: Mapped[str | None] = mapped_column(Text)  # Fernet(JSON KtpData), wiped at retention
    scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    ktp_key: Mapped[str] = mapped_column(Text)
    selfie_key: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    images_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaptureSession(Base):
    __tablename__ = "capture_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    external_ref: Mapped[str | None] = mapped_column(String(200))
    callback_url: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_id: Mapped[str | None] = mapped_column(ForeignKey("verifications.id"))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    verification_id: Mapped[str] = mapped_column(ForeignKey("verifications.id"), index=True)
    step: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@lru_cache
def engine():
    url = get_settings().database_url
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=args)


@lru_cache
def _sessionmaker():
    return sessionmaker(engine(), expire_on_commit=False)


@contextmanager
def session() -> Iterator[Session]:
    with _sessionmaker()() as s:
        yield s


def get_db() -> Iterator[Session]:
    with session() as s:
        yield s
