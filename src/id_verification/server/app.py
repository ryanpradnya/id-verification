"""FastAPI app: REST API (/v1) + hosted camera capture page (/capture).

Standalone:  id-verification serve
Mounted:     idv = create_app(settings)
             app = FastAPI(lifespan=idv.router.lifespan_context)  # starts the lite worker
             app.mount("/kyc", idv)                               # PUBLIC_BASE_URL=https://host/kyc
"""

import base64
import binascii
import hmac
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from id_verification.config import Settings, get_settings, set_settings
from id_verification.core.images import InvalidImage, sanitize
from id_verification.server import queue, security, storage
from id_verification.server.db import CaptureSession, Tenant, Verification, aware, get_db, new_id, utcnow
from id_verification.server.schemas import (
    SessionCreate,
    SessionOut,
    VerificationJSON,
    VerificationOut,
    verification_out,
)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
router = APIRouter()

CAPTURE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message):
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message, headers=None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def _errors(e: ValidationError | RequestValidationError) -> list[dict]:
    return [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]


async def _api_error(_: Request, e: ApiError):
    return _error(e.status, e.code, e.message)


async def _http_error(_: Request, e: StarletteHTTPException):
    return _error(e.status_code, f"HTTP_{e.status_code}", e.detail, getattr(e, "headers", None))


async def _validation_error(_: Request, e: RequestValidationError):
    return _error(422, "VALIDATION_ERROR", _errors(e))


async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if "/capture/" in request.url.path:
        resp.headers["Content-Security-Policy"] = CAPTURE_CSP
        resp.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- auth + rate limit ----------


@lru_cache
def _redis():
    import redis  # lazy: optional [scale] extra

    return redis.Redis.from_url(get_settings().redis_url)


_hits: dict[str, int] = {}
_hits_lock = threading.Lock()


def rate_hit(key: str) -> int:
    """Fixed-window counter; returns hits in the current minute. Redis when configured, else in memory."""
    if get_settings().redis_url:
        r = _redis()
        n = r.incr(key)
        if n == 1:
            r.expire(key, 60)
        return n
    with _hits_lock:
        if len(_hits) > 10_000:  # drop past windows
            window = key.rsplit(":", 1)[1]
            for k in [k for k in _hits if not k.endswith(":" + window)]:
                del _hits[k]
        _hits[key] = n = _hits.get(key, 0) + 1
        return n


def current_tenant(
    db: Annotated[Session, Depends(get_db)], x_api_key: Annotated[str | None, Header()] = None
) -> Tenant:
    tenant = x_api_key and db.scalar(select(Tenant).where(Tenant.api_key_hash == security.sha256(x_api_key)))
    if not tenant or not tenant.active:
        raise ApiError(401, "UNAUTHORIZED", "missing or invalid X-API-Key")
    if rate_hit(f"rl:{tenant.id}:{int(time.time() // 60)}") > get_settings().rate_limit_per_minute:
        raise ApiError(429, "RATE_LIMITED", "too many requests")
    return tenant


TenantDep = Annotated[Tenant, Depends(current_tenant)]
DbDep = Annotated[Session, Depends(get_db)]


# ---------- shared ----------


def _check_url(url: str | None, check, code: str) -> None:
    if url:
        try:
            check(url, get_settings().webhook_allow_http)
        except ValueError as e:
            raise ApiError(422, code, str(e)) from e


def _check_length(request: Request) -> None:
    # Two images plus base64 overhead. Also cap body size at the reverse proxy.
    if int(request.headers.get("content-length") or 0) > 2 * get_settings().max_upload_bytes * 4 // 3 + 1_000_000:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "request body too large")


async def _read_upload(form, field: str) -> bytes:
    f = form.get(field)
    if not isinstance(f, UploadFile):
        raise ApiError(422, "VALIDATION_ERROR", f"file field '{field}' is required")
    return await f.read(get_settings().max_upload_bytes + 1)


def _clean(ktp: bytes, selfie: bytes) -> tuple[bytes, bytes]:
    limit = get_settings().max_upload_bytes
    try:
        return sanitize(ktp, limit), sanitize(selfie, limit)
    except InvalidImage as e:
        raise ApiError(422, "INVALID_IMAGE", str(e)) from e


