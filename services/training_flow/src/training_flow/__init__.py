"""Day-2 offline training pipeline for the realtime-credit-decisioning platform.

Per ADR 010, treatment assignment is synthetic RCT (T ~ Bernoulli(0.5) per
training row, independent of features). The pipeline consumes RisingWave
materialized views (per ADR 002/009) as the feature store, simulates labels
from each customer's embedded true response parameters, and produces a
parquet dataset for per-segment T-learner training.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from loguru import logger

__version__ = '0.1.0'


class _LoguruAdapter:
    """Adapter so modules using `log = get_logger(__name__)` + `log.info('event', k=v)`
    emit via loguru with structured key=value output."""

    def __init__(self, module: str) -> None:
        self._log = logger.bind(module=module)

    def _msg(self, event: str, kw: dict[str, Any]) -> str:
        if not kw:
            return event
        pairs = ' '.join(f'{k}={v!r}' for k, v in kw.items())
        return f'{event} | {pairs}'

    def info(self, event: str, **kw: Any) -> None:
        self._log.info(self._msg(event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self._log.warning(self._msg(event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self._log.error(self._msg(event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self._log.debug(self._msg(event, kw))


def get_logger(name: str) -> _LoguruAdapter:
    """Drop-in for structlog.get_logger(__name__) — routes through loguru."""
    return _LoguruAdapter(name)


def _configure_logging() -> None:
    """Loguru logging — colored, timestamped, structured."""
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            '<green>{time:HH:mm:ss.SS}</green> | '
            '<level>{level: <8}</level> | '
            '<level>{message}</level>'
        ),
        level='INFO',
        colorize=True,
    )
    logging.getLogger('optuna').setLevel(logging.WARNING)
    logging.getLogger('kubernetes').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


_configure_logging()
