"""Verification pipeline as a LangGraph StateGraph.

preprocess ─┬─► ktp_extract (LLM) ─► nik_rules ─┐
            └─► biometrics (local) ─────────────┴─► llm_review (LLM) ─► decide
any image issue short-circuits straight to decide.
"""

import asyncio
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.pipeline import decide as policy
from app.pipeline import face, images
from app.pipeline.ktp import KtpExtraction, nik_rules
from app.pipeline.llm import LLM, Review


class State(TypedDict, total=False):
    # inputs
    ktp: bytes  # sanitized JPEG
    selfie: bytes
    today: date
    # signals
    image_issues: list[str]
    quality: dict
    extraction: KtpExtraction | None
    nik_codes: list[str]
    face: dict | None
    review: Review | None
    # output
    reasons: list[str]
    decision: str


def build_graph(llm: LLM, s: Settings):
    def _reasons(st: State, require_review: bool) -> list[str]:
        return policy.reasons(
            image_issues=st.get("image_issues", []),
            extraction=st.get("extraction"),
            nik_codes=st.get("nik_codes", []),
            face=st.get("face"),
            review=st.get("review"),
            face_threshold=s.face_match_threshold,
            liveness_threshold=s.liveness_threshold,
            require_review=require_review,
        )

    def preprocess(st: State) -> State:
        issues, quality = [], {}
        for name in ("ktp", "selfie"):
            i, q = images.quality_issues(st[name], s.min_image_side, s.min_blur_variance, s.min_brightness)
            issues += i
            quality[name] = q
        return {"image_issues": sorted(set(issues)), "quality": quality}

    def route(st: State) -> list[str]:
        return ["decide"] if st["image_issues"] else ["ktp_extract", "biometrics"]

    async def ktp_extract(st: State) -> State:
        return {"extraction": await llm.extract_ktp(st["ktp"])}

    def nik(st: State) -> State:
        ex = st["extraction"]
        return {"nik_codes": nik_rules(ex.data, st["today"]) if ex.data else []}

    async def biometrics(st: State) -> State:
        ktp, selfie = images.to_bgr(st["ktp"]), images.to_bgr(st["selfie"])
        return {"face": await asyncio.to_thread(face.analyze, ktp, selfie, s.models_dir)}

    async def llm_review(st: State) -> State:
        if _reasons(st, require_review=False):
            return {}  # already rejected, skip the second (paid) LLM call
        d = st["extraction"].data
        return {"review": await llm.review(st["ktp"], st["selfie"], d.nik, d.nama)}

    def decide(st: State) -> State:
        r = _reasons(st, require_review=True)
        return {"reasons": r, "decision": policy.decision(r)}

    g = StateGraph(State)
    g.add_node("preprocess", preprocess)
    g.add_node("ktp_extract", ktp_extract)
    g.add_node("nik_rules", nik)
    g.add_node("biometrics", biometrics)
    g.add_node("llm_review", llm_review)
    g.add_node("decide", decide)
    g.add_edge(START, "preprocess")
    g.add_conditional_edges("preprocess", route, ["decide", "ktp_extract", "biometrics"])
    g.add_edge("ktp_extract", "nik_rules")
    g.add_edge(["nik_rules", "biometrics"], "llm_review")
    g.add_edge("llm_review", "decide")
    g.add_edge("decide", END)
    return g.compile()
