"""Retraining flow (Metaflow).

Fan-out training across customer segments. Triggered by drift events or
scheduled. Runs on K8s via Metaflow `@kubernetes` by default; AWS Batch
via `@batch` as the AWS-overlay option (ADR 003).

Skeleton only at Day 0; Day 5 implements the FlowSpec.

See:
- docs/decisions/003-metaflow-kubernetes-not-batch.md
- docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md
  (the batch plane this flow lives on)
"""

from loguru import logger


def main() -> None:
    logger.info('retraining_flow: skeleton; Day 5 will implement the FlowSpec')


if __name__ == '__main__':
    main()
