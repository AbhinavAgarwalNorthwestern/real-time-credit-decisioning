FROM python:3.12-slim AS base

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY src/ src/
RUN uv pip install --system --no-cache .

ENV DECISIONER_HOST=0.0.0.0 \
    DECISIONER_PORT=8080 \
    DECISIONER_LOG_LEVEL=INFO \
    DECISIONER_WORKERS=2

EXPOSE 8080

ENTRYPOINT ["python", "-m", "decisioner.main"]
