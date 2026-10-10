"""Public API shapes. Clients only ever see decision + NIK + name, never the full KTP fields."""

import json
from datetime import datetime

from pydantic import BaseModel, Field

from app import security
from app.db import Verification


class VerificationJSON(BaseModel):
    ktp_base64: str = Field(description="KTP image (jpeg/png/webp), base64")
    selfie_base64: str = Field(description="Selfie image (jpeg/png/webp), base64")
    external_ref: str | None = Field(None, max_length=200)
    callback_url: str | None = Field(None, description="Overrides the tenant webhook for this verification")


class SessionCreate(BaseModel):
    external_ref: str | None = Field(None, max_length=200)
    callback_url: str | None = None


class SessionOut(BaseModel):
    session_id: str
    capture_url: str
    expires_at: datetime


class KtpSummary(BaseModel):
    nik: str
    nama: str


class VerificationOut(BaseModel):
    id: str
    external_ref: str | None
    status: str
    decision: str | None
    reason_codes: list[str]
    ktp: KtpSummary | None
    scores: dict
    error: str | None
    created_at: datetime
    completed_at: datetime | None


def verification_out(v: Verification) -> VerificationOut:
    ktp = None
    if v.ktp_enc:
        d = json.loads(security.decrypt_str(v.ktp_enc))
        ktp = KtpSummary(nik=d["nik"], nama=d["nama"])
    return VerificationOut(
        id=v.id,
        external_ref=v.external_ref,
        status=v.status,
        decision=v.decision,
        reason_codes=v.reason_codes or [],
        ktp=ktp,
        scores=v.scores or {},
        error=v.error,
        created_at=v.created_at,
        completed_at=v.completed_at,
    )
