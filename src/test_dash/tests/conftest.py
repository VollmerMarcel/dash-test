"""Fixtures and markers shared by more than one test module.

The suite mirrors the source layout - one test module per source module - so
anything two of them need lives here rather than being duplicated.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from test_dash.data import build_dataset
from test_dash.export import Preset, preset_figures


@pytest.fixture(scope="session")
def presets() -> list[Preset]:
    """The nine-figure gallery the exporter writes."""
    return preset_figures()


@pytest.fixture(scope="session")
def dataset() -> pd.DataFrame:
    """The synthetic field-trial dataset, built once per run."""
    return build_dataset()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip the ``browser``-marked tests unless ``TEST_DASH_BROWSER_EXPORT=1``.

    Marking instead of naming keeps the default run fast and hermetic, and it
    survives renaming the tests - unlike a ``-k`` substring.
    """
    if os.environ.get("TEST_DASH_BROWSER_EXPORT") == "1":
        return
    skip = pytest.mark.skip(
        reason="set TEST_DASH_BROWSER_EXPORT=1 to drive a real browser"
    )
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)
