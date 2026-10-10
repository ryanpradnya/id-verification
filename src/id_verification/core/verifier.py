"""Library entry point: Verifier(settings).verify(ktp_bytes, selfie_bytes)."""

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel

from id_verification.config import Settings
from id_verification.core.graph import build_graph
from id_verification.core.images import sanitize
from id_verification.core.llm import LLM
from id_verification.core.models import ensure_models


class VerifyResult(BaseModel):
    decision: Literal["approved", "rejected"]
    reasons: list[str]  # reason codes; empty when approved
    nik: str | None
    nama: str | None
    skipped_checks: list[str]  # checks that could not run in this mode (no-LLM)
    scores: dict


def scores(out: dict) -> dict:
    face, review = out.get("face") or {}, out.get("review")
    return {
        "face_similarity": face.get("face_similarity"),
        "liveness": face.get("liveness_score"),
        "llm_same_person": review.same_person if review else None,
    }


class Verifier:
    """One instance per process: models and LLM clients load once.

    llm: override the configured LLM (anything with async extract_ktp/review), mainly for tests.
    """

    def __init__(self, settings: Settings | None = None, llm=None):
        self.settings = s = settings or Settings()
        ensure_models(s.models_dir)
        self.llm = llm if llm is not None else (None if s.llm_model == "none" else LLM(s))
        self._graph = build_graph(self.llm, s)

    @property
    def mode(self) -> str:
        return "no-llm" if self.llm is None else "llm"

    async def run(self, ktp: bytes, selfie: bytes, today: date | None = None) -> dict:
        """Raw pipeline state for already-sanitized JPEGs (the server uses it for its audit log)."""
        state = {"ktp": ktp, "selfie": selfie, "today": today or datetime.now(UTC).date()}
        return await self._graph.ainvoke(state)

    async def verify(self, ktp: bytes, selfie: bytes) -> VerifyResult:
        """ktp/selfie: jpeg, png or webp bytes. Raises InvalidImage for bad input and
        ProviderError when every AI provider fails (a system error, not a rejection)."""
        limit = self.settings.max_upload_bytes
        out = await self.run(sanitize(ktp, limit), sanitize(selfie, limit))
        ex = out.get("extraction")
        data = ex.data if ex else None
        return VerifyResult(
            decision=out["decision"],
            reasons=out["reasons"],
            nik=data.nik if data else None,
            nama=data.nama if data else None,
            skipped_checks=out["skipped_checks"],
            scores=scores(out),
        )
