"""Image storage: local disk (lite default) or S3-compatible (set S3_BUCKET).

Images are Fernet-encrypted before they reach either backend, so encryption at rest
does not depend on disk or bucket settings.
"""

from functools import lru_cache
from pathlib import Path

from id_verification.config import Settings, get_settings
from id_verification.server import security


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("storage key escapes the storage root")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


class S3Storage:
    def __init__(self, s: Settings):
        import boto3  # lazy: optional [scale] extra

        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def ensure_ready(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)


@lru_cache
def backend() -> LocalStorage | S3Storage:
    s = get_settings()
    return S3Storage(s) if s.s3_bucket else LocalStorage(s.data_dir / "blobs")


def ensure_ready() -> None:
    backend().ensure_ready()


def put(key: str, data: bytes) -> None:
    backend().put(key, security.encrypt(data))


def get(key: str) -> bytes:
    return security.decrypt(backend().get(key))


def delete(key: str) -> None:
    backend().delete(key)
