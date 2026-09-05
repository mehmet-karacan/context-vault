ARG PYTHON_IMAGE=python:3.12-alpine@sha256:b64631e04e4920160c50fbe8d8df828f7f35f06f425cb44aa09bca53e708a35a
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /app

# Install the exact locked runtime dependency graph.
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

WORKDIR /app

# Keep compilers and uv out of the runtime image. The numeric identity makes
# the non-root contract stable even when the image is run without /etc/passwd.
RUN apk add --no-cache ca-certificates git libmagic \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

COPY --chown=10001:10001 . .

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
