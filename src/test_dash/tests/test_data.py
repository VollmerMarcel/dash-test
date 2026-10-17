"""Tests for the synthetic dataset builder (:mod:`test_dash.data`)."""

from __future__ import annotations

import pandas as pd

from test_dash.data import CROPS, METRICS, build_dataset

#: Every column except the identifier, in dataset order.
AXIS_IDS = ["crop", "rainfall_mm", "temperature_c", "soil_ph", "yield_t_per_ha"]


def test_dataset_shape_and_columns(dataset: pd.DataFrame) -> None:
    assert list(dataset.columns) == ["plot_id", *AXIS_IDS]
    assert len(dataset) == 60 * len(CROPS)


def test_dataset_is_reproducible() -> None:
    pd.testing.assert_frame_equal(build_dataset(), build_dataset())


def test_no_missing_or_invalid_values(dataset: pd.DataFrame) -> None:
    assert not dataset.isna().any().any()
    assert (dataset["rainfall_mm"] > 0).all()
    assert (dataset["soil_ph"].between(4.0, 8.5)).all()
    assert (dataset["yield_t_per_ha"] > 0).all()


def test_metrics_cover_every_numeric_column(dataset: pd.DataFrame) -> None:
    numeric = set(dataset.select_dtypes("number").columns)
    assert set(METRICS) == numeric
