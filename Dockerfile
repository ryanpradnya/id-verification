FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    MODELS_DIR=/app/models \
    DATA_DIR=/data
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project

# Biometric models baked into the image, verified against pinned SHA-256.
COPY src/id_verification/core/models.py /tmp/models.py
RUN python /tmp/models.py /app/models

COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --all-extras

RUN useradd --system --uid 1000 app && mkdir -p /data && chown app /data
USER app
VOLUME /data

EXPOSE 8000
CMD ["id-verification", "serve", "--host", "0.0.0.0", "--port", "8000"]
