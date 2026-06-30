FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

# Copy source BEFORE installing — hatchling's `packages = ["src/training_flow"]`
# requires the source tree to exist at install time (per Day-1 D-5 fix log
# item 20 in docs/STATUS.md). Trading rebuild-cache efficiency for correctness.
COPY pyproject.toml .
COPY src/ src/
RUN uv pip install --system --no-cache .

ENV TF_LOG_LEVEL=INFO

ENTRYPOINT ["python", "-m", "training_flow"]
