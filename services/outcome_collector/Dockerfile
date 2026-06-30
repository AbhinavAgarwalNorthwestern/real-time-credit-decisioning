FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

# Build context = repo root (CD workflow). Use -f services/outcome_collector/Dockerfile .
COPY services/outcome_collector/pyproject.toml ./
COPY services/outcome_collector/src/ ./src/
RUN uv pip install --system --no-cache .

ENV KAFKA_BROKER=kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092

ENTRYPOINT ["python", "-m", "outcome_collector"]
