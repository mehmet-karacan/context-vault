FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libmagic1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Trust the corporate root CA so the AI gateway's TLS-intercepted
# certificate chain (aihub-api.turktelekom.com.tr) verifies correctly.
COPY ttroot-g3.crt /usr/local/share/ca-certificates/ttroot-g3.crt
RUN update-ca-certificates

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# httpx/requests use certifi's bundled CA file, not the OS trust store —
# append the corporate root there too so outbound TLS to the gateway verifies.
RUN cat /usr/local/share/ca-certificates/ttroot-g3.crt >> "$(python -c 'import certifi; print(certifi.where())')"

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]