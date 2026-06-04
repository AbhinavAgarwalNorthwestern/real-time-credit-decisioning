"""Behavioral-feature streaming transformer.

Consumes `transactions` Kafka topic, computes stateful per-customer
features (velocity, utilization, MCC entropy, time-since-last,
geo-anomaly), materializes into RisingWave for serving.

Skeleton only at Day 0; Day 1 implements the aggregations.

See:
- docs/decisions/002-risingwave-as-feature-store-not-feast.md
- services/technical_indicators (the crypto analog of this pattern)
"""

from loguru import logger


def main() -> None:
    logger.info(
        'behavioral_features service: skeleton; Day 1 will implement aggregations'
    )


if __name__ == '__main__':
    main()
