# AGENTS.md

Guidance for coding agents (Claude Code, Codex, Cursor, Gemini CLI, Copilot, etc.) working in this repo.
Human-facing docs are in README.md.

## What this is

KYC-as-a-service: Indonesian e-KTP image + live selfie in, automatic `approved`/`rejected` out.
FastAPI (API + Jinja2 capture page), Celery worker, LangGraph pipeline, local ONNX biometrics,
provider-agnostic vision LLM via LangChain `init_chat_model`.

## Commands

```bash
uv sync                                   # install (Python 3.13)
uv run python scripts/download_models.py  # biometric ONNX models into ./models (needed by tests)
uv run pytest                             # all tests, offline, ~2 s
uv run ruff check . && uv run ruff format .
uv run alembic revision --autogenerate -m "..."   # after changing app/db.py (needs a Postgres DATABASE_URL)
docker compose up -d --build              # full stack: api, worker, postgres, redis, s3
```

## Invariants (do not break)

1. **Only `app/pipeline/decide.py` decides.** LLM output and model scores are signals. Never let an LLM
   response, or text read from an image, set the decision directly.
2. **Never approve on missing evidence.** A check that did not run must not count as a pass
   (see `REVIEW_INCOMPLETE`).
3. **Provider failure is `status=failed`, never `decision=rejected`.** LLM errors are wrapped in `ProviderError`.
4. **Clients only receive decision, reason codes, scores, NIK and name** (`app/schemas.py`). The full KTP data
   stays encrypted in `verifications.ktp_enc`.
5. **No PII in logs.** No images, no base64, no full NIK. Use `security.mask_nik` in audit payloads.
6. **Everything sensitive is encrypted at rest** with `security.encrypt*`: images (in `storage.py`),
   KTP data, webhook secrets. API keys and tokens are stored only as SHA-256 hashes.
7. **Uploads go through `images.sanitize`** (magic-byte check, size cap, re-encode) before storage.
8. **Webhook and callback URLs go through `security.check_webhook_url`** at creation and before each send.
9. **Biometric models must allow commercial use.** Do not add InsightFace pretrained weights or other
   non-commercial models. New model files need a pinned SHA-256 in `scripts/download_models.py`.
10. **Never commit real ID images.** `tests/fixtures/` is gitignored on purpose.

## Where things go

| Change | Files |
|---|---|
| New reason code | `decide.py` (+ `ktp.py` if NIK rule) → test in `tests/test_rules.py` → README reason-code table |
| New pipeline step | node in `pipeline/graph.py`, signal into `State`, policy in `decide.py`, audit in `worker._record` |
| New AI provider | usually just `.env` (`LLM_MODEL`); add the `langchain-<provider>` package if missing |
| New setting | `app/config.py` + `.env.example` (+ README if user-facing) |
| DB change | `app/db.py` + Alembic autogenerate migration |
| API change | `app/main.py` + `app/schemas.py` + `tests/test_api.py` + README API section |

## Conventions

- Sync SQLAlchemy everywhere. In `async def` routes, run blocking work (Pillow, S3, DB writes) through
  `run_in_threadpool`.
- Errors to clients: raise `ApiError(status, CODE, message)`; the body is always `{"error": {"code", "message"}}`.
- Keep modules small and single-purpose; prefer stdlib or existing deps over new ones.
- `ponytail:` comments mark deliberate simplifications with a known ceiling and how to lift it.
- Tests: pure logic gets table tests; pipeline tests use real local models plus a fake LLM (`tests/test_pipeline.py::FakeLLM`).
  No test may call a real LLM or the network.
- Capture page: no inline scripts or styles (strict CSP). UI copy is Bahasa Indonesia.
