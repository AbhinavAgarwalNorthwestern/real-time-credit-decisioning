"""Root conftest — registers custom pytest markers for CI tiers."""

from __future__ import annotations


def pytest_configure(config):  # noqa: ANN001, ANN201
    config.addinivalue_line(
        'markers', 'integration: cross-module wiring with mocked externals'
    )
    config.addinivalue_line('markers', 'pipeline: end-to-end pipeline in offline mode')
