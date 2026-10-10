"""Pure-function checks: NIK rules, decision policy, security helpers."""

import hashlib
import hmac
from datetime import date

import pytest

from app import security
from app.pipeline.decide import decision, reasons
from app.pipeline.ktp import KtpData, KtpExtraction, nik_rules
from app.pipeline.llm import Review

TODAY = date(2026, 10, 10)


def ktp(**kw) -> KtpData:
    base = dict(
        nik="7471031102020001",  # Sulawesi Tenggara, 11-02-02, male
        nama="MUH. QASHRUDDIN KASO",
        tanggal_lahir=date(2002, 2, 11),
        jenis_kelamin="LAKI-LAKI",
        kewarganegaraan="WNI",
        berlaku_hingga="SEUMUR HIDUP",
        provinsi="SULAWESI TENGGARA",
    )
    return KtpData(**{**base, **kw})


def test_valid_ktp_has_no_codes():
    assert nik_rules(ktp(), TODAY) == []


def test_female_day_offset():
    assert nik_rules(ktp(nik="7471035102020001", jenis_kelamin="PEREMPUAN"), TODAY) == []
    assert "NIK_GENDER_MISMATCH" in nik_rules(ktp(nik="7471035102020001"), TODAY)


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"nik": "747103110202"}, "NIK_INVALID_FORMAT"),
        ({"nik": "7471031102020000"}, "NIK_INVALID_FORMAT"),
        ({"nik": "7471033502020001"}, "NIK_INVALID_FORMAT"),  # day 35
        ({"nik": "9971031102020001", "provinsi": None}, "NIK_INVALID_PROVINCE"),
        ({"provinsi": "JAWA BARAT"}, "NIK_PROVINCE_MISMATCH"),
        ({"tanggal_lahir": date(2002, 2, 12)}, "NIK_BIRTHDATE_MISMATCH"),
        ({"jenis_kelamin": "PEREMPUAN"}, "NIK_GENDER_MISMATCH"),
        ({"jenis_kelamin": None}, "KTP_FIELDS_MISSING"),
    ],
)
def test_nik_failures(kw, code):
    assert code in nik_rules(ktp(**kw), TODAY)


def test_expiry_only_applies_to_foreigners():
    # Citizens: lifetime validity even with an old printed date.
    assert nik_rules(ktp(berlaku_hingga="2017-09-04"), TODAY) == []
    # ktp1 sample: WNA (CHINA), expired 2023-12-12.
    wna = ktp(
        nik="3203012503770011",
        tanggal_lahir=date(1977, 3, 25),
        provinsi="JAWA BARAT",
        kewarganegaraan="CHINA",
        berlaku_hingga="2023-12-12",
    )
    assert nik_rules(wna, TODAY) == ["KTP_EXPIRED"]
    assert nik_rules(wna.model_copy(update={"berlaku_hingga": "2030-01-01"}), TODAY) == []
    assert nik_rules(wna.model_copy(update={"berlaku_hingga": None}), TODAY) == ["KTP_EXPIRED"]


def test_papua_split_codes():
    assert nik_rules(ktp(nik="9171031102020001", provinsi="PAPUA SELATAN"), TODAY) == []


GOOD_FACE = {"ktp_face_found": True, "selfie_face_count": 2, "face_similarity": 0.7, "liveness_score": 0.99}
GOOD_EX = KtpExtraction(is_indonesian_ktp=True, tampering_suspected=False, recaptured=False, data=ktp())
GOOD_REVIEW = Review(same_person=True, selfie_spoof_suspected=False, selfie_card_matches_ktp=True)


def r(**kw):
    args = dict(
        image_issues=[],
        extraction=GOOD_EX,
        nik_codes=[],
        face=GOOD_FACE,
        review=GOOD_REVIEW,
        face_threshold=0.363,
        liveness_threshold=0.8,
    )
    return reasons(**{**args, **kw})


def test_all_good_approves():
    assert r() == []
    assert decision(r()) == "approved"


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"image_issues": ["IMAGE_TOO_BLURRY"]}, "IMAGE_TOO_BLURRY"),
        ({"extraction": GOOD_EX.model_copy(update={"is_indonesian_ktp": False})}, "NOT_A_KTP"),
        ({"extraction": GOOD_EX.model_copy(update={"tampering_suspected": True})}, "KTP_TAMPERED"),
        ({"extraction": GOOD_EX.model_copy(update={"recaptured": True})}, "KTP_RECAPTURED"),
        ({"extraction": GOOD_EX.model_copy(update={"data": None})}, "KTP_UNREADABLE"),
        ({"nik_codes": ["KTP_EXPIRED"]}, "KTP_EXPIRED"),
        ({"face": {**GOOD_FACE, "face_similarity": 0.2}}, "FACE_MISMATCH"),
        ({"face": {**GOOD_FACE, "liveness_score": 0.3}}, "LIVENESS_FAILED"),
        ({"face": {**GOOD_FACE, "ktp_face_found": False, "face_similarity": None}}, "KTP_FACE_NOT_FOUND"),
        (
            {"face": {**GOOD_FACE, "selfie_face_count": 0, "face_similarity": None, "liveness_score": None}},
            "SELFIE_FACE_NOT_FOUND",
        ),
        ({"review": GOOD_REVIEW.model_copy(update={"same_person": False})}, "LLM_FACE_MISMATCH"),
        ({"review": GOOD_REVIEW.model_copy(update={"selfie_spoof_suspected": True})}, "LLM_SPOOF_SUSPECTED"),
        ({"review": GOOD_REVIEW.model_copy(update={"selfie_card_matches_ktp": False})}, "SELFIE_KTP_MISMATCH"),
        ({"review": None}, "REVIEW_INCOMPLETE"),
    ],
)
def test_each_failure_rejects(kw, code):
    codes = r(**kw)
    assert code in codes
    assert decision(codes) == "rejected"


def test_card_not_visible_is_not_a_failure():
    assert r(review=GOOD_REVIEW.model_copy(update={"selfie_card_matches_ktp": None})) == []


def test_webhook_signature_roundtrip():
    body = b'{"id":"x"}'
    header = security.sign_webhook("whsec", body, 1700000000)
    t, v1 = (p.split("=", 1)[1] for p in header.split(","))
    expected = hmac.new(b"whsec", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert t == "1700000000" and hmac.compare_digest(v1, expected)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest",
        "https://localhost/x",
        "ftp://example.com",
    ],
)
def test_ssrf_guard_blocks(url):
    with pytest.raises(ValueError):
        security.check_webhook_url(url)


def test_ssrf_guard_allows_public_ip():
    security.check_webhook_url("https://1.1.1.1/hook")


def test_mask_and_encrypt():
    assert security.mask_nik("7471031102020001") == "7471********0001"
    assert security.decrypt_str(security.encrypt_str("rahasia")) == "rahasia"
