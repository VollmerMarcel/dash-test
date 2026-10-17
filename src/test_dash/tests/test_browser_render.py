"""Opt-in tests that drive a real browser.

Marked ``browser`` and gated behind ``TEST_DASH_BROWSER_EXPORT=1`` (see
``conftest.py``), so the default run stays fast and hermetic:

    TEST_DASH_BROWSER_EXPORT=1 uv run pytest -m browser
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_dash.browsers import (
    ChromiumRenderer,
    FirefoxBiDi,
    figure_to_html,
    find_firefox,
    kaleido_installed,
    requested_browser,
)
from test_dash.export import preset_figures

pytestmark = pytest.mark.browser

_GEOMETRY_JS = """
(async () => {
  const gd = document.getElementById('fig');
  await window.__plotReady;
  const fl = gd._fullLayout;
  const gdRect = gd.getBoundingClientRect();
  const rel = (r) => ({top: r.top - gdRect.top, bottom: r.bottom - gdRect.top});
  const title = rel(gd.querySelector('.infolayer text.gtitle').getBoundingClientRect());
  const legendEl = gd.querySelector('g.legend');
  const legend = legendEl ? rel(legendEl.getBoundingClientRect()) : null;
  return JSON.stringify({plotTop: fl._size.t, title: title, legend: legend});
})()
"""


def test_render_png_through_firefox(tmp_path: Path) -> None:
    preset = preset_figures()[0]
    destination = tmp_path / "figure.png"
    with FirefoxBiDi(find_firefox()) as browser:
        browser.render(preset.figure, destination, width=400, height=300, scale=1)

    data = destination.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 5_000


def test_render_png_through_chromium(tmp_path: Path) -> None:
    if not kaleido_installed():
        pytest.skip("kaleido not installed (uv sync --extra chromium)")

    preset = preset_figures()[0]
    destination = tmp_path / "figure.png"
    with ChromiumRenderer(requested_browser()) as renderer:
        renderer.render(preset.figure, destination, width=400, height=300, scale=1)

    data = destination.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 5_000


def test_title_keeps_clear_of_the_legend_and_plot_area(tmp_path: Path) -> None:
    """Regression: the top legend used to be drawn straight through the title."""
    preset = next(p for p in preset_figures() if p.name == "01_rainfall_vs_yield")
    html_path = tmp_path / "geometry.html"
    html_path.write_text(
        figure_to_html(preset.figure, width=preset.width, height=preset.height),
        encoding="utf-8",
    )

    with FirefoxBiDi(find_firefox()) as browser:
        context = browser.open_context()
        browser.navigate(html_path.as_uri(), context)
        geometry = json.loads(browser.evaluate(_GEOMETRY_JS, context))

    title = geometry["title"]
    assert title["bottom"] <= geometry["plotTop"], "title overlaps the plot area"
    legend = geometry["legend"]
    assert legend is not None, "expected a legend"
    assert legend["top"] >= title["bottom"], "legend overlaps the title"
