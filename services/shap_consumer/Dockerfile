FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY src/ src/
RUN uv pip install --system --no-cache .

FROM python:3.12-slim AS base

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY src/ src/

ENV KAFKA_BROKER=kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092 \
    MLFLOW_TRACKING_URI=http://mlflow.mlflow.svc.cluster.local:5000

ENTRYPOINT ["python", "-m", "shap_consumer"]
