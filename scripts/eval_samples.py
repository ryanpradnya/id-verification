"""Run KTP/selfie pairs through the pipeline and print a decision table, for tuning thresholds.

uv run python scripts/eval_samples.py tests/fixtures/ktp1.jpg tests/fixtures/selfie2.png [KTP SELFIE ...]
uv run python scripts/eval_samples.py --biometrics-only tests/fixtures/selfie2.png tests/fixtures/selfie2.png

Full mode uses the configured LLM_MODEL (a cloud provider costs money; "none" = local OCR).
--biometrics-only runs the local face models only.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from id_verification.config import get_settings
from id_verification.core import face, images


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--biometrics-only", action="store_true")
    p.add_argument("paths", nargs="+", help="KTP SELFIE pairs")
    args = p.parse_args()
    if len(args.paths) % 2:
        p.error("paths must be KTP SELFIE pairs")
    s = get_settings()
    pairs = list(zip(args.paths[::2], args.paths[1::2], strict=True))

    verifier = None
    if not args.biometrics_only:
        from id_verification import Verifier

        verifier = Verifier(s)

    print(f"model={s.llm_model} face_threshold={s.face_match_threshold} liveness_threshold={s.liveness_threshold}")
    for ktp_path, selfie_path in pairs:
        ktp = images.sanitize(Path(ktp_path).read_bytes(), s.max_upload_bytes)
        selfie = images.sanitize(Path(selfie_path).read_bytes(), s.max_upload_bytes)
        label = f"{Path(ktp_path).name} + {Path(selfie_path).name}"
        if verifier is None:
            r = face.analyze(images.to_bgr(ktp), images.to_bgr(selfie), s.models_dir)
            print(f"{label:40} sim={r['face_similarity']} live={r['liveness_score']} faces={r['selfie_face_count']}")
            continue
        out = asyncio.run(verifier.run(ktp, selfie))
        f = out.get("face") or {}
        print(
            f"{label:40} {out['decision']:9} sim={f.get('face_similarity')} live={f.get('liveness_score')} "
            f"reasons={','.join(out['reasons']) or '-'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
