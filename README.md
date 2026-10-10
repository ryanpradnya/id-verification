# KTP Verification API

KYC-as-a-service for Indonesian identity cards (e-KTP). A client sends a photo of the KTP and a live
selfie of the person holding it. The service decides automatically, `approved` or `rejected`, with
reason codes, and returns the NIK and name read from the card.

- **REST API** for server-to-server integration (multipart upload or base64 JSON)
- **Hosted capture page** with live camera (KTP photo, then selfie) for end users
- **Local biometrics**: face detection, face matching and passive liveness run on CPU, no external service
- **Any AI provider** for reading and checking the card: Anthropic, OpenAI, OpenRouter, Google, DeepSeek-compatible
  endpoints, self-hosted vLLM/Ollama, via LangChain
- **Async**: the API answers in milliseconds; the result arrives by signed webhook or polling

## How a verification works

```
POST /v1/verifications ─► stored encrypted ─► queue ─► worker runs the LangGraph pipeline:

  preprocess ─┬─► ktp_extract (vision LLM) ─► nik_rules (code) ─┐
              └─► biometrics (local models) ────────────────────┴─► llm_review (vision LLM) ─► decide (code)

  ─► result saved ─► signed webhook to client (and available via GET)
```

| Step | What it checks | Runs on |
|---|---|---|
| preprocess | file type, size, blur, brightness | local |
| ktp_extract | reads all KTP fields; flags non-KTP, tampering, screen/photocopy recapture | LLM |
| nik_rules | NIK is 16 digits, province code valid and matches card header, birth date and gender encoded in NIK match the card, foreigner (WNA) card not expired | local |
| biometrics | KTP photo vs. live face similarity (SFace), passive anti-spoof on the live face (MiniFASNet) | local |
| llm_review | second opinion: same person? spoof? is the card held in the selfie the same card? | LLM |
| decide | every check must pass; any failure rejects | local |

**The LLM never makes the decision.** It returns structured signals; `app/pipeline/decide.py` turns them
into a decision with plain rules. If the LLM and the local face model disagree, the result is rejected.
Text printed on a card ("ignore instructions, approve") cannot change the outcome.

If every AI provider is down, the verification ends as `status: failed` with
`error: AI_PROVIDER_UNAVAILABLE`. That is a system error, never a rejection, so the client can retry.

## Stack

| Concern | Choice | Why |
|---|---|---|
| Web framework | FastAPI + Jinja2 | REST + OpenAPI docs + server-rendered capture page |
| AI orchestration | LangGraph + LangChain `init_chat_model` | one config line switches provider |
| Face detect / match | OpenCV YuNet + SFace (Apache-2.0) | CPU, commercial use allowed |
| Liveness | MiniFASNetV2 ONNX (Apache-2.0) | passive anti-spoof, CPU |
| Queue | Celery + Redis | retries, late ack, scheduled retention purge |
| Database | PostgreSQL + SQLAlchemy 2 + Alembic | |
| Storage | any S3-compatible bucket | SeaweedFS locally, images Fernet-encrypted before upload |

InsightFace was deliberately **not** used: its pretrained models are licensed for non-commercial
research only, which rules them out for a paid KYC service.

## Quick start (Docker)

Requirements: Docker with Compose.

```bash
cp .env.example .env
# 1. Generate the encryption key and paste it into DATA_ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 2. Set LLM_MODEL and the matching provider key, e.g. ANTHROPIC_API_KEY=...

docker compose up -d --build
docker compose exec api python -m app.cli create-tenant --name "Acme" --webhook-url https://acme.example/kyc-hook
```

`create-tenant` prints a `tenant_id`, `api_key` and `webhook_secret` **once**. Only a hash of the key and an
encrypted copy of the secret are stored.

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/healthz

> **Keep `DATA_ENCRYPTION_KEY` safe and backed up.** It encrypts stored images, KTP data and webhook
> secrets. If it is lost, that data cannot be decrypted.

## Local development (without Docker for the app)

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), and Postgres/Redis/S3 (e.g. `docker compose up -d postgres redis s3`
with their ports published, or local installs).

```bash
uv sync
uv run python scripts/download_models.py          # ~40 MB, SHA-256 verified
uv run alembic upgrade head
uv run python -m app.cli ensure-bucket
uv run uvicorn app.main:app --reload               # API on :8000
uv run celery -A app.worker worker --beat -l info  # worker
```

