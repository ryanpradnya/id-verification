# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

First public release.

### Added

- Python library: `Verifier(settings).verify(ktp, selfie)` returns a `VerifyResult` with no database or web server.
- No-LLM mode (`LLM_MODEL=none`, `[ocr]` extra): RapidOCR reads the KTP; checks that need a vision model are
  listed in `skipped_checks`.
- Local and self-hosted models through any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, llama.cpp), with
  a prompted-JSON fallback for models that cannot do structured output.
- `id-verification` CLI: `serve`, `migrate`, `gen-key`, `download-models`, `check-llm`, tenant commands.
- Lite self-hosting: SQLite, in-process queue that resumes unfinished work after a restart, encrypted local storage.
- `create_app(settings)` to run standalone or mount inside another FastAPI app.
- `redirect_url` on capture sessions: the browser returns to your site with `?session_id=` after submitting.
- `skipped_checks` in API results and webhooks.

### Changed

- Package renamed from `app` to `id_verification` with optional extras: `server`, `scale`, `ocr`, `anthropic`,
  `openrouter`, `deepseek`, `all`.
- Postgres, Redis/Celery and S3 are now opt-in (`DATABASE_URL`, `REDIS_URL`, `S3_BUCKET`).
- Default `LLM_MODEL` is `none`.
- Biometric models download on first use to `~/.cache/id-verification/models` (`MODELS_DIR` to override).
