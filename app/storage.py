"""S3-compatible object storage (MinIO locally). Images are Fernet-encrypted before upload,
so encryption at rest does not depend on the bucket's SSE/KMS setup."""

from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from app import security
from app.config import get_settings


@lru_cache
def _client():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
    )


def ensure_bucket() -> None:
    bucket = get_settings().s3_bucket
    try:
        _client().head_bucket(Bucket=bucket)
    except ClientError:
        _client().create_bucket(Bucket=bucket)


def put(key: str, data: bytes) -> None:
    _client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=security.encrypt(data))


def get(key: str) -> bytes:
    obj = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return security.decrypt(obj["Body"].read())


def delete(key: str) -> None:
    _client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
