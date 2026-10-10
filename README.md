# id-verification

Open-source verification for Indonesian identity cards (e-KTP). Give it a photo of the KTP and a live selfie of
the person holding it. It decides automatically, `approved` or `rejected`, with reason codes, and returns the NIK
and name read from the card.

Use it three ways:

- **Python library**: `await Verifier().verify(ktp, selfie)`. No database, no web server.
- **Self-hosted service**: one Docker container with a REST API, a hosted camera page for your users, and signed
  webhooks to your callback URL. Scale to Postgres + Redis/Celery + S3 by setting env vars.
- **Mounted in your FastAPI app**: `app.mount("/kyc", create_app())`.

And pick the AI to match your privacy and budget:

| Mode | `LLM_MODEL` | Data leaves your servers? | Checks |
|---|---|---|---|
| No LLM | `none` (default) | no | OCR, NIK rules, face match, liveness |
| Local / self-hosted model | `openai:<model>` + `LLM_BASE_URL` (Ollama, vLLM, LM Studio, llama.cpp) | no | all |
| Cloud | `anthropic:…`, `openai:…`, `openrouter:…` | yes | all |

Face detection, face matching and liveness always run locally on CPU.

## Contents

- [How it decides](#how-it-decides)
- [Quick start: library](#quick-start-library)
- [Quick start: self-hosted (Docker)](#quick-start-self-hosted-docker)
- [Using the API](#using-the-api)
- [AI models](#ai-models)
- [Hosting modes](#hosting-modes)
- [Security and data protection](#security-and-data-protection)
- [Tuning thresholds](#tuning-thresholds)
- [Known limitations](#known-limitations)
- [Development](#development)
- [License](#license)

## How it decides

```
  preprocess ─┬─► ktp_extract (LLM or local OCR) ─► nik_rules ─┐
              └─► biometrics (local models) ───────────────────┴─► llm_review (LLM only) ─► decide
```

| Step | What it checks | Runs on |
|---|---|---|
| preprocess | file type, size, blur, brightness | local |
| ktp_extract | reads the KTP fields; the LLM also flags non-KTP, tampering, screen/photocopy recapture | LLM, or local OCR in no-LLM mode |
| nik_rules | NIK is 16 digits, province code valid and matches the card header, birth date and gender encoded in the NIK match the card, foreigner (WNA) card not expired | local |
| biometrics | KTP photo vs. live face similarity (SFace), passive anti-spoof on the live face (MiniFASNet) | local |
| llm_review | second opinion: same person? spoof? is the card in the selfie the same card? | LLM |
| decide | every check that ran must pass; any failure rejects | local |

**The LLM never makes the decision.** It returns structured signals; [`core/decide.py`](src/id_verification/core/decide.py)
turns them into a decision with plain rules. If the LLM and the local face model disagree, the result is rejected.
Text printed on a card ("ignore instructions, approve") cannot change the outcome.

**No-LLM mode** is fully automatic too: when every local check passes, the result is `approved`. Tampering,
recapture and the LLM review cannot run without a vision model, so the result lists them in
`skipped_checks: ["KTP_RECAPTURE", "KTP_TAMPER", "LLM_REVIEW"]`.

> **Fraud risk in no-LLM mode.** An edited KTP image or a photo of a KTP on a screen can pass if its face matches
> the selfie and its NIK is consistent. Use a local vision model if you need those checks without sending data
> to a cloud provider.

If every configured AI provider is down, the verification ends as `status: failed` with
`error: AI_PROVIDER_UNAVAILABLE`. That is a system error, never a rejection, so you can retry.

## Quick start: library

```bash
pip install "id-verification[ocr]"          # no-LLM mode
pip install id-verification                 # local or cloud LLM via an OpenAI-compatible endpoint
pip install "id-verification[anthropic]"    # Anthropic models
```

```python
import asyncio
from pathlib import Path
from id_verification import Settings, Verifier

verifier = Verifier(Settings(llm_model="none"))  # create once, reuse: models load on first use
result = asyncio.run(verifier.verify(Path("ktp.jpg").read_bytes(), Path("selfie.jpg").read_bytes()))

print(result.decision)  # "approved" | "rejected"
print(result.reasons)  # ["FACE_MISMATCH", ...], empty when approved
print(result.nik, result.nama)
print(result.skipped_checks)  # non-empty only in no-LLM mode
```

With a local model (Ollama shown):

```python
Verifier(Settings(llm_model="openai:qwen2.5vl:7b", llm_base_url="http://localhost:11434/v1", llm_api_key="ollama"))
```

`Settings` also reads env vars and `.env` (`LLM_MODEL=…`), see [.env.example](.env.example). The face models
(~40 MB, SHA-256 checked) download to `~/.cache/id-verification/models` on first use; set `MODELS_DIR` to change
that, or run `id-verification download-models` ahead of time.

`verify()` raises `InvalidImage` for files that are not jpeg/png/webp or too large, and `ProviderError` when every
AI provider fails.

## Quick start: self-hosted (Docker)

```bash
cp .env.example .env
docker run --rm ghcr.io/ryanpradnya/id-verification id-verification gen-key   # paste into DATA_ENCRYPTION_KEY
docker compose up -d
docker compose exec idv id-verification create-tenant --name "Acme" --webhook-url https://acme.example/kyc-hook
```

`create-tenant` prints a `tenant_id`, `api_key` and `webhook_secret` **once**. Only a hash of the key and an
encrypted copy of the secret are stored.

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/healthz

This is **lite mode**: one container, SQLite and encrypted image files in the `idv` volume, a background worker
inside the same process. Run a fully local vision model next to it with `docker compose --profile ollama up -d`
(instructions at the top of [docker-compose.yml](docker-compose.yml)).

Without Docker:

```bash
pip install "id-verification[server,ocr]"
export DATA_ENCRYPTION_KEY=$(id-verification gen-key)
id-verification serve          # migrates the database, then serves on :8000
```

> **Keep `DATA_ENCRYPTION_KEY` safe and backed up.** It encrypts stored images, KTP data and webhook secrets.
> If it is lost, that data cannot be decrypted.

### Mount inside your own FastAPI app

```python
from fastapi import FastAPI
from id_verification import Settings
from id_verification.server import create_app, migrate

idv = create_app(Settings(public_base_url="https://shop.example/kyc"))
migrate()  # or run `id-verification migrate` on deploy
app = FastAPI(lifespan=idv.router.lifespan_context)  # starts the background worker
app.mount("/kyc", idv)
```

One `create_app` per process. Set `PUBLIC_BASE_URL` to include the mount prefix so capture links point to it.

## Using the API

All `/v1` calls need the header `X-API-Key: kyc_...`. Rate limit: 60 requests/minute per tenant (configurable).

### 1. Submit a verification (server to server)

```bash
curl -X POST http://localhost:8000/v1/verifications \
  -H "X-API-Key: $KEY" \
  -H "Idempotency-Key: signup-123" \
  -F ktp=@ktp.jpg -F selfie=@selfie.jpg \
  -F external_ref=user-123
```

Or JSON with base64 images:

```json
POST /v1/verifications
{ "ktp_base64": "...", "selfie_base64": "...", "external_ref": "user-123", "callback_url": "https://..." }
```

Response `202 Accepted` with `"status": "pending"`. The result comes later by webhook or polling.

- Images: jpeg, png or webp, max 10 MB each. They are re-encoded on arrival (strips EXIF and anything hidden).
- `Idempotency-Key` (optional): resending the same key returns the original verification.
- `callback_url` (optional): overrides the tenant's default webhook for this verification. Must be https and
  resolve to a public address.

### 2. Or send your user to the hosted camera page

```bash
curl -X POST http://localhost:8000/v1/sessions -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"external_ref": "user-123", "redirect_url": "https://acme.example/kyc/done"}'
# → { "session_id": "...", "capture_url": "https://.../capture/<token>", "expires_at": "..." }
```

Send the user to `capture_url`. The link works once and expires after 15 minutes. The page captures the KTP
(rear camera) and a selfie holding the KTP (front camera) with the live camera only, no file picker. Your API key
never reaches the browser. Browsers allow camera access only on **https** (or localhost), so set
`PUBLIC_BASE_URL` to your https domain in production.

After submitting, the browser goes to `redirect_url?session_id=<id>` if you set one, otherwise the page shows a
"received" message. **The redirect never carries the result**, because anyone could edit a URL. Match the
webhook to your user by `external_ref`.

### 3. Get the result

Webhook (POST to the tenant's webhook URL or the request's `callback_url`) or `GET /v1/verifications/{id}`.
Same body:

```json
{
  "id": "3f2a...",
  "external_ref": "user-123",
  "status": "completed",
  "decision": "rejected",
  "reason_codes": ["FACE_MISMATCH"],
  "skipped_checks": [],
  "ktp": { "nik": "3171011208900001", "nama": "BUDI SPESIMEN" },
  "scores": { "face_similarity": 0.21, "liveness": 0.97, "llm_same_person": null },
  "error": null,
  "created_at": "2026-10-10T04:41:12Z",
  "completed_at": "2026-10-10T04:41:20Z"
}
```

`status`: `pending` → `processing` → `completed` (has a `decision`) or `failed` (system error, see `error`).
Only NIK and name are returned. All other KTP fields stay encrypted on the server for audit.

Set or change a tenant's default webhook with `id-verification set-webhook --tenant-id <id> --url https://…`.

### Verifying webhooks

Each webhook carries `X-KYC-Signature: t=<unix time>,v1=<hex HMAC-SHA256>`, an HMAC of `"<t>." + raw body`
keyed with your `webhook_secret`. Failed deliveries (non-2xx or network error) are retried 6 times with
exponential backoff.

```python
import hashlib, hmac, time


def verify(raw_body: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    if abs(time.time() - int(parts["t"])) > tolerance:
        return False  # replayed or stale
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

### Reason codes

| Code | Meaning |
|---|---|
| `IMAGE_UNREADABLE` / `IMAGE_TOO_SMALL` / `IMAGE_TOO_BLURRY` / `IMAGE_TOO_DARK` | photo quality too low |
| `NOT_A_KTP` | the document is not an Indonesian e-KTP |
| `KTP_TAMPERED` | signs of digital editing (LLM modes) |
| `KTP_RECAPTURED` | photo of a screen, photocopy or printout instead of the physical card (LLM modes) |
| `KTP_UNREADABLE` | card fields could not be read |
| `KTP_FIELDS_MISSING` | name, birth date, gender or citizenship missing |
| `NIK_INVALID_FORMAT` | NIK is not 16 digits / impossible date / zero serial |
| `NIK_INVALID_PROVINCE` | NIK province code does not exist |
| `NIK_PROVINCE_MISMATCH` | NIK province code differs from the card header |
| `NIK_BIRTHDATE_MISMATCH` | birth date in NIK differs from the printed date |
| `NIK_GENDER_MISMATCH` | gender in NIK (day +40 for women) differs from the printed gender |
| `KTP_EXPIRED` | foreigner (WNA) card past `berlaku hingga`. Citizen cards are valid for life |
| `KTP_FACE_NOT_FOUND` / `SELFIE_FACE_NOT_FOUND` | no face detected |
| `FACE_MISMATCH` | local face similarity below threshold |
| `LIVENESS_FAILED` | live face looks like a print or screen |
| `LLM_FACE_MISMATCH` | the LLM judged it a different person |
| `LLM_SPOOF_SUSPECTED` | the LLM judged the selfie a spoof |
| `SELFIE_KTP_MISMATCH` | the card held in the selfie is not the uploaded KTP |
| `REVIEW_INCOMPLETE` | safety net in LLM modes: never approve unless every check ran |

## AI models

Set `LLM_MODEL` to `none` or to any LangChain `init_chat_model` id. **The model must accept image input.**
Check your setup before going live:

```bash
id-verification check-llm    # reads a bundled synthetic KTP and checks the model returns the right NIK
```

| Where | `.env` |
|---|---|
| No LLM | `LLM_MODEL=none` (needs the `[ocr]` extra) |
| Ollama | `LLM_MODEL=openai:qwen2.5vl:7b`, `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_API_KEY=ollama` |
| vLLM | `LLM_MODEL=openai:Qwen/Qwen2.5-VL-7B-Instruct`, `LLM_BASE_URL=http://localhost:8001/v1` |
| LM Studio / llama.cpp server | `LLM_MODEL=openai:<model id>`, `LLM_BASE_URL=http://localhost:1234/v1` |
| Anthropic | `LLM_MODEL=anthropic:claude-sonnet-5-5`, `ANTHROPIC_API_KEY=...` (`[anthropic]` extra) |
| OpenAI | `LLM_MODEL=openai:gpt-5`, `OPENAI_API_KEY=...` |
| OpenRouter | `LLM_MODEL=openrouter:google/gemini-2.5-pro`, `OPENROUTER_API_KEY=...` (`[openrouter]` extra) |
| Any OpenAI-compatible API | `LLM_MODEL=openai:<model>`, `LLM_BASE_URL=https://.../v1`, `LLM_API_KEY=...` |

Local vision models to try: Qwen2.5-VL (7B and up), Gemma 3, Llama 3.2 Vision. Smaller models read KTP text less
reliably. Run `check-llm` and [scripts/eval_samples.py](scripts/eval_samples.py) on your own samples before
trusting one.

- **Weak structured output**: when a model can't do tool calls or JSON schema, the library retries once by asking
  for plain JSON and parsing it. `LLM_STRUCTURED_METHOD=json_mode` (or `json_schema` / `function_calling`)
  forces a method. Anthropic models use `json_schema` automatically.
- `LLM_FALLBACK_MODEL`: a second provider tried when the first fails. It reads its own key env var.
- DeepSeek's own chat models do not accept images; use a vision model through another endpoint.

## Hosting modes

Each piece switches on its own env var. Nothing set = lite.

| Piece | Lite (default) | Scale (env var set) |
|---|---|---|
| Database | SQLite in `DATA_DIR` | `DATABASE_URL=postgresql+psycopg://...` |
| Queue | in-process worker threads | `REDIS_URL`, then run the Celery worker |
| Rate limit | in memory | Redis |
| Image storage | encrypted files in `DATA_DIR/blobs` | `S3_BUCKET` (+ `S3_ENDPOINT_URL` for R2/MinIO) |

Lite mode is **one process only**: its queue and rate limits live in memory. Unfinished verifications are picked
up again after a restart. For more throughput, use [docker-compose.scale.yml](docker-compose.scale.yml):

```bash
docker compose -f docker-compose.scale.yml up -d
# worker: celery -A id_verification.server.celery_app worker --beat
```

Production checklist:

- Put a reverse proxy (nginx, Caddy, a cloud LB) in front for TLS, and cap request bodies there (~30 MB).
  Set uvicorn's `FORWARDED_ALLOW_IPS` to the proxy's address.
- At scale, use managed Postgres, Redis and S3, and scale `worker` replicas. With more than one worker, move
  `--beat` to a single dedicated service.
- Back up `DATA_ENCRYPTION_KEY` separately from the database.

## Security and data protection

- API keys and capture tokens: 256-bit random, only SHA-256 hashes stored.
- Encryption at rest (Fernet): images (before they reach disk or S3), full KTP data, webhook secrets.
- Uploads: checked by magic bytes, size-capped, decompression-bomb guarded, re-encoded.
- Webhooks: https only, private/loopback/link-local/metadata addresses blocked, HMAC-signed with timestamp.
- Redirects: absolute https URLs only, and they carry the session id, never the result.
- Capture page: single-use token, CSRF double-submit cookie, strict CSP (no inline or third-party scripts),
  camera-only permissions.
- Logs: no images and no full NIK (masked as `3171********0001` in the audit trail).
- Every step's result is stored in `audit_events` (model used, scores, LLM reasoning), so each decision can be traced.
- Retention: a daily job deletes images and full KTP data after `RETENTION_DAYS` (30). Decisions and audit
  records stay.

**UU PDP (Law 27/2022):** with a cloud LLM, KTP images and face photos go to that provider, usually outside
Indonesia. Get explicit consent from end users for this processing and transfer, and have a data processing
agreement with the provider. No-LLM mode or a self-hosted model keeps all data on your own servers.

Found a vulnerability? See [SECURITY.md](SECURITY.md).

## Tuning thresholds

All thresholds are env vars: `FACE_MATCH_THRESHOLD` (0.363, OpenCV's recommended SFace value),
`LIVENESS_THRESHOLD` (0.80), `MIN_IMAGE_SIDE`, `MIN_BLUR_VARIANCE`, `MIN_BRIGHTNESS`.

```bash
python scripts/eval_samples.py --biometrics-only ktp.jpg selfie.jpg   # local models only, free
python scripts/eval_samples.py ktp.jpg selfie.jpg                     # full pipeline with your LLM_MODEL
```

What sample images showed during development:

- Same person (live face vs. the card photo in the same selfie): similarity 0.63–0.75. Different people: −0.14 to 0.24.
- Liveness on real camera selfies: 0.98–1.0. On printed card photos: 0.001–0.16.
- A downloaded news photo and a tiny 237 px image scored low on liveness and were rejected.

## Known limitations

- **No-LLM mode skips tampering and recapture checks** (see above). Its OCR parser is label-based and can miss
  fields on worn or skewed cards; a missing field rejects rather than approves.
- **Old photos on lifetime KTPs**: citizen cards never expire, so the photo can be 10+ years old. Face similarity
  can drop below threshold and reject a genuine person.
- **Passive liveness only**: no blink/turn-head challenge.
- **No government (Dukcapil) check**: the NIK is checked for internal consistency, not against the population
  registry. That needs licensed Dukcapil access.
- **Tenant admin is CLI-only**: there is no admin web UI.
- **Webhook DNS rebinding**: the target IP is checked once, then resolved again when sending. Pin the IP in a
  custom transport if tenants are untrusted.
- **KTP photo selection**: when a KTP image shows more than one face (card held in hand), the most print-like
  face is taken as the card photo. This is a heuristic.

## Development

```bash
git clone https://github.com/ryanpradnya/id-verification && cd id-verification
uv sync --all-extras
MODELS_DIR=models uv run id-verification download-models
MODELS_DIR=models uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests need no network or API keys; LLM calls are faked. Tests that need real face photos read
`tests/fixtures/`, which is **gitignored** because sample ID cards belong to real people. Put your own samples
there (`ktp1.jpg`, `ktp2.jpg`, `selfie2.png`, `selfie5.webp`); those tests skip when the files are missing.
Everything committed uses synthetic data, like
[`specimen_ktp.jpg`](src/id_verification/core/assets/specimen_ktp.jpg) (made by `scripts/make_specimen.py`).

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) (rules for humans and coding agents).

```
src/id_verification/
  __init__.py       Verifier, VerifyResult, Settings
  config.py         all settings (env / .env / code)
  cli.py            id-verification command
  core/             the pipeline, no web or database dependencies
    verifier.py     library entry point
    graph.py        LangGraph wiring (LLM and no-LLM)
    images.py       decode, sanitize, quality checks
    face.py         YuNet + SFace + MiniFASNet (local biometrics)
    models.py       biometric model download + SHA-256 pinning
    ktp.py          KTP schema + NIK rules
    llm.py          provider-agnostic vision LLM calls + JSON fallback
    ocr.py          no-LLM mode: RapidOCR + KTP field parser
    decide.py       the decision policy (pure function)
  server/           [server] extra
    app.py          create_app(): /v1 API, hosted capture page, auth, rate limit
    jobs.py         run verification, deliver webhook, retention purge
    queue.py        in-process queue (lite) or Celery dispatch
    celery_app.py   Celery tasks (scale)
    storage.py      encrypted local disk or S3
    db.py           SQLAlchemy models, migrate()
    security.py     keys, encryption, webhook signing, SSRF and redirect guards
    schemas.py      public API shapes
    migrations/     Alembic
    templates/, static/   capture page
scripts/            threshold evaluation, specimen generator
tests/
```

## License

[MIT](LICENSE). Bundled and downloaded models keep their own licenses, all permitting commercial use:

| Model | Use | License |
|---|---|---|
| YuNet (OpenCV Zoo) | face detection | Apache-2.0 |
| SFace (OpenCV Zoo) | face matching | Apache-2.0 |
| MiniFASNetV2 | passive liveness | Apache-2.0 |
| RapidOCR / PP-OCR models | KTP text in no-LLM mode | Apache-2.0 |

InsightFace was deliberately **not** used: its pretrained models are licensed for non-commercial research only.

This project checks documents and faces; it does not make you compliant with KYC regulation on its own. You
are responsible for how you deploy it, for user consent, and for reviewing its accuracy on your own users.
