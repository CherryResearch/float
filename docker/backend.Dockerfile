# Multi-stage build for the Float backend (API + workers)
ARG PYTHON_BASE=mcr.microsoft.com/devcontainers/python:3.12
ARG PYTHON_RUNTIME_BASE=mcr.microsoft.com/devcontainers/python:3.12
FROM ${PYTHON_BASE} AS builder

ARG POETRY_VERSION=1.8.3
ARG POETRY_WITH=""
ARG POETRY_EXTRAS=""

ENV POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    PATH="$POETRY_HOME/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade --no-cache-dir pip \
    && python -m pip install --no-cache-dir "poetry==${POETRY_VERSION}"

COPY pyproject.toml poetry.lock* ./

# Export Poetry dependencies to a plain requirements file (optionally incl. extras)
RUN if [ -n "$POETRY_WITH" ] && [ -n "$POETRY_EXTRAS" ]; then \
        poetry export --format=requirements.txt --output=requirements.txt --without-hashes --with "$POETRY_WITH" --extras "$POETRY_EXTRAS"; \
    elif [ -n "$POETRY_WITH" ]; then \
        poetry export --format=requirements.txt --output=requirements.txt --without-hashes --with "$POETRY_WITH"; \
    elif [ -n "$POETRY_EXTRAS" ]; then \
        poetry export --format=requirements.txt --output=requirements.txt --without-hashes --extras "$POETRY_EXTRAS"; \
    else \
        poetry export --format=requirements.txt --output=requirements.txt --without-hashes; \
    fi

FROM ${PYTHON_RUNTIME_BASE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System packages that are needed by numerical libraries and media tooling
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/requirements.txt ./

RUN python -m pip install --upgrade --no-cache-dir pip setuptools wheel \
    && python -m pip install --no-cache-dir -r requirements.txt

# Copy backend sources
COPY backend/ ./backend/

WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
