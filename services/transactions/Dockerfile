FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

# Build context = repo root (CD workflow). Use -f services/transactions/Dockerfile .
COPY services/transactions/pyproject.toml ./
COPY services/transactions/src/ ./src/
RUN uv pip install --system --no-cache .

ENV TXN_MODE=live \
    TXN_COHORT_SIZE=1000 \
    TXN_SEED=42 \
    TXN_LOG_LEVEL=INFO

ENTRYPOINT ["python", "-m", "transactions.main"]
