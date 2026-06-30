FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

# Build context should be set to repo root:
#   docker build -t retraining-flow -f services/retraining_flow/Dockerfile .
COPY services/retraining_flow/pyproject.toml ./pyproject.toml
COPY services/retraining_flow/src/ ./src/
COPY services/training_flow/pyproject.toml /deps/training_flow/pyproject.toml
COPY services/training_flow/src/ /deps/training_flow/src/
# transactions needed because training_flow's customer_params_loader does
# `from transactions.customer import generate_cohort` for label simulation
COPY services/transactions/pyproject.toml /deps/transactions/pyproject.toml
COPY services/transactions/src/ /deps/transactions/src/

RUN uv pip install --system --no-cache . /deps/training_flow/ /deps/transactions/

ENV MLFLOW_TRACKING_URI=http://mlflow.mlflow.svc.cluster.local:5000

ENTRYPOINT ["python", "-m", "retraining_flow.flow"]
