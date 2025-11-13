ARG PYTHON_VERSION=3.11.9-slim-bookworm

FROM python:${PYTHON_VERSION} AS builder

ENV POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \ 
    && apt-get install -y --no-install-recommends build-essential curl \ 
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /build

COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
COPY scripts ./scripts

# Build wheel (prod deps only)
RUN poetry build -f wheel


FROM python:${PYTHON_VERSION} AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_ROOT=/data/files

WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -rf /tmp/*.whl /root/.cache

EXPOSE 8000

# Railway sets $PORT dynamically; fallback to 8000 for local testing
# Note: Railway handles health checks via railway.toml healthcheckPath="/readyz"
ENV WEB_CONCURRENCY=1
CMD ["sh", "-c", "exec hypercorn 'data_bank_api.app:create_app()' --bind [::]:${PORT:-8000}"]
