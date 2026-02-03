FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Mirror repo layout so pyproject.toml relative paths resolve correctly
WORKDIR /workspace/apps/vps-admin-api
COPY packages/python/vsa-common /workspace/packages/python/vsa-common
COPY apps/vps-admin-api/pyproject.toml apps/vps-admin-api/uv.lock* ./

RUN uv sync --frozen --no-dev --no-install-project || uv sync --no-dev --no-install-project

COPY apps/vps-admin-api/src/ src/
RUN uv sync --frozen --no-dev || uv sync --no-dev

FROM python:3.12-slim

RUN useradd -r -s /bin/false appuser
WORKDIR /app

COPY --from=builder /workspace/apps/vps-admin-api/.venv /app/.venv
COPY --from=builder /workspace/apps/vps-admin-api/src /app/src
COPY apps/vps-admin-api/alembic/ alembic/
COPY apps/vps-admin-api/alembic.ini .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); r.raise_for_status()"

CMD ["python", "-m", "uvicorn", "vsa_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