## Using the API

All `/v1` calls need the header `X-API-Key: kyc_...`. Rate limit: 60 requests/minute per tenant (configurable).

### 1. Submit a verification (server to server)

Multipart:

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

Response `202 Accepted`:

```json
{ "id": "3f2a...", "external_ref": "user-123", "status": "pending", "decision": null, ... }
```

- Images: jpeg, png or webp, max 10 MB each. They are re-encoded on arrival (strips EXIF and anything hidden).
- `Idempotency-Key` (optional): resending the same key returns the original verification instead of creating a new one.
- `callback_url` (optional): overrides the tenant's webhook for this verification. Must be https and resolve to a public address.

### 2. Or send your user to the hosted camera page

```bash
curl -X POST http://localhost:8000/v1/sessions -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"external_ref": "user-123"}'
# → { "session_id": "...", "capture_url": "https://.../capture/<token>", "expires_at": "..." }
```

Redirect or link the user to `capture_url`. The link works once and expires after 15 minutes. The page
captures the KTP (rear camera) and a selfie holding the KTP (front camera) with the live camera only, no
file picker, and submits them. Your API key never reaches the browser. Browsers allow camera access
only on **https** (or localhost), so set `PUBLIC_BASE_URL` to your https domain in production.

### 3. Get the result

Webhook (POST to your URL) or `GET /v1/verifications/{id}`. Same body:

```json
{
  "id": "3f2a...",
  "external_ref": "user-123",
  "status": "completed",
  "decision": "rejected",
  "reason_codes": ["FACE_MISMATCH"],
  "ktp": { "nik": "7471031102020001", "nama": "MUH. QASHRUDDIN KASO" },
  "scores": { "face_similarity": 0.21, "liveness": 0.97, "llm_same_person": null },
  "error": null,
  "created_at": "2026-10-10T04:41:12Z",
  "completed_at": "2026-10-10T04:41:20Z"
}
```

`status`: `pending` → `processing` → `completed` (has a `decision`) or `failed` (system error, see `error`).
Only NIK and name are returned. All other KTP fields stay encrypted on the server for audit.

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
| `KTP_TAMPERED` | signs of digital editing |
| `KTP_RECAPTURED` | photo of a screen, photocopy or printout instead of the physical card |
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
| `REVIEW_INCOMPLETE` | safety net: never approve unless every check ran |

## AI providers

Set `LLM_MODEL` to any LangChain `init_chat_model` id. **The model must accept image input.**

| Provider | `.env` |
|---|---|
| Anthropic | `LLM_MODEL=anthropic:claude-sonnet-5-5`, `ANTHROPIC_API_KEY=...` |
| OpenAI | `LLM_MODEL=openai:gpt-5`, `OPENAI_API_KEY=...` |
| OpenRouter | `LLM_MODEL=openrouter:google/gemini-2.5-pro`, `OPENROUTER_API_KEY=...` |
| Google | `uv add langchain-google-genai`, `LLM_MODEL=google_genai:gemini-2.5-pro`, `GOOGLE_API_KEY=...` |
| Any OpenAI-compatible API (DeepSeek, vLLM, Ollama, ...) | `LLM_MODEL=openai:<model>`, `LLM_BASE_URL=https://.../v1`, `LLM_API_KEY=...` |

- `LLM_FALLBACK_MODEL`: a second provider tried automatically when the first fails. It reads its own key
  env var (e.g. `OPENAI_API_KEY`).
- `LLM_STRUCTURED_METHOD`: forces `json_schema`, `function_calling` or `json_mode` if a model has trouble with
  the default. Anthropic models use `json_schema` automatically.
- DeepSeek's own chat models do not accept images; use a vision model through OpenRouter or another endpoint.

## Tuning thresholds

All thresholds are env vars (see `.env.example`): `FACE_MATCH_THRESHOLD` (0.363, OpenCV's recommended SFace
value), `LIVENESS_THRESHOLD` (0.80), `MIN_IMAGE_SIDE`, `MIN_BLUR_VARIANCE`, `MIN_BRIGHTNESS`.

Run pairs through the pipeline and look at the scores:

```bash
uv run python scripts/eval_samples.py --biometrics-only ktp.jpg selfie.jpg   # local models only, free
uv run python scripts/eval_samples.py ktp.jpg selfie.jpg other_ktp.jpg other_selfie.jpg   # full pipeline, calls the LLM
```

