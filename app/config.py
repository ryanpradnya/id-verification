from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infra
    database_url: str = "postgresql+psycopg://kyc:kyc@localhost:5432/kyc"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str | None = "http://localhost:8333"
    s3_bucket: str = "kyc"
    s3_access_key: str = "local"
    s3_secret_key: str = "local"  # noqa: S105  local SeaweedFS default
    s3_region: str = "us-east-1"
    public_base_url: str = "http://localhost:8000"

    # Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    data_encryption_key: str

    # AI provider: any LangChain init_chat_model id. Must support image input.
    llm_model: str = "anthropic:claude-sonnet-5-5"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_fallback_model: str | None = None
    llm_structured_method: str | None = None  # json_schema | function_calling | json_mode; None = auto
    llm_timeout_seconds: float = 60

    # Biometric models
    models_dir: Path = Path("models")
    face_match_threshold: float = 0.363  # SFace cosine, OpenCV recommended value
    liveness_threshold: float = 0.80  # MiniFASNet "real" probability

    # Image quality gates
    max_upload_bytes: int = 10 * 1024 * 1024
    min_image_side: int = 300
    min_blur_variance: float = 30.0
    min_brightness: float = 40.0

    # Policy
    retention_days: int = 30
    rate_limit_per_minute: int = 60
    capture_session_minutes: int = 15
    webhook_allow_http: bool = False  # dev only: allows http:// and private hosts


@lru_cache
def get_settings() -> Settings:
    return Settings()
