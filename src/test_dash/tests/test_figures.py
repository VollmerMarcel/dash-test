"""Tests for the figure builders (:mod:`test_dash.figures`)."""

from __future__ import annotations

import subprocess
import sys

import pandas as pd

from test_dash.data import CROPS
from test_dash.figures import build_figure, mean_yield_chart

ALL_CROPS = list(CROPS)


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
    geometry is checked by the opt-in browser test in ``test_browser_render.py``.
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


def test_mean_yield_chart_highlight_only_colours_one_bar() -> None:
    colors = list(mean_yield_chart(highlight="Wheat").data[0].marker.color)
    assert colors.count("#d8d8d8") == len(CROPS) - 1


# --------------------------------------------------------------------------- #
# Architecture guard
# --------------------------------------------------------------------------- #
def test_figures_module_does_not_import_dash() -> None:
    """The export path must stay usable without dash/flask."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, test_dash.figures, test_dash.export; print('dash' in sys.modules)"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
