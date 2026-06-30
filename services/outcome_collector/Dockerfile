FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY src/ src/
RUN uv pip install --system --no-cache .

ENV KAFKA_BROKER=kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092

ENTRYPOINT ["python", "-m", "outcome_collector"]
