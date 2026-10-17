"""Plotly figure builders.

This module is intentionally **dash-free**: it only imports plotly, numpy and
pandas, so the offline exporter in :mod:`test_dash.export` can reuse the exact
same figures as the web app without pulling in Dash or starting a server.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .data import CROPS, METRICS, build_dataset

#: The dataset every figure is built from (generated once per process).
DATA: pd.DataFrame = build_dataset()

#: Stable per-crop colour used by both the scatter groups and the trend lines.
CROP_COLORS: dict[str, str] = dict(zip(CROPS, px.colors.qualitative.Set2))


def filter_frame(crops: list[str]) -> pd.DataFrame:
    """Return rows for the selected crops, guarding against an empty selection."""
    if not crops:
        return DATA.iloc[0:0]
    return DATA[DATA["crop"].isin(crops)]


def _trend_traces(frame: pd.DataFrame, x_col: str, y_col: str) -> list[go.Scatter]:
    """Build one dashed OLS trend line per crop group."""
    traces: list[go.Scatter] = []
    if frame.empty:
        return traces

    for crop, group in frame.groupby("crop", sort=False):
        x = group[x_col].to_numpy(dtype=float)
        y = group[y_col].to_numpy(dtype=float)
        if x.size < 3 or np.ptp(x) == 0:
            continue
        slope, intercept = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 40)
        traces.append(
            go.Scatter(
                x=xs,
                y=slope * xs + intercept,
                mode="lines",
                # The group key is the crop name; pandas types it as a Hashable,
                # so narrow it to str for the colour lookup.
                line={"color": CROP_COLORS.get(str(crop), "#888"), "width": 2, "dash": "dot"},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    return traces


def build_figure(
    x_col: str,
    y_col: str,
    color_by: str,
    marker_size: float,
    opacity: float,
    crops: list[str],
    show_trendline: bool,
) -> go.Figure:
    """Assemble the scatter figure from the current control values."""
    frame = filter_frame(crops)

    if frame.empty:
        figure = go.Figure()
        figure.update_layout(
            annotations=[
                {
                    "text": "Select at least one crop",
                    "xref": "paper",
                    "yref": "paper",
                    "showarrow": False,
                    "font": {"size": 18, "color": "#999"},
                }
            ],
            template="plotly_white",
        )
        return figure

    figure = px.scatter(
        frame,
        x=x_col,
        y=y_col,
        color="crop" if color_by == "crop" else None,
        color_discrete_map=CROP_COLORS,
        custom_data=["plot_id", "crop"],
        labels=METRICS,
        hover_data={
            "crop": True,
            x_col: ":.1f",
            y_col: ":.2f",
            "plot_id": False,
        },
        title=f"{METRICS[y_col]} vs {METRICS[x_col]}",
    )

    for trace in figure.data:
        trace.marker.update(size=marker_size, opacity=opacity, line={"width": 1, "color": "#fff"})

    if show_trendline:
        figure.add_traces(_trend_traces(frame, x_col, y_col))

    figure.update_layout(
        template="plotly_white",
        clickmode="event+select",
        dragmode="select",
        # Pin the title to the top of the figure and give the horizontal legend
        # its own row above the plot area.  Without this plotly centres the
        # title inside the top margin, right where the legend is drawn, so the
        # legend runs straight through the title.
        title={"yref": "container", "y": 0.98, "yanchor": "top"},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 50, "r": 20, "t": 90, "b": 45},
        uirevision=f"{x_col}-{y_col}",
    )
    figure.update_xaxes(showgrid=True, zeroline=False)
    figure.update_yaxes(showgrid=True, zeroline=False)
    return figure


def mean_yield_chart(
    highlight: str | None = None,
    color_by_crop: bool = False,
) -> go.Figure:
    """Small horizontal bar chart of mean yield per crop for the inspector.

    ``highlight`` colours a single crop (the app's selection).  With
    ``color_by_crop`` every bar takes its own crop colour, which is what the
    offline export uses so the image stands on its own.
    """
    # The double brackets keep this a DataFrame (a single column selection would
    # otherwise collapse it to a Series).
    means = (
        DATA.groupby("crop", as_index=False)[["yield_t_per_ha"]]
        .mean()
        .sort_values("yield_t_per_ha")
    )
    colors = [
        CROP_COLORS.get(crop, "#ccc") if (color_by_crop or crop == highlight) else "#d8d8d8"
        for crop in means["crop"]
    ]
    chart = go.Bar(
        x=means["yield_t_per_ha"],
        y=means["crop"],
        orientation="h",
        marker={"color": colors, "line": {"color": "#fff", "width": 1}},
        hovertemplate="%{y}: %{x:.2f} t/ha<extra></extra>",
    )
    figure = go.Figure(data=[chart])
    figure.update_layout(
        template="plotly_white",
        margin={"l": 45, "r": 10, "t": 25, "b": 30},
        height=210,
        xaxis_title="Mean yield (t/ha)",
        yaxis={"visible": False},
        showlegend=False,
        clickmode="event+select",
    )
    return figure
