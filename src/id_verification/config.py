import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "id-verification"


class Settings(BaseSettings):
    """All config, read from env / .env or passed in code: Settings(llm_model="none").

    Library use only needs the AI and biometric fields. The rest is for the server.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI provider: any LangChain init_chat_model id that accepts images, or "none" for local OCR only.
    # Local / self-hosted: "openai:<model>" + llm_base_url (Ollama, vLLM, LM Studio, llama.cpp).
    llm_model: str = "none"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_fallback_model: str | None = None
    llm_structured_method: str | None = None  # json_schema | function_calling | json_mode; None = auto
    llm_timeout_seconds: float = 60

    # Biometric models (downloaded on first use, SHA-256 checked)
    models_dir: Path = _cache_dir() / "models"
    face_match_threshold: float = 0.363  # SFace cosine, OpenCV recommended value
    liveness_threshold: float = 0.80  # MiniFASNet "real" probability

    # Image quality gates
    max_upload_bytes: int = 10 * 1024 * 1024
    min_image_side: int = 300
    min_blur_variance: float = 30.0
    min_brightness: float = 40.0

    # ---- server only ----
    # Fernet key: id-verification gen-key. Required by the server, unused by the library.
    data_encryption_key: str | None = None
    public_base_url: str = "http://localhost:8000"
    data_dir: Path = Path("data")  # lite mode: SQLite file + encrypted image blobs
    database_url: str | None = None  # None = sqlite in data_dir
    redis_url: str | None = None  # set = Celery queue + Redis rate limit; None = in-process
    worker_concurrency: int = 2  # in-process queue threads
    s3_bucket: str | None = None  # set = S3 storage; None = local disk in data_dir
    s3_endpoint_url: str | None = None  # unset for AWS S3
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    # Policy
    retention_days: int = 30
    rate_limit_per_minute: int = 60
    capture_session_minutes: int = 15
    webhook_allow_http: bool = False  # dev only: allows http:// and private hosts

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'idv.db'}"


_override: Settings | None = None


def set_settings(s: Settings) -> None:
    """Used by create_app(settings): one configuration per process."""
    global _override
    _override = s
    get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    return _override or Settings()
