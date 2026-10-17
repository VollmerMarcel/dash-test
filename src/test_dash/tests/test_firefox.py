"""Tests for the headless-Firefox backend (:mod:`test_dash.browsers.firefox`).

Everything here runs without a browser: the discovery tests fake the search,
and the HTML/data-URI helpers are pure functions.  The tests that actually
drive Firefox live in ``test_browser_render.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from test_dash.browsers import (
    ExportError,
    FirefoxUnavailable,
    decode_data_url,
    figure_to_html,
    find_firefox,
    plotly_js_path,
)
from test_dash.export import Preset


# --------------------------------------------------------------------------- #
# Firefox discovery
# --------------------------------------------------------------------------- #
def test_find_firefox_rejects_missing_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(FirefoxUnavailable):
        find_firefox(tmp_path / "no" / "such" / "firefox")


@pytest.mark.skipif(os.name != "nt", reason="the install locations are Windows-only")
def test_find_firefox_uses_the_windows_locations_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows is the one platform where Firefox may sit outside the PATH."""
    import test_dash.browsers.firefox as firefox_module

    binary = tmp_path / "Mozilla Firefox" / "firefox.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake binary")

    monkeypatch.setattr(firefox_module.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no" / "such" / "localappdata"))

    assert find_firefox() == binary


def test_find_firefox_ignores_the_windows_locations_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On Linux and macOS Firefox is expected on the PATH, not in Program Files."""
    import test_dash.browsers.firefox as firefox_module

    binary = tmp_path / "Mozilla Firefox" / "firefox"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake binary")

    def _windows_only() -> list[Path]:
        raise AssertionError("the Windows locations are only searched on Windows")

    monkeypatch.setattr(firefox_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(firefox_module, "_windows_install_locations", _windows_only)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(firefox_module.os, "name", "posix")

    with pytest.raises(FirefoxUnavailable):
        find_firefox()


def test_find_firefox_locates_local_firefox() -> None:
    """Only meaningful on this machine; skipped where Firefox is absent."""
    try:
        found = find_firefox()
    except FirefoxUnavailable:
        pytest.skip("no firefox installed")
    assert found.is_file()


# --------------------------------------------------------------------------- #
# HTML page
# --------------------------------------------------------------------------- #
def test_figure_to_html_is_self_contained(presets: list[Preset]) -> None:
    html = figure_to_html(presets[0].figure, width=800, height=600)
    assert html.startswith("<!DOCTYPE html>")
    assert "Plotly.newPlot" in html
    assert "window.__plotReady" in html
    # no external scripts: the whole library is inlined, so nothing is fetched
    assert "<script src=" not in html
    assert len(html) > 1_000_000
    assert plotly_js_path().is_file()


def test_figure_to_html_applies_requested_size(presets: list[Preset]) -> None:
    html = figure_to_html(presets[0].figure, width=1234, height=567)
    assert '"width": 1234' in html
    assert '"height": 567' in html
    assert "1234px" in html and "567px" in html


def test_figure_to_html_can_skip_the_library(presets: list[Preset]) -> None:
    lean = figure_to_html(presets[0].figure, include_plotlyjs=False)
    assert "Plotly.newPlot" in lean
    assert len(lean) < 100_000


def test_figure_to_html_survives_quoting_titles() -> None:
    import plotly.graph_objects as go

    figure = go.Figure(go.Scatter(x=[1], y=[2]))
    figure.update_layout(title='</script><script>alert("x")</script>')
    html = figure_to_html(figure)
    # the closing tag must not appear unescaped inside the data block
    assert html.count("</script>") == 2  # exactly the two legitimate closers


# --------------------------------------------------------------------------- #
# Data URI decoding
# --------------------------------------------------------------------------- #
def test_decode_data_url_base64() -> None:
    assert decode_data_url("data:image/png;base64,aGVsbG8=") == b"hello"


def test_decode_data_url_percent_encoded() -> None:
    assert decode_data_url("data:image/svg+xml,%3Csvg%3E%3C%2Fsvg%3E") == b"<svg></svg>"


def test_decode_data_url_rejects_garbage() -> None:
    with pytest.raises(ExportError):
        decode_data_url("not-a-data-uri")