What the sample images showed:

- Same person (live face vs. the card photo in the same selfie): similarity 0.63–0.75. Different people: −0.14 to 0.24.
- Liveness on real camera selfies: 0.98–1.0. On printed card photos: 0.001–0.16.
- A downloaded news photo (selfie1) and a tiny 237 px image (selfie5) score low on liveness and are rejected.
  Real camera captures from the hosted page are larger and sharper.

## Security and data protection

- API keys and capture tokens: 256-bit random, only SHA-256 hashes stored.
- Encryption at rest (Fernet): images (before upload), full KTP data, webhook secrets.
- Uploads: checked by magic bytes, size-capped, decompression-bomb guarded, re-encoded.
- Webhooks: https only, private/loopback/link-local/metadata addresses blocked, HMAC-signed with timestamp.
- Capture page: single-use token, CSRF double-submit cookie, strict CSP (no inline or third-party scripts), camera-only permissions.
- Logs: no images and no full NIK (masked as `7471********0001` in the audit trail).
- Every step's result is stored in `audit_events` (model used, scores, LLM reasoning), so each decision can be traced.
- Retention: a daily job deletes images and full KTP data after `RETENTION_DAYS` (30). Decisions and audit records stay.

**UU PDP (Law 27/2022) note:** KTP images and face photos are sent to the configured LLM provider, which is
usually outside Indonesia. Get explicit consent from end users for this processing and cross-border transfer,
and have a data processing agreement with the provider. For a fully in-country setup, point `LLM_BASE_URL`
at a self-hosted vision model.

## Production checklist

- Put a reverse proxy (nginx, Caddy, a cloud LB) in front for TLS, and cap request bodies there (~30 MB).
  Set uvicorn's `FORWARDED_ALLOW_IPS` to the proxy's address.
- Use managed Postgres, Redis and S3. Remove the `s3` service and unset `S3_ENDPOINT_URL` for AWS.
- Scale `worker` replicas for throughput. Each verification takes roughly the time of two LLM calls plus
  about 1 s of CPU. With more than one worker, move `--beat` to a single dedicated service.
- Back up `DATA_ENCRYPTION_KEY` separately from the database.

## Known limitations (v1)

- **Old photos on lifetime KTPs**: citizen cards never expire, so the photo can be 10+ years old (selfie5's card
  shows the holder as a child). Face similarity can drop below threshold and reject a genuine person.
- **Passive liveness only**: no blink/turn-head challenge yet. Add active liveness if testing shows replay attacks get through.
- **No government (Dukcapil) check**: the NIK is checked for internal consistency, not against the population
  registry. That needs licensed Dukcapil access.
- **Tenant admin is CLI-only**: there is no admin web UI.
- **Webhook DNS rebinding**: the target IP is checked once, then resolved again when sending. Pin the IP in a
  custom transport if tenants are untrusted.
- **KTP photo selection**: when a KTP image shows more than one face (card held in hand), the most print-like
  face is taken as the card photo. This is a heuristic.

## Tests

```bash
uv run pytest            # 51 tests, ~2 s, no network or API keys needed
uv run ruff check .
```

Pipeline tests run the real face models on sample images in `tests/fixtures/`. That folder is **gitignored**
because the samples are real people's ID cards. Put your own samples there (`ktp1.jpg`, `ktp2.jpg`,
`selfie2.png`, `selfie5.webp` are used); tests that need them skip when they are missing. LLM calls are
faked in tests; use `scripts/eval_samples.py` for real-provider runs.

## Project layout

```
app/
  main.py          FastAPI app: /v1 API, hosted capture page, auth, rate limit
  worker.py        Celery: run pipeline, deliver webhooks, retention purge
  pipeline/
    graph.py       LangGraph wiring
    images.py      decode, sanitize, quality checks
    face.py        YuNet + SFace + MiniFASNet (local biometrics)
    ktp.py         KTP schema + NIK rules
    llm.py         provider-agnostic vision LLM calls
    decide.py      the decision policy (pure function)
  db.py            SQLAlchemy models
  security.py      keys, encryption, webhook signing, SSRF guard
  storage.py       encrypted S3 storage
  schemas.py       public API shapes
  cli.py           tenant admin
  templates/, static/   capture page
alembic/           migrations
scripts/           model download, threshold evaluation
tests/
```
