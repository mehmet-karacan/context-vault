FROM python:3.12-alpine@sha256:b64631e04e4920160c50fbe8d8df828f7f35f06f425cb44aa09bca53e708a35a

WORKDIR /app

# Install the one runtime library required by python-magic and apply the
# current Alpine security repository updates in the same layer.
RUN apk upgrade --no-cache && apk add --no-cache libmagic

# Install the exact locked runtime dependency graph.
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH"

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
