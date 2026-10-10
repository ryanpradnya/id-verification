"""Full LangGraph pipeline with real local face models on the sample images and a fake LLM,
plus the worker task end to end (storage and webhook faked)."""

import asyncio
from datetime import date

import pytest

from app.config import get_settings
from app.pipeline.graph import build_graph
from app.pipeline.images import sanitize
from app.pipeline.ktp import KtpData, KtpExtraction
from app.pipeline.llm import ProviderError, Review
from tests.conftest import fixture_bytes, make_tenant

settings = get_settings()
if not (settings.models_dir / "MiniFASNetV2.onnx").exists():
    pytest.skip("models missing: run `uv run python scripts/download_models.py`", allow_module_level=True)

SELFIE2_KTP = KtpData(  # plausible data; the fake LLM "reads" it
    nik="3601127005800002",
    nama="TEST PEREMPUAN",
    tanggal_lahir=date(1980, 5, 30),
    jenis_kelamin="PEREMPUAN",
    kewarganegaraan="WNI",
    berlaku_hingga="SEUMUR HIDUP",
    provinsi="BANTEN",
)
KTP1 = KtpData(
    nik="3203012503770011",
    nama="GUOHUI CHEN",
    tanggal_lahir=date(1977, 3, 25),
    jenis_kelamin="LAKI-LAKI",
    kewarganegaraan="CHINA",
    berlaku_hingga="2023-12-12",
    provinsi="JAWA BARAT",
)


class FakeLLM:
    model_id = "fake"

    def __init__(self, data: KtpData, same_person: bool = True, fail: bool = False):
        self.data, self.same_person, self.fail, self.review_calls = data, same_person, fail, 0

    async def extract_ktp(self, ktp):
        if self.fail:
            raise ProviderError("down")
        return KtpExtraction(is_indonesian_ktp=True, tampering_suspected=False, recaptured=False, data=self.data)

    async def review(self, ktp, selfie, nik, nama):
        self.review_calls += 1
        return Review(same_person=self.same_person, selfie_spoof_suspected=False, selfie_card_matches_ktp=True)


def run(llm, ktp_name, selfie_name):
    state = {
        "ktp": sanitize(fixture_bytes(ktp_name), 10 << 20),
        "selfie": sanitize(fixture_bytes(selfie_name), 10 << 20),
        "today": date(2026, 10, 10),
    }
    return asyncio.run(build_graph(llm, settings).ainvoke(state))


def test_matching_person_is_approved():
    # selfie2 holds the card next to her face: the card photo stands in for the KTP image.
    llm = FakeLLM(SELFIE2_KTP)
    out = run(llm, "selfie2.png", "selfie2.png")
    assert out["decision"] == "approved", out["reasons"]
    assert out["face"]["face_similarity"] >= settings.face_match_threshold
    assert out["face"]["liveness_score"] >= settings.liveness_threshold
    assert llm.review_calls == 1


def test_different_person_and_expired_wna_is_rejected():
    llm = FakeLLM(KTP1)
    out = run(llm, "ktp1.jpg", "selfie2.png")
    assert out["decision"] == "rejected"
    assert {"FACE_MISMATCH", "KTP_EXPIRED"} <= set(out["reasons"])
    assert llm.review_calls == 0  # already rejected: paid second LLM call skipped


def test_llm_disagreement_rejects():
    out = run(FakeLLM(SELFIE2_KTP, same_person=False), "selfie2.png", "selfie2.png")
    assert out["reasons"] == ["LLM_FACE_MISMATCH"]


def test_small_image_short_circuits():
    out = run(FakeLLM(SELFIE2_KTP), "ktp2.jpg", "selfie5.webp")  # selfie5 is 237px tall
    assert out["reasons"] == ["IMAGE_TOO_SMALL"]
    assert "extraction" not in out


def test_worker_end_to_end(fake_infra, monkeypatch):
    import app.worker as worker
    from app.db import AuditEvent, Verification, session
    from app.schemas import verification_out

    blobs, _ = fake_infra
    tenant, _ = make_tenant()
    img = sanitize(fixture_bytes("selfie2.png"), 10 << 20)
    blobs["k"], blobs["s"] = img, img
    with session() as db:
        db.add(Verification(id="v1", tenant_id=tenant.id, ktp_key="k", selfie_key="s"))
        db.add(Verification(id="v2", tenant_id=tenant.id, ktp_key="k", selfie_key="s"))
        db.commit()

    monkeypatch.setattr(worker, "_graph", lambda: build_graph(FakeLLM(SELFIE2_KTP), settings))
    worker.process_verification("v1")

    monkeypatch.setattr(worker, "_graph", lambda: build_graph(FakeLLM(SELFIE2_KTP, fail=True), settings))
    monkeypatch.setattr(worker.process_verification, "max_retries", 0)
    worker.process_verification("v2")

    with session() as db:
        ok = verification_out(db.get(Verification, "v1"))
        assert ok.status == "completed" and ok.decision == "approved"
        assert ok.ktp.nik == SELFIE2_KTP.nik and ok.ktp.nama == SELFIE2_KTP.nama
        assert set(ok.ktp.model_dump()) == {"nik", "nama"}  # never the full KTP
        steps = {e.step for e in db.query(AuditEvent).filter_by(verification_id="v1")}
        assert {"preprocess", "ktp_extract", "biometrics", "llm_review", "decide"} <= steps

        failed = db.get(Verification, "v2")
        assert failed.status == "failed" and failed.error == "AI_PROVIDER_UNAVAILABLE" and failed.decision is None
