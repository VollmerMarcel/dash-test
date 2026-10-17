"""Unit tests for the data builder and figure helpers (no browser required)."""

from __future__ import annotations

import pandas as pd
import pytest

from test_dash.app import build_figure, build_inspector, create_app
from test_dash.data import CROPS, METRICS, build_dataset

ALL_CROPS = list(CROPS)
AXIS_IDS = ["crop", "rainfall_mm", "temperature_c", "soil_ph", "yield_t_per_ha"]


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    return build_dataset()


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


def test_build_figure_returns_one_trace_per_crop(dataset: pd.DataFrame) -> None:
    figure = build_figure(
        x_col="rainfall_mm",
        y_col="yield_t_per_ha",
        color_by="crop",
        marker_size=11,
        opacity=0.75,
        crops=ALL_CROPS,
        show_trendline=False,
    )
    assert len(figure.data) == len(CROPS)
    assert {trace.name for trace in figure.data} == set(CROPS)
    assert figure.layout.dragmode == "select"


def test_build_figure_gives_the_title_and_legend_their_own_rows() -> None:
    """Regression: the horizontal legend used to be drawn through the title.

    The title is pinned to the top of the figure and the top margin is large
    enough for the legend's own row above the plot area.  The exact pixel
    geometry is checked by the opt-in browser test in ``test_export.py``.
    """
    figure = build_figure(
        x_col="rainfall_mm",
        y_col="yield_t_per_ha",
        color_by="crop",
        marker_size=11,
        opacity=0.75,
        crops=ALL_CROPS,
        show_trendline=True,
    )

    # title anchored to the figure container, near the very top
    assert figure.layout.title.yref == "container"
    assert figure.layout.title.yanchor == "top"
    assert figure.layout.title.y > 0.9

    # horizontal legend anchored above the plot area, below the title
    assert figure.layout.legend.orientation == "h"
    assert figure.layout.legend.yanchor == "bottom"
    assert figure.layout.legend.y >= 1.0

    # enough head room for a 17px title plus a 29px legend row
    assert figure.layout.margin.t >= 80


def test_build_figure_single_colour() -> None:
    figure = build_figure(
        x_col="soil_ph",
        y_col="yield_t_per_ha",
        color_by="none",
        marker_size=8,
        opacity=0.5,
        crops=ALL_CROPS,
        show_trendline=False,
    )
    assert len(figure.data) == 1


def test_build_figure_trend_lines_add_traces() -> None:
    figure = build_figure(
        x_col="temperature_c",
        y_col="yield_t_per_ha",
        color_by="crop",
        marker_size=11,
        opacity=0.75,
        crops=ALL_CROPS,
        show_trendline=True,
    )
    # One dashed OLS line per crop, with no legend entries of their own.
    trends = [t for t in figure.data if t.mode == "lines"]
    assert len(trends) == len(CROPS)
    assert all(not t.showlegend for t in trends)


def test_build_figure_empty_selection() -> None:
    figure = build_figure(
        x_col="rainfall_mm",
        y_col="yield_t_per_ha",
        color_by="crop",
        marker_size=11,
        opacity=0.75,
        crops=[],
        show_trendline=True,
    )
    assert not figure.data
    assert figure.layout.annotations[0].text == "Select at least one crop"


def test_build_figure_filters_crops() -> None:
    figure = build_figure(
        x_col="rainfall_mm",
        y_col="yield_t_per_ha",
        color_by="crop",
        marker_size=11,
        opacity=0.75,
        crops=["Wheat"],
        show_trendline=False,
    )
    assert {trace.name for trace in figure.data} == {"Wheat"}


def test_inspector_without_click_shows_hint() -> None:
    hint = build_inspector(None)
    assert "Click a marker" in hint.children


def test_inspector_reads_click_data(dataset: pd.DataFrame) -> None:
    plot_id = dataset.iloc[0]["plot_id"]
    crop = dataset.iloc[0]["crop"]
    panel = build_inspector({"points": [{"x": 100, "y": 5, "customdata": [plot_id, crop]}]})
    rendered = str(panel.to_plotly_json())
    assert plot_id in rendered          # header code badge
    assert crop in rendered             # value table row


def test_app_exposes_graph_and_controls() -> None:
    app = create_app()
    app._setup_server()
    layout = str(app.layout.to_plotly_json())
    for component_id in ("scatter-plot", "x-axis", "y-axis", "color-by", "crops",
                         "marker-size", "opacity", "trendline", "inspector"):
        assert f"'{component_id}'" in layout
