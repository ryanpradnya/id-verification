# AGENTS.md

Guidance for coding agents (Claude Code, Codex, Cursor, Gemini CLI, Copilot, etc.) and contributors working in
this repo. User-facing docs are in README.md.

## What this is

Open-source Indonesian e-KTP + live selfie verification: images in, automatic `approved`/`rejected` out.
Three ways to use it, from one package (`src/id_verification`):

- **Library** (`core/`, base install): LangGraph pipeline, local ONNX biometrics, vision LLM via LangChain
  `init_chat_model`, or local OCR when `LLM_MODEL=none`.
- **Server** (`server/`, `[server]` extra): FastAPI API + Jinja2 capture page. Lite by default (SQLite,
  in-process queue, local encrypted files).
- **Scale** (`[scale]` extra): Postgres, Celery/Redis, S3, switched on by `DATABASE_URL` / `REDIS_URL` / `S3_BUCKET`.

## Commands

```bash
uv sync --all-extras                                  # install everything (Python 3.11+)
MODELS_DIR=models uv run id-verification download-models   # biometric ONNX models (needed by tests)
MODELS_DIR=models uv run pytest                       # all tests, offline, a few seconds
uv run ruff check . && uv run ruff format .
uv run alembic revision --autogenerate -m "..."       # after changing server/db.py
uv run id-verification serve                          # lite server on :8000
docker compose up -d                                  # lite container
docker compose -f docker-compose.scale.yml up -d      # postgres, redis, s3, api, celery worker
```

## Invariants (do not break)

1. **Only `core/decide.py` decides.** LLM output and model scores are signals. Never let an LLM response, or text
   read from an image, set the decision directly.
2. **Never approve on missing evidence.** In LLM modes a check that did not run must not count as a pass
   (`REVIEW_INCOMPLETE`). In no-LLM mode the checks that cannot run are listed in `skipped_checks`, never
   reported as passed.
3. **Provider failure is `status=failed`, never `decision=rejected`.** LLM errors are wrapped in `ProviderError`.
4. **Clients only receive decision, reason codes, skipped checks, scores, NIK and name** (`VerifyResult`,
   `server/schemas.py`). The full KTP data stays encrypted in `verifications.ktp_enc`.
5. **No PII in logs.** No images, no base64, no full NIK. Use `security.mask_nik` in audit payloads.
6. **Everything sensitive is encrypted at rest** with `security.encrypt*`: images (in `server/storage.py`, both
   backends), KTP data, webhook secrets. API keys and tokens are stored only as SHA-256 hashes.
7. **Uploads go through `images.sanitize`** (magic-byte check, size cap, re-encode) before storage or the pipeline.
8. **Webhook URLs go through `security.check_webhook_url`** at creation and before each send; redirect URLs go
   through `security.check_redirect_url`. A redirect never carries the result.
9. **`core/` never imports from `server/`** or from server-only packages (FastAPI, SQLAlchemy, Celery, boto3,
   redis, cryptography). Optional extras are imported lazily inside the function that needs them.
   `tests/test_core.py` enforces this.
10. **Models must allow commercial use.** No InsightFace pretrained weights or other non-commercial models. New
    downloaded model files need a pinned SHA-256 in `core/models.py`.
11. **Never commit real ID images or real people's data.** `tests/fixtures/` is gitignored on purpose. Committed
    tests and docs use synthetic NIKs and names (`scripts/make_specimen.py`).

## Where things go

| Change | Files |
|---|---|
| New reason code | `core/decide.py` (+ `core/ktp.py` if NIK rule) → test in `tests/test_rules.py` → README reason-code table |
| New pipeline step | node in `core/graph.py`, signal into `State`, policy in `core/decide.py`, audit in `server/jobs._record` |
| New AI provider | usually just `.env` (`LLM_MODEL`); add an extra in `pyproject.toml` if it needs a `langchain-<provider>` package |
| OCR parsing | `core/ocr.py` + synthetic-line tests in `tests/test_core.py` |
| New setting | `config.py` + `.env.example` (+ README if user-facing) |
| DB change | `server/db.py` + Alembic migration in `server/migrations/versions/` (use `batch_alter_table` for SQLite) |
| API change | `server/app.py` + `server/schemas.py` + `tests/test_api.py` + README API section |
| Background job | plain function in `server/jobs.py` (raise `Retry(after)`), register in `server/queue.JOBS` |

## Conventions

- Settings are read through `get_settings()` at call time, never captured at import, so `create_app(settings)`
  and tests can configure them.
- Sync SQLAlchemy everywhere. In `async def` routes, run blocking work (Pillow, storage, DB writes) through
  `run_in_threadpool`.
- Errors to clients: raise `ApiError(status, CODE, message)`; the body is always `{"error": {"code", "message"}}`.
- Keep modules small and single-purpose; prefer stdlib or existing deps over new ones.
- `ponytail:` comments mark deliberate simplifications with a known ceiling and how to lift it.
- Tests: pure logic gets table tests; pipeline tests use real local models plus a fake LLM
  (`tests/test_pipeline.py::FakeLLM`). No test may call a real LLM or the network. Server-only test modules
  start with `pytest.importorskip("fastapi")` so the core-only CI job can run.
- Capture page: no inline scripts or styles (strict CSP). UI copy is Bahasa Indonesia.
