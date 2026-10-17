"""Tests for the Chromium/Kaleido backend (:mod:`test_dash.browsers.chromium`).

Kaleido and a real browser are never started here: ``plotly.io.to_image`` is
replaced, so the renderer's contract is checked without rendering anything.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from test_dash.browsers import ChromiumRenderer, ChromiumUnavailable, requested_browser
from test_dash.export import preset_figures


def _capture_to_image(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace plotly's static-image call, recording how it was made."""
    import plotly.io as pio

    captured: dict[str, Any] = {}

    def fake_to_image(figure: object, **kwargs: object) -> bytes:
        captured.update(kwargs)
        return b"\x89PNG\r\n\x1a\n" + b"0" * 64

    monkeypatch.setattr(pio, "to_image", fake_to_image)
    return captured


# --------------------------------------------------------------------------- #
# Explicit --chromium choice
# --------------------------------------------------------------------------- #
def test_requested_browser_rejects_a_missing_explicit_path() -> None:
    with pytest.raises(ChromiumUnavailable, match=r"C:\\definitely\\not\\here"):
        requested_browser(r"C:\definitely\not\here\chromium.exe")


def test_requested_browser_accepts_an_existing_path(tmp_path: Path) -> None:
    fake = tmp_path / "chromium.exe"
    fake.write_bytes(b"fake binary")
    assert requested_browser(fake) == fake


def test_requested_browser_is_empty_without_a_choice() -> None:
    """No --chromium -> Kaleido picks the browser."""
    assert requested_browser() is None


# --------------------------------------------------------------------------- #
# Chromium renderer
# --------------------------------------------------------------------------- #
def test_chromium_renderer_writes_the_image_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_to_image(monkeypatch)
    destination = tmp_path / "nested" / "figure.png"

    with ChromiumRenderer() as renderer:
        result = renderer.render(
            preset_figures()[0].figure, destination, width=400, height=300, scale=3
        )

    assert result == destination
    assert destination.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert captured["format"] == "png"
    assert captured["width"] == 400
    assert captured["height"] == 300
    assert captured["scale"] == 3


def test_chromium_renderer_omits_scale_for_svg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_to_image(monkeypatch)
    destination = tmp_path / "figure.svg"

    with ChromiumRenderer() as renderer:
        renderer.render(
            preset_figures()[0].figure, destination, image_format="svg", scale=3
        )

    assert captured["format"] == "svg"
    assert "scale" not in captured, "SVG is resolution independent"
    assert destination.is_file()


def test_chromium_renderer_pins_and_restores_browser_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BROWSER_PATH", raising=False)
    browser = tmp_path / "chromium.exe"
    browser.write_bytes(b"fake binary")

    with ChromiumRenderer(browser) as renderer:
        assert os.environ["BROWSER_PATH"] == str(browser)
        assert renderer.executable == browser

    assert "BROWSER_PATH" not in os.environ


def test_chromium_renderer_restores_a_preexisting_browser_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_PATH", r"C:\already\set\brave.exe")

    with ChromiumRenderer(r"C:\given\chromium.exe"):
        assert os.environ["BROWSER_PATH"] == r"C:\given\chromium.exe"

    assert os.environ["BROWSER_PATH"] == r"C:\already\set\brave.exe"


def test_chromium_renderer_without_an_explicit_browser_leaves_the_env_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_PATH", r"C:\already\set\brave.exe")

    with ChromiumRenderer() as renderer:
        assert renderer.executable is None

    assert os.environ["BROWSER_PATH"] == r"C:\already\set\brave.exe"
