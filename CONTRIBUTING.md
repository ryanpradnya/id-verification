# Contributing

Thanks for helping. Bug reports, fixes, OCR improvements, new local-model results and docs are all welcome.

## Setup

```bash
git clone https://github.com/ryanpradnya/id-verification && cd id-verification
uv sync --all-extras
MODELS_DIR=models uv run id-verification download-models
MODELS_DIR=models uv run pytest
```

## Before you open a pull request

- `uv run ruff check . && uv run ruff format --check .` passes.
- `uv run pytest` passes, and new behavior has a test. Tests must not call a real LLM or the network.
- User-facing changes are in `README.md` and `CHANGELOG.md` (under "Unreleased").
- Read the invariants in [AGENTS.md](AGENTS.md). The important ones: only `core/decide.py` decides, a check that
  did not run never counts as a pass, `core/` does not import server code.

## Never commit personal data

Do not commit real KTP images, selfies, NIKs or names, not even in tests, issues or screenshots. Use
`tests/fixtures/` (gitignored) for real samples on your machine, and synthetic data in anything committed.
`scripts/make_specimen.py` shows how to render a fake card.

## Reporting results for a local model

If you tried a local vision model, an issue with the model name, size, hardware, `id-verification check-llm`
output and rough accuracy on your samples (no images) helps others choose.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).
