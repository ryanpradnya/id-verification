"""Full LangGraph pipeline with real local face models on the sample images and a fake LLM,
plus the worker task end to end (storage and webhook faked)."""

import asyncio
from datetime import date

import pytest

from id_verification.config import get_settings
from id_verification.core.graph import NO_LLM_SKIPPED, build_graph
from id_verification.core.images import sanitize
from id_verification.core.ktp import KtpData, KtpExtraction
from id_verification.core.llm import ProviderError, Review
from id_verification.core.verifier import Verifier
from tests.conftest import HAS_SERVER, SPECIMEN, fixture_bytes, make_tenant

settings = get_settings()
if not (settings.models_dir / "MiniFASNetV2.onnx").exists():
    pytest.skip("models missing: run `id-verification download-models` with MODELS_DIR=models", allow_module_level=True)

SELFIE2_KTP = KtpData(  # synthetic data; the fake LLM "reads" it
    nik="3601097005800007",
    nama="TEST PEREMPUAN",
    tanggal_lahir=date(1980, 5, 30),
    jenis_kelamin="PEREMPUAN",
    kewarganegaraan="WNI",
    berlaku_hingga="SEUMUR HIDUP",
    provinsi="BANTEN",
)
KTP1 = KtpData(
    nik="3203091503800007",
    nama="WNA SPESIMEN",
    tanggal_lahir=date(1980, 3, 15),
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


@pytest.mark.skipif(not HAS_SERVER, reason="needs the [server] extra")
def test_worker_end_to_end(fake_infra, monkeypatch):
    from id_verification.server import jobs
    from id_verification.server.db import AuditEvent, Verification, session
    from id_verification.server.schemas import verification_out

    blobs, _ = fake_infra
    tenant, _ = make_tenant()
    img = sanitize(fixture_bytes("selfie2.png"), 10 << 20)
    blobs["k"], blobs["s"] = img, img
    with session() as db:
        db.add(Verification(id="v1", tenant_id=tenant.id, ktp_key="k", selfie_key="s"))
        db.add(Verification(id="v2", tenant_id=tenant.id, ktp_key="k", selfie_key="s"))
        db.add(Verification(id="v3", tenant_id=tenant.id, ktp_key="k", selfie_key="s"))
        db.commit()

    monkeypatch.setattr(jobs, "_verifier", lambda: Verifier(settings, llm=FakeLLM(SELFIE2_KTP)))
    jobs.process_verification("v1")

    monkeypatch.setattr(jobs, "_verifier", lambda: Verifier(settings, llm=FakeLLM(SELFIE2_KTP, fail=True)))
    with pytest.raises(jobs.Retry):  # retries left: back to pending, never a rejection
        jobs.process_verification("v3")
    jobs.process_verification("v2", attempt=jobs.VERIFY_MAX_RETRIES)

    with session() as db:
        ok = verification_out(db.get(Verification, "v1"))
        assert ok.status == "completed" and ok.decision == "approved" and ok.skipped_checks == []
        assert ok.ktp.nik == SELFIE2_KTP.nik and ok.ktp.nama == SELFIE2_KTP.nama
        assert set(ok.ktp.model_dump()) == {"nik", "nama"}  # never the full KTP
        steps = {e.step for e in db.query(AuditEvent).filter_by(verification_id="v1")}
        assert {"preprocess", "ktp_extract", "biometrics", "llm_review", "decide"} <= steps

        assert db.get(Verification, "v3").status == "pending"
        failed = db.get(Verification, "v2")
        assert failed.status == "failed" and failed.error == "AI_PROVIDER_UNAVAILABLE" and failed.decision is None


def test_no_llm_mode_reports_skipped_checks():
    pytest.importorskip("rapidocr")
    v = Verifier(settings.model_copy(update={"llm_model": "none"}))
    assert v.mode == "no-llm"
    # Specimen has no face: rejected on biometrics, but the OCR read and NIK rules ran.
    r = asyncio.run(v.verify(SPECIMEN.read_bytes(), fixture_bytes("selfie2.png")))
    assert r.nik == "3171011208900001" and r.nama == "BUDI SPESIMEN"
    assert r.skipped_checks == NO_LLM_SKIPPED
    assert r.decision == "rejected" and "KTP_FACE_NOT_FOUND" in r.reasons
    assert not any(c.startswith("NIK_") for c in r.reasons)
