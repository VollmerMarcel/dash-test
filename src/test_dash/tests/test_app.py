"""Tests for the Dash app itself (:mod:`test_dash.app`)."""

from __future__ import annotations

import pandas as pd

from test_dash.app import build_inspector, create_app


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
