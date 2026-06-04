"""Synthetic credit-transaction stream producer.

Emits realistic transaction events into Kafka for downstream feature
computation. Skeleton only at Day 0; Day 1 implements the generator.

See:
- docs/repo_layout.md (services/ shape)
- docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md
  (streaming-plane role)
"""

from loguru import logger


def main() -> None:
    logger.info('transactions service: skeleton; Day 1 will implement the generator')


if __name__ == '__main__':
    main()
