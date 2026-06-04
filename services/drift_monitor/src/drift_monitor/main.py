"""Drift monitor.

Consumes decisions + features; emits drift events when PSI/KS/ADWIN
detectors fire. Drift events flow into Argo Events which triggers the
retraining flow (per ADR 003).

Skeleton only at Day 0; Day 5 implements the detectors.
"""

from loguru import logger


def main() -> None:
    logger.info('drift_monitor service: skeleton; Day 5 will implement detectors')


if __name__ == '__main__':
    main()
