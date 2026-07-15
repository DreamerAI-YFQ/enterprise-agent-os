"""Pytest configuration for infra tests.

Integration tests require live PG/Redis/LLM services. They are skipped by
default; set ``EAOS_RUN_INTEGRATION=1`` to opt in.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("EAOS_RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="integration test; set EAOS_RUN_INTEGRATION=1 to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
