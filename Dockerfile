FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caches unless pyproject.toml changes)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Copy source
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 7000

CMD ["uvicorn", "runbookai.main:app", "--host", "0.0.0.0", "--port", "7000"]
