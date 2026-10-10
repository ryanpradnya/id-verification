"""The decision policy. Pure function: same signals in, same decision out. No AI here."""

from id_verification.core.ktp import KtpExtraction
from id_verification.core.llm import Review


def reasons(
    *,
    image_issues: list[str],
    extraction: KtpExtraction | None,
    nik_codes: list[str],
    face: dict | None,
    review: Review | None,
    face_threshold: float,
    liveness_threshold: float,
    require_review: bool = True,
) -> list[str]:
    """Every failed check as a reason code. Empty list means approve."""
    r = list(image_issues)

    if extraction is not None:
        if not extraction.is_indonesian_ktp:
            r.append("NOT_A_KTP")
        if extraction.tampering_suspected:
            r.append("KTP_TAMPERED")
        if extraction.recaptured:
            r.append("KTP_RECAPTURED")
        if extraction.data is None:
            r.append("KTP_UNREADABLE")
    r += nik_codes

    if face is not None:
        if not face["ktp_face_found"]:
            r.append("KTP_FACE_NOT_FOUND")
        if face["selfie_face_count"] == 0:
            r.append("SELFIE_FACE_NOT_FOUND")
        sim, live = face["face_similarity"], face["liveness_score"]
        if sim is not None and sim < face_threshold:
            r.append("FACE_MISMATCH")
        if live is not None and live < liveness_threshold:
            r.append("LIVENESS_FAILED")

    if review is not None:
        # LLM disagreeing with the local face model is a rejection on its own.
        if not review.same_person:
            r.append("LLM_FACE_MISMATCH")
        if review.selfie_spoof_suspected:
            r.append("LLM_SPOOF_SUSPECTED")
        if review.selfie_card_matches_ktp is False:
            r.append("SELFIE_KTP_MISMATCH")
    elif require_review and not r:
        r.append("REVIEW_INCOMPLETE")  # never approve without every check having run

    return sorted(set(r))


def decision(reason_codes: list[str]) -> str:
    return "rejected" if reason_codes else "approved"
