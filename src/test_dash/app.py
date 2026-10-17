"""Scatter-plot example built with Dash and Plotly.

Features demonstrated:
  * cascading dropdowns / sliders / checklists feeding a single callback
  * colour-by grouping with a per-group ordinary-least-squares trend line
  * box-select as the default drag mode
  * a ``clickData`` callback that fills an inspector panel with the clicked row

The figures themselves live in :mod:`test_dash.figures` so they can also be
exported offline without Dash (see :mod:`test_dash.export`).
"""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html
from dash.development.base_component import Component

from .data import CROPS, METRICS
from .figures import DATA, build_figure, mean_yield_chart

#: The ``clickData`` payload Plotly delivers to a graph's click callback.
ClickData = dict[str, Any]

METRIC_OPTIONS = [{"label": label, "value": col} for col, label in METRICS.items()]
CROP_OPTIONS = [{"label": crop, "value": crop} for crop in CROPS]

CONTROL_PANEL_STYLE = {"flex": "1 1 180px", "minWidth": "160px"}


# --------------------------------------------------------------------------- #
# Inspector panel
# --------------------------------------------------------------------------- #
def build_inspector(click_data: ClickData | None) -> html.Div:
    """Turn a ``clickData`` payload into the inspector panel contents."""
    if not click_data or not click_data.get("points"):
        return html.Div(
            "Click a marker in the scatter plot to inspect a single field plot.",
            className="inspector-hint",
        )

    point = click_data["points"][0]
    plot_id, crop = point.get("customdata", (None, None))

    # Prefer the source row; fall back to the coordinates Plotly hands back.
    match = DATA.loc[DATA["plot_id"] == plot_id] if plot_id else None
    if match is not None and not match.empty:
        row = match.iloc[0]
        rows = {
            "Crop": crop or row["crop"],
            METRICS["rainfall_mm"]: f"{row['rainfall_mm']:.0f} mm",
            METRICS["temperature_c"]: f"{row['temperature_c']:.1f} °C",
            METRICS["soil_ph"]: f"{row['soil_ph']:.2f}",
            METRICS["yield_t_per_ha"]: f"{row['yield_t_per_ha']:.2f} t/ha",
        }
    else:
        rows = {
            "Crop": crop or "–",
            METRICS["rainfall_mm"]: point.get("x"),
            METRICS["yield_t_per_ha"]: point.get("y"),
        }

    table = html.Table(
        [html.Tr([html.Th(key), html.Td(str(value))]) for key, value in rows.items()],
        className="inspector-table",
    )

    return html.Div(
        [
            html.Div([html.Span("Selected plot"), html.Code(plot_id or "–")], className="inspector-head"),
            table,
            html.P("Mean yield per crop (selection highlighted)", className="inspector-caption"),
            dcc.Graph(figure=mean_yield_chart(highlight=crop), config={"displayModeBar": False}),
        ]
    )


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def _control(label: str, control: Component) -> html.Div:
    return html.Div([html.Label(label), control], className="control", style=CONTROL_PANEL_STYLE)


def create_layout() -> html.Div:
    controls = html.Div(
        [
            _control(
                "X axis",
                dcc.Dropdown(id="x-axis", options=METRIC_OPTIONS, value="rainfall_mm", clearable=False),
            ),
            _control(
                "Y axis",
                dcc.Dropdown(id="y-axis", options=METRIC_OPTIONS, value="yield_t_per_ha", clearable=False),
            ),
            _control(
                "Color by",
                dcc.Dropdown(
                    id="color-by",
                    options=[
                        {"label": "Crop", "value": "crop"},
                        {"label": "Single colour", "value": "none"},
                    ],
                    value="crop",
                    clearable=False,
                ),
            ),
            _control(
                "Crops",
                dcc.Dropdown(id="crops", options=CROP_OPTIONS, value=list(CROPS), multi=True),
            ),
            _control(
                "Marker size",
                dcc.Slider(
                    id="marker-size", min=4, max=24, step=1, value=11,
                    marks={4: "4", 12: "12", 24: "24"},
                ),
            ),
            _control(
                "Opacity",
                dcc.Slider(id="opacity", min=0.1, max=1.0, step=0.05, value=0.75,
                           marks={0.1: "0.1", 0.75: "0.75", 1.0: "1.0"}),
            ),
            _control(
                "OLS trend lines",
                dcc.Checklist(
                    id="trendline",
                    options=[{"label": " Show per-crop trend", "value": "on"}],
                    value=["on"],
                    className="checklist",
                ),
            ),
        ],
        className="controls",
    )

    return html.Div(
        [
            html.Header(
                [
                    html.H1("Field Plot Explorer"),
                    html.P(
                        "Synthetic crop-trial measurements — pick axes, filter crops, "
                        "then box-select or click a marker to inspect it."
                    ),
                ]
            ),
            controls,
            html.Div(
                [
                    html.Div([dcc.Graph(id="scatter-plot")], className="card graph-card"),
                    html.Div([html.H2("Inspector"), build_inspector(None)], className="card inspector-card", id="inspector"),
                ],
                className="panels",
            ),
            html.Footer(
                f"Generated in-process · {len(DATA)} plots · {len(CROPS)} crops · "
                "Dash + Plotly"
            ),
        ],
        className="app-shell",
    )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
def register_callbacks(app: Dash) -> None:
    @app.callback(
        Output("scatter-plot", "figure"),
        Input("x-axis", "value"),
        Input("y-axis", "value"),
        Input("color-by", "value"),
        Input("marker-size", "value"),
        Input("opacity", "value"),
        Input("crops", "value"),
        Input("trendline", "value"),
    )
    def update_scatter(
        x_col: str,
        y_col: str,
        color_by: str,
        marker_size: float,
        opacity: float,
        crops: list[str] | None,
        trendline: list[str] | None,
    ) -> go.Figure:
        return build_figure(
            x_col=x_col,
            y_col=y_col,
            color_by=color_by,
            marker_size=marker_size,
            opacity=opacity,
            crops=crops or [],
            show_trendline="on" in (trendline or []),
        )

    @app.callback(
        Output("inspector", "children"),
        Input("scatter-plot", "clickData"),
        prevent_initial_call=True,
    )
    def update_inspector(click_data: ClickData | None) -> list[Component]:
        body = build_inspector(click_data)
        return [html.H2("Inspector"), body]


def create_app() -> Dash:
    app = Dash(__name__, title="Field Plot Explorer")
    app.layout = create_layout()
    register_callbacks(app)
    return app