def _create_verification(
    db: Session, tenant_id: str, ktp: bytes, selfie: bytes, external_ref, callback_url, idem_key=None
) -> Verification:
    """ktp/selfie must already be sanitized by _clean."""
    vid = new_id()
    v = Verification(
        id=vid,
        tenant_id=tenant_id,
        external_ref=external_ref,
        callback_url=callback_url,
        idempotency_key=idem_key,
        ktp_key=f"{tenant_id}/{vid}/ktp.jpg",
        selfie_key=f"{tenant_id}/{vid}/selfie.jpg",
    )
    storage.put(v.ktp_key, ktp)
    storage.put(v.selfie_key, selfie)
    db.add(v)
    db.commit()
    queue.enqueue("process_verification", vid)
    return v


# ---------- REST API ----------

_UPLOAD_DOC = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["ktp", "selfie"],
                    "properties": {
                        "ktp": {"type": "string", "format": "binary"},
                        "selfie": {"type": "string", "format": "binary"},
                        "external_ref": {"type": "string"},
                        "callback_url": {"type": "string"},
                    },
                }
            },
            "application/json": {"schema": VerificationJSON.model_json_schema()},
        },
    }
}


def _by_idempotency_key(db: Session, tenant_id: str, key: str) -> Verification | None:
    return db.scalar(
        select(Verification).where(Verification.tenant_id == tenant_id, Verification.idempotency_key == key)
    )


@router.post("/v1/verifications", status_code=202, response_model=VerificationOut, openapi_extra=_UPLOAD_DOC)
async def create_verification(
    request: Request,
    tenant: TenantDep,
    db: DbDep,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
):
    """Submit a KTP image + selfie (multipart or base64 JSON). Returns immediately;
    the result arrives by webhook or GET /v1/verifications/{id}."""
    if idempotency_key and (existing := _by_idempotency_key(db, tenant.id, idempotency_key)):
        return JSONResponse(verification_out(existing).model_dump(mode="json"), status_code=200)

    _check_length(request)
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form(max_files=2, max_fields=4)
        ktp, selfie = await _read_upload(form, "ktp"), await _read_upload(form, "selfie")
        external_ref, callback_url = form.get("external_ref") or None, form.get("callback_url") or None
    else:
        try:
            body = VerificationJSON.model_validate_json(await request.body())
            ktp = base64.b64decode(body.ktp_base64, validate=True)
            selfie = base64.b64decode(body.selfie_base64, validate=True)
        except ValidationError as e:
            raise ApiError(422, "VALIDATION_ERROR", _errors(e)) from e
        except binascii.Error as e:
            raise ApiError(422, "INVALID_IMAGE", "invalid base64") from e
        external_ref, callback_url = body.external_ref, body.callback_url

    if external_ref and len(external_ref) > 200:
        raise ApiError(422, "VALIDATION_ERROR", "external_ref too long")
    _check_url(callback_url, security.check_webhook_url, "INVALID_CALLBACK_URL")
    # Pillow, storage and DB calls are blocking: keep them off the event loop.
    ktp, selfie = await run_in_threadpool(_clean, ktp, selfie)
    try:
        v = await run_in_threadpool(
            _create_verification, db, tenant.id, ktp, selfie, external_ref, callback_url, idempotency_key
        )
    except IntegrityError:  # concurrent retry with the same Idempotency-Key
        db.rollback()
        v = _by_idempotency_key(db, tenant.id, idempotency_key)
    return verification_out(v)


@router.get("/v1/verifications/{vid}", response_model=VerificationOut)
def get_verification(vid: str, tenant: TenantDep, db: DbDep):
    v = db.get(Verification, vid)
    if v is None or v.tenant_id != tenant.id:  # same 404 either way: no cross-tenant existence leak
        raise ApiError(404, "NOT_FOUND", "verification not found")
    return verification_out(v)


