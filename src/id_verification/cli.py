"""id-verification CLI. Secrets are printed once and only their hash/ciphertext is stored.

id-verification gen-key                          new DATA_ENCRYPTION_KEY
id-verification download-models                  fetch the biometric ONNX models
id-verification check-llm                        test the configured model on a specimen KTP
id-verification serve [--host --port --workers]  migrate, then run the API
id-verification migrate                          DB schema + storage only
id-verification create-tenant --name "Acme" [--webhook-url https://...]
id-verification rotate-key --tenant-id <id>
id-verification set-webhook --tenant-id <id> --url https://...
id-verification set-active --tenant-id <id> --active false
id-verification list-tenants
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from id_verification.config import get_settings

SPECIMEN = Path(__file__).parent / "core" / "assets" / "specimen_ktp.jpg"
SPECIMEN_NIK = "3171011208900001"  # synthetic: matches the fake card, belongs to no one


def _check_llm() -> int:
    from id_verification.core.images import sanitize
    from id_verification.core.llm import LLM

    s = get_settings()
    ktp = sanitize(SPECIMEN.read_bytes(), s.max_upload_bytes)
    print(f"model: {s.llm_model}" + (f" @ {s.llm_base_url}" if s.llm_base_url else ""))
    start = time.monotonic()
    try:
        if s.llm_model == "none":
            from id_verification.core import ocr

            ex = ocr.extract_ktp(ktp)
        else:
            ex = asyncio.run(LLM(s).extract_ktp(ktp))
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    nik = ex.data.nik if ex.data else None
    print(f"took {time.monotonic() - start:.1f}s, is_indonesian_ktp={ex.is_indonesian_ktp}, nik={nik}")
    if nik != SPECIMEN_NIK:
        print(f"FAILED: expected NIK {SPECIMEN_NIK}. The model cannot read the card reliably.")
        return 1
    print("OK: image input and structured output work")
    return 0


def _serve(args) -> int:
    import uvicorn

    from id_verification.server import migrate, storage

    if args.workers > 1 and not get_settings().redis_url:
        print("warning: lite mode keeps the queue and rate limits in memory; use --workers 1 or set REDIS_URL")
    migrate()
    storage.ensure_ready()
    uvicorn.run(
        "id_verification.server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers,
        proxy_headers=True,
    )
    return 0


def _tenants(args) -> int:
    from sqlalchemy import select

    from id_verification.server import security
    from id_verification.server.db import Tenant, session

    def webhook(url: str | None) -> str | None:
        if url:
            security.check_webhook_url(url, get_settings().webhook_allow_http)
        return url

    with session() as db:
        if args.cmd == "create-tenant":
            key, secret = security.new_api_key(), security.new_token()
            t = Tenant(
                name=args.name,
                api_key_hash=security.sha256(key),
                webhook_url=webhook(args.webhook_url),
                webhook_secret_enc=security.encrypt_str(secret),
            )
            db.add(t)
            db.commit()
            print(f"tenant_id:      {t.id}\napi_key:        {key}\nwebhook_secret: {secret}")
            print("Store these now. They are not shown again.")
            return 0
        if args.cmd == "list-tenants":
            for t in db.scalars(select(Tenant).order_by(Tenant.created_at)):
                print(f"{t.id}  active={t.active}  {t.name}  webhook={t.webhook_url or '-'}")
            return 0

        t = db.get(Tenant, args.tenant_id)
        if t is None:
            print("tenant not found", file=sys.stderr)
            return 1
        if args.cmd == "rotate-key":
            key = security.new_api_key()
            t.api_key_hash = security.sha256(key)
            print(f"api_key: {key}\nThe old key stopped working.")
        elif args.cmd == "set-webhook":
            t.webhook_url = webhook(args.url)
            print("webhook updated")
        elif args.cmd == "set-active":
            t.active = args.active == "true"
            print(f"active={t.active}")
        db.commit()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="id-verification")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-key")
    sub.add_parser("download-models")
    sub.add_parser("check-llm")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="0.0.0.0")  # noqa: S104  server entry point, bind all interfaces
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--workers", type=int, default=1)
    sub.add_parser("migrate")
    c = sub.add_parser("create-tenant")
    c.add_argument("--name", required=True)
    c.add_argument("--webhook-url")
    r = sub.add_parser("rotate-key")
    r.add_argument("--tenant-id", required=True)
    w = sub.add_parser("set-webhook")
    w.add_argument("--tenant-id", required=True)
    w.add_argument("--url", required=True)
    a = sub.add_parser("set-active")
    a.add_argument("--tenant-id", required=True)
    a.add_argument("--active", choices=["true", "false"], required=True)
    sub.add_parser("list-tenants")
    args = p.parse_args(argv)

    if args.cmd == "download-models":
        from id_verification.core.models import ensure_models

        ensure_models(get_settings().models_dir)
        print(f"models ready in {get_settings().models_dir}")
        return 0
    if args.cmd == "check-llm":
        return _check_llm()
    try:
        if args.cmd == "gen-key":
            from cryptography.fernet import Fernet

            print(Fernet.generate_key().decode())
            return 0
        if args.cmd == "serve":
            return _serve(args)
        if args.cmd == "migrate":
            from id_verification.server import migrate, storage

            migrate()
            storage.ensure_ready()
            print("database and storage ready")
            return 0
        return _tenants(args)
    except ModuleNotFoundError as e:
        print(f"{e.name} is missing: pip install 'id-verification[server]'", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
