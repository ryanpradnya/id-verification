"""Admin CLI. Secrets are printed once and only their hash/ciphertext is stored.

python -m app.cli create-tenant --name "Acme" [--webhook-url https://...]
python -m app.cli rotate-key --tenant-id <id>
python -m app.cli set-webhook --tenant-id <id> --url https://...
python -m app.cli set-active --tenant-id <id> --active false
python -m app.cli list-tenants
python -m app.cli ensure-bucket
"""

import argparse
import sys

from sqlalchemy import select

from app import security, storage
from app.config import get_settings
from app.db import Tenant, session


def _webhook(url: str | None) -> str | None:
    if url:
        security.check_webhook_url(url, get_settings().webhook_allow_http)
    return url


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
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
    sub.add_parser("ensure-bucket")
    args = p.parse_args(argv)

    if args.cmd == "ensure-bucket":
        storage.ensure_bucket()
        print("bucket ready")
        return 0

    with session() as db:
        if args.cmd == "create-tenant":
            key, secret = security.new_api_key(), security.new_token()
            t = Tenant(
                name=args.name,
                api_key_hash=security.sha256(key),
                webhook_url=_webhook(args.webhook_url),
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
            t.webhook_url = _webhook(args.url)
            print("webhook updated")
        elif args.cmd == "set-active":
            t.active = args.active == "true"
            print(f"active={t.active}")
        db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