@router.post("/v1/sessions", status_code=201, response_model=SessionOut)
def create_session(body: SessionCreate, tenant: TenantDep, db: DbDep):
    """Create a hosted camera-capture link for your end user. Single use, short-lived."""
    s = get_settings()
    _check_url(body.callback_url, security.check_webhook_url, "INVALID_CALLBACK_URL")
    _check_url(body.redirect_url, security.check_redirect_url, "INVALID_REDIRECT_URL")
    token = security.new_token()
    cs = CaptureSession(
        tenant_id=tenant.id,
        token_hash=security.sha256(token),
        external_ref=body.external_ref,
        callback_url=body.callback_url,
        redirect_url=body.redirect_url,
        expires_at=utcnow() + timedelta(minutes=s.capture_session_minutes),
    )
    db.add(cs)
    db.commit()
    return SessionOut(
        session_id=cs.id, capture_url=f"{s.public_base_url.rstrip('/')}/capture/{token}", expires_at=cs.expires_at
    )


@router.get("/healthz")
def healthz(db: DbDep):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


# ---------- hosted capture page ----------


def _open_session(db: Session, token: str) -> CaptureSession | None:
    s = db.scalar(select(CaptureSession).where(CaptureSession.token_hash == security.sha256(token)))
    if s is None or s.used_at is not None or aware(s.expires_at) < utcnow():
        return None
    return s


def _with_session_id(url: str, session_id: str) -> str:
    parts = urlsplit(url)
    query = urlencode([*parse_qsl(parts.query), ("session_id", session_id)])
    return urlunsplit(parts._replace(query=query))


@router.get("/capture/{token}", response_class=HTMLResponse, include_in_schema=False)
def capture_page(request: Request, token: str, db: DbDep):
    if _open_session(db, token) is None:
        ctx = {"title": "Link expired", "message": "This verification link is invalid, already used, or expired."}
        return templates.TemplateResponse(request, "message.html", ctx, status_code=410)
    csrf = security.new_token()
    resp = templates.TemplateResponse(request, "capture.html", {"csrf": csrf})
    resp.set_cookie(
        "csrf",
        csrf,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path=f"{request.scope.get('root_path', '')}/capture/{token}",
        max_age=get_settings().capture_session_minutes * 60,
    )
    return resp


@router.post("/capture/{token}/submit", include_in_schema=False)
async def capture_submit(request: Request, token: str, db: DbDep, x_csrf_token: Annotated[str | None, Header()] = None):
    cookie = request.cookies.get("csrf")
    if not cookie or not x_csrf_token or not hmac.compare_digest(cookie, x_csrf_token):
        raise ApiError(403, "CSRF_FAILED", "invalid CSRF token")
    _check_length(request)
    s = _open_session(db, token)
    if s is None:
        raise ApiError(410, "SESSION_EXPIRED", "capture session invalid, used, or expired")
    form = await request.form(max_files=2, max_fields=2)
    ktp, selfie = await run_in_threadpool(_clean, await _read_upload(form, "ktp"), await _read_upload(form, "selfie"))
    # Atomic claim so two parallel submits cannot both pass.
    claimed = db.execute(
        update(CaptureSession)
        .where(CaptureSession.id == s.id, CaptureSession.used_at.is_(None))
        .values(used_at=utcnow())
    ).rowcount
    db.commit()
    if not claimed:
        raise ApiError(410, "SESSION_EXPIRED", "capture session already used")
    v = await run_in_threadpool(_create_verification, db, s.tenant_id, ktp, selfie, s.external_ref, s.callback_url)
    s.verification_id = v.id
    db.commit()
    # The redirect only carries the session id. The result goes by webhook, so it cannot be forged in a URL.
    return {"status": "received", "redirect_url": _with_session_id(s.redirect_url, s.id) if s.redirect_url else None}


# ---------- app ----------


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.ensure_ready()
    queue.start_local()
    yield
    queue.stop_local()


def create_app(settings: Settings | None = None) -> FastAPI:
    """One app per process. Pass settings to configure in code instead of env vars."""
    if settings is not None:
        set_settings(settings)
    if not get_settings().data_encryption_key:
        raise RuntimeError("DATA_ENCRYPTION_KEY is required by the server: run `id-verification gen-key`")
    app = FastAPI(title="ID Verification API", version=version("id-verification"), lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.middleware("http")(security_headers)
    return app
