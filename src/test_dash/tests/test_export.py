"""Tests for the offline figure exporter.

Everything here runs without a browser.  The tests that actually drive Chrome or
Firefox are opt-in via ``TEST_DASH_BROWSER_EXPORT=1`` so the normal suite stays
fast and hermetic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import pytest
from click.testing import CliRunner

from test_dash.browsers import (
    ChromeRenderer,
    ChromeUnavailable,
    Engine,
    ExportError,
    FirefoxBiDi,
    FirefoxUnavailable,
    decode_data_url,
    figure_to_html,
    find_firefox,
    kaleido_installed,
    plotly_js_path,
    probe_chrome,
    requested_browser,
    select_renderer,
)
from test_dash.data import CROPS
from test_dash.export import DEFAULT_HEIGHT, DEFAULT_WIDTH, Preset, preset_figures
from test_dash.figures import mean_yield_chart


@pytest.fixture(scope="module")
def presets() -> list[Preset]:
    return preset_figures()


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
def test_presets_cover_the_gallery(presets: list[Preset]) -> None:
    names = [preset.name for preset in presets]
    assert len(names) == 9
    assert len(set(names)) == len(names), "preset names must be unique file stems"
    assert names[:4] == [
        "01_rainfall_vs_yield",
        "02_temperature_vs_yield",
        "03_soil_ph_vs_yield",
        "04_mean_yield_by_crop",
    ]


def test_presets_include_one_figure_per_crop(presets: list[Preset]) -> None:
    crop_stems = {p.name for p in presets if "_crop_" in p.name}
    assert crop_stems == {f"{i:02d}_crop_{c.lower()}" for i, c in enumerate(CROPS, start=5)}


def test_single_crop_preset_only_has_that_crop(presets: list[Preset]) -> None:
    wheat = next(p for p in presets if p.name == "05_crop_wheat")
    marker_traces = [t for t in wheat.figure.data if t.mode == "markers"]
    assert len(marker_traces) == 1
    assert marker_traces[0].name == "Wheat"


def test_bar_chart_preset_colours_every_crop(presets: list[Preset]) -> None:
    bar = next(p for p in presets if p.name == "04_mean_yield_by_crop")
    colors = list(bar.figure.data[0].marker.color)
    assert len(colors) == len(CROPS)
    assert len(set(colors)) == len(CROPS), "each bar should get its own crop colour"
    assert "#d8d8d8" not in colors


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


# --------------------------------------------------------------------------- #
# Firefox discovery
# --------------------------------------------------------------------------- #
def test_find_firefox_rejects_missing_explicit_path() -> None:
    with pytest.raises(FirefoxUnavailable):
        find_firefox(r"C:\definitely\not\here\firefox.exe")


def test_find_firefox_locates_local_firefox() -> None:
    """Only meaningful on this machine; skipped where Firefox is absent."""
    try:
        found = find_firefox()
    except FirefoxUnavailable:
        pytest.skip("no firefox installed")
    assert found.is_file()


# --------------------------------------------------------------------------- #
# Browser selection
# --------------------------------------------------------------------------- #
def test_requested_browser_rejects_a_missing_explicit_path() -> None:
    with pytest.raises(ChromeUnavailable, match=r"C:\\definitely\\not\\here"):
        requested_browser(r"C:\definitely\not\here\chrome.exe")


def test_requested_browser_rejects_a_missing_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_DASH_CHROME", r"C:\definitely\not\here\chrome.exe")
    with pytest.raises(ChromeUnavailable, match="TEST_DASH_CHROME"):
        requested_browser()


def test_requested_browser_honours_the_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "chrome.exe"
    fake.write_bytes(b"fake binary")
    monkeypatch.setenv("TEST_DASH_CHROME", str(fake))
    assert requested_browser() == fake


def test_requested_browser_prefers_the_explicit_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "explicit.exe"
    explicit.write_bytes(b"fake binary")
    from_env = tmp_path / "from-env.exe"
    from_env.write_bytes(b"fake binary")
    monkeypatch.setenv("TEST_DASH_CHROME", str(from_env))
    assert requested_browser(explicit) == explicit


def test_requested_browser_is_empty_without_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither --chrome nor TEST_DASH_CHROME -> Kaleido picks the browser."""
    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    assert requested_browser() is None


# --------------------------------------------------------------------------- #
# Engine selection
# --------------------------------------------------------------------------- #
def test_select_renderer_firefox_never_probes_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_dash.browsers as browsers_module

    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chrome", _probe)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox.exe"))

    renderer = browsers_module.select_renderer("firefox")

    assert isinstance(renderer, FirefoxBiDi)
    assert renderer.engine == "firefox"
    assert probes == []


def test_select_renderer_auto_uses_chrome_when_the_probe_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No explicit browser: Kaleido picks, and the probe confirms it works."""
    import test_dash.browsers as browsers_module

    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chrome", _probe)

    renderer = browsers_module.select_renderer("auto")

    assert isinstance(renderer, ChromeRenderer)
    assert renderer.engine == "chrome"
    assert renderer.executable is None, "Kaleido's own choice is left unpinned"
    assert probes == [None], "no browser was named, so none may be pinned"


def test_select_renderer_pins_a_browser_the_user_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--chrome PATH is pinned for the probe, so Kaleido cannot pick another."""
    import test_dash.browsers as browsers_module

    named = tmp_path / "msedge.exe"
    named.write_bytes(b"fake binary")
    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chrome", _probe)

    renderer = browsers_module.select_renderer("chrome", chrome=named)

    assert isinstance(renderer, ChromeRenderer)
    assert renderer.executable == named
    assert probes == [named], "a named browser must be probed pinned"


def test_select_renderer_honours_the_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import test_dash.browsers as browsers_module

    named = tmp_path / "chrome.exe"
    named.write_bytes(b"fake binary")
    monkeypatch.setenv("TEST_DASH_CHROME", str(named))
    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chrome", _probe)

    renderer = browsers_module.select_renderer("auto")

    assert isinstance(renderer, ChromeRenderer)
    assert renderer.executable == named
    assert probes == [named]


def test_select_renderer_auto_falls_back_to_firefox_when_chrome_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_dash.browsers as browsers_module

    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    monkeypatch.setattr(browsers_module, "probe_chrome", lambda browser=None: False)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox.exe"))

    renderer = browsers_module.select_renderer("auto")

    assert isinstance(renderer, FirefoxBiDi)
    assert renderer.engine == "firefox"


def test_select_renderer_auto_uses_firefox_without_kaleido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_dash.browsers as browsers_module
    import test_dash.browsers.chrome as chrome_module

    monkeypatch.setattr(chrome_module, "kaleido_installed", lambda: False)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox.exe"))

    assert probe_chrome() is False, "a missing Kaleido must never render anything"
    assert isinstance(browsers_module.select_renderer("auto"), FirefoxBiDi)


def test_select_renderer_explicit_chrome_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import test_dash.browsers as browsers_module

    named = tmp_path / "chrome.exe"
    named.write_bytes(b"fake binary")
    monkeypatch.setattr(browsers_module, "probe_chrome", lambda browser=None: False)

    with pytest.raises(ChromeUnavailable):
        browsers_module.select_renderer("chrome", chrome=named)


def test_select_renderer_explicit_chrome_reports_a_missing_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_dash.browsers as browsers_module

    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    monkeypatch.setattr(browsers_module, "probe_chrome", lambda browser=None: False)

    with pytest.raises(ChromeUnavailable) as excinfo:
        browsers_module.select_renderer("chrome")
    assert "no Chrome" in str(excinfo.value)


def test_select_renderer_explicit_chrome_reports_missing_kaleido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_dash.browsers as browsers_module
    import test_dash.browsers.chrome as chrome_module

    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    monkeypatch.setattr(chrome_module, "kaleido_installed", lambda: False)

    with pytest.raises(ChromeUnavailable) as excinfo:
        browsers_module.select_renderer("chrome")
    assert "Kaleido" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Chrome renderer
# --------------------------------------------------------------------------- #
def _capture_to_image(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace plotly's static-image call, recording how it was made."""
    import plotly.io as pio

    captured: dict[str, Any] = {}

    def fake_to_image(figure: object, **kwargs: object) -> bytes:
        captured.update(kwargs)
        return b"\x89PNG\r\n\x1a\n" + b"0" * 64

    monkeypatch.setattr(pio, "to_image", fake_to_image)
    return captured


def test_chrome_renderer_writes_the_image_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_to_image(monkeypatch)
    destination = tmp_path / "nested" / "figure.png"

    with ChromeRenderer() as renderer:
        result = renderer.render(
            preset_figures()[0].figure, destination, width=400, height=300, scale=3
        )

    assert result == destination
    assert destination.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert captured["format"] == "png"
    assert captured["width"] == 400
    assert captured["height"] == 300
    assert captured["scale"] == 3


def test_chrome_renderer_omits_scale_for_svg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_to_image(monkeypatch)
    destination = tmp_path / "figure.svg"

    with ChromeRenderer() as renderer:
        renderer.render(
            preset_figures()[0].figure, destination, image_format="svg", scale=3
        )

    assert captured["format"] == "svg"
    assert "scale" not in captured, "SVG is resolution independent"
    assert destination.is_file()


def test_chrome_renderer_pins_and_restores_browser_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BROWSER_PATH", raising=False)
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"fake binary")

    with ChromeRenderer(chrome) as renderer:
        assert os.environ["BROWSER_PATH"] == str(chrome)
        assert renderer.executable == chrome

    assert "BROWSER_PATH" not in os.environ


def test_chrome_renderer_restores_a_preexisting_browser_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_PATH", r"C:\already\set\brave.exe")

    with ChromeRenderer(r"C:\given\chrome.exe"):
        assert os.environ["BROWSER_PATH"] == r"C:\given\chrome.exe"

    assert os.environ["BROWSER_PATH"] == r"C:\already\set\brave.exe"


def test_chrome_renderer_without_an_explicit_browser_leaves_the_env_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_PATH", r"C:\already\set\brave.exe")

    with ChromeRenderer() as renderer:
        assert renderer.executable is None

    assert os.environ["BROWSER_PATH"] == r"C:\already\set\brave.exe"


# --------------------------------------------------------------------------- #
# CLI wiring (click)
# --------------------------------------------------------------------------- #
def test_cli_defaults_to_serving_the_app() -> None:
    from test_dash import build_cli

    params = {param.name: param for param in build_cli().params}
    assert params["export_figures"].default is None
    assert params["image_format"].default == "png"
    assert params["scale"].default == pytest.approx(2.0)
    assert params["engine"].default == "auto"
    assert params["chrome"].default is None


def test_cli_accepts_export_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    import test_dash
    import test_dash.export as export_module

    captured: dict[str, object] = {}

    def fake_run_export(output_dir: Path, **kwargs: object) -> int:
        captured["output_dir"] = output_dir
        captured.update(kwargs)
        return 0

    # build_cli() imports run_export lazily, so patching first takes effect.
    monkeypatch.setattr(export_module, "run_export", fake_run_export)

    result = CliRunner().invoke(
        test_dash.build_cli(),
        [
            "--export-figures", "out",
            "--format", "svg",
            "--scale", "3",
            "--width", "900",
            "--engine", "firefox",
            "--chrome", r"C:\browsers\chrome.exe",
            "-v",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == Path("out")
    assert captured["image_format"] == "svg"
    assert captured["scale"] == 3
    assert captured["width"] == 900
    assert captured["height"] is None
    assert captured["engine"] == "firefox"
    assert captured["chrome"] == r"C:\browsers\chrome.exe"
    assert captured["firefox"] is None
    assert captured["verbose"] is True


def test_cli_without_flags_serves_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    import test_dash
    import test_dash.app as app_module

    ran: dict[str, object] = {}

    class FakeApp:
        def run(self, **kwargs: object) -> None:
            ran.update(kwargs)

    monkeypatch.setattr(app_module, "create_app", FakeApp)

    result = CliRunner().invoke(test_dash.build_cli(), [])

    assert result.exit_code == 0, result.output
    assert ran == {"debug": True}


def test_cli_rejects_unknown_format() -> None:
    import test_dash

    result = CliRunner().invoke(
        test_dash.build_cli(), ["--export-figures", "out", "--format", "pdf"]
    )
    assert result.exit_code == 2


def test_cli_rejects_unknown_engine() -> None:
    import test_dash

    result = CliRunner().invoke(
        test_dash.build_cli(), ["--export-figures", "out", "--engine", "bogus"]
    )
    assert result.exit_code == 2


def test_standalone_export_module_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    import test_dash.export as export_module

    captured: dict[str, object] = {}

    def fake_run_export(output_dir: Path, **kwargs: object) -> int:
        captured["output_dir"] = output_dir
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(export_module, "run_export", fake_run_export)

    result = CliRunner().invoke(export_module.cli, ["out", "--format", "svg"])

    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == Path("out")
    assert captured["image_format"] == "svg"
    assert captured["scale"] == pytest.approx(2.0)
    assert captured["engine"] == "auto"
    assert captured["chrome"] is None
    assert captured["firefox"] is None


# --------------------------------------------------------------------------- #
# Export loop + tqdm progress
# --------------------------------------------------------------------------- #
class _FakeBrowser:
    """Stands in for :class:`FirefoxBiDi` so the loop needs no real browser."""

    engine: Engine = "firefox"

    def __init__(self, *args: object, **kwargs: object) -> None:
        # FirefoxBiDi is handed the executable positionally; mirror it so the
        # verbose configuration report has something to print.
        self.firefox = Path(str(args[0])) if args else Path("firefox.exe")

    @property
    def executable(self) -> Path:
        return self.firefox

    def __enter__(self) -> _FakeBrowser:
        return self

    def __exit__(self, *exc_info: object) -> Literal[False]:
        return False

    def render(self, figure: object, destination: Path, **kwargs: object) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 1024)
        return path


class _FakeBar:
    """Records what the tqdm bar was told, without writing to a terminal."""

    def __init__(self, iterable: Iterable[object], **kwargs: object) -> None:
        self._items = list(iterable)
        self.init_kwargs = kwargs
        self.descriptions: list[str] = []

    def __enter__(self) -> _FakeBar:
        return self

    def __exit__(self, *exc_info: object) -> Literal[False]:
        return False

    def __iter__(self) -> Iterator[object]:
        return iter(self._items)

    def set_description_str(self, text: str, refresh: bool = True) -> None:
        self.descriptions.append(text)

    def write(self, *args: object, **kwargs: object) -> None:
        pass


#: The patched export module plus the progress bars it was handed.
FakeExport = tuple[ModuleType, list[_FakeBar]]


@pytest.fixture
def fake_export(monkeypatch: pytest.MonkeyPatch) -> FakeExport:
    """Patch the browser and the progress bar out of the export machinery."""
    import test_dash.browsers as browsers_module
    import test_dash.export as export_module

    bars: list[_FakeBar] = []

    def make_bar(iterable: Iterable[object], **kwargs: object) -> _FakeBar:
        bar = _FakeBar(iterable, **kwargs)
        bars.append(bar)
        return bar

    # With no browser named by the user and a probe that never passes, ``auto``
    # resolves to the Firefox engine without ever touching Kaleido - so the
    # suite stays hermetic.
    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox.exe"))
    monkeypatch.setattr(browsers_module, "requested_browser", lambda explicit=None: None)
    monkeypatch.setattr(browsers_module, "probe_chrome", lambda browser=None: False)
    monkeypatch.setattr(browsers_module, "FirefoxBiDi", _FakeBrowser)
    monkeypatch.setattr(export_module, "tqdm", make_bar)
    return export_module, bars


def test_export_all_writes_every_preset(tmp_path: Path, fake_export: FakeExport) -> None:
    export_module, _ = fake_export

    written = export_module.export_all(tmp_path / "out")

    assert len(written) == 9
    assert all(path.is_file() for path in written)
    assert {path.suffix for path in written} == {".png"}
    assert not (tmp_path / "out" / "firefox.log").exists()


def test_export_all_names_each_file_as_it_is_written(
    tmp_path: Path, fake_export: FakeExport
) -> None:
    export_module, bars = fake_export

    export_module.export_all(tmp_path / "out")

    descriptions = bars[0].descriptions
    assert descriptions[0] == "writing 01_rainfall_vs_yield.png"
    assert any("05_crop_wheat" in text for text in descriptions)
    assert descriptions[-1] == "writing 09_crop_soy.png"
    assert len(descriptions) == 9


def test_export_all_progress_can_be_disabled(
    tmp_path: Path, fake_export: FakeExport
) -> None:
    export_module, bars = fake_export

    export_module.export_all(tmp_path / "out", progress=False)

    assert bars[0].init_kwargs["disable"] is True


def test_export_all_progress_auto_detects_a_terminal(
    tmp_path: Path, fake_export: FakeExport
) -> None:
    export_module, bars = fake_export

    export_module.export_all(tmp_path / "out")

    # tqdm's disable=None means "auto": off when stderr is not a terminal.
    assert bars[0].init_kwargs["disable"] is None


def test_export_all_forwards_the_engine_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import test_dash.export as export_module

    seen: dict[str, object] = {}
    bars: list[_FakeBar] = []

    def fake_select(
        engine: str,
        *,
        chrome: str | None = None,
        firefox: str | None = None,
        log_path: Path | None = None,
    ) -> _FakeBrowser:
        seen.update(engine=engine, chrome=chrome, firefox=firefox)
        return _FakeBrowser()

    def make_bar(iterable: Iterable[object], **kwargs: object) -> _FakeBar:
        bar = _FakeBar(iterable, **kwargs)
        bars.append(bar)
        return bar

    monkeypatch.setattr(export_module, "select_renderer", fake_select)
    monkeypatch.setattr(export_module, "tqdm", make_bar)

    written = export_module.export_all(
        tmp_path / "out", engine="firefox", chrome="chrome.exe", verbose=True
    )

    assert seen == {"engine": "firefox", "chrome": "chrome.exe", "firefox": None}
    assert len(written) == 9
    assert len(bars) == 1

    out = capsys.readouterr().out
    assert "engine:   firefox" in out
    assert "browser:  firefox.exe" in out
    assert "output:" in out


def test_export_all_verbose_reports_the_chrome_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import plotly.io as pio
    import test_dash.browsers as browsers_module
    import test_dash.export as export_module

    # Hedge the whole Chrome path so it is exercised without a real browser.
    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    monkeypatch.setattr(browsers_module, "requested_browser", lambda explicit=None: None)
    monkeypatch.setattr(browsers_module, "probe_chrome", lambda browser=None: True)
    monkeypatch.setattr(pio, "to_image", lambda figure, **kwargs: b"\x89PNG\r\n\x1a\n")

    written = export_module.export_all(tmp_path / "out", engine="chrome", verbose=True)

    assert len(written) == 9
    assert all(path.read_bytes()[:4] == b"\x89PNG" for path in written)

    out = capsys.readouterr().out
    assert "engine:   chrome" in out
    assert "browser:  autodetected" in out


def test_export_all_leaves_no_directory_when_the_engine_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_dash.browsers as browsers_module
    import test_dash.export as export_module

    monkeypatch.delenv("TEST_DASH_CHROME", raising=False)
    monkeypatch.setattr(browsers_module, "requested_browser", lambda explicit=None: None)
    monkeypatch.setattr(browsers_module, "probe_chrome", lambda browser=None: False)

    output = tmp_path / "out"
    with pytest.raises(ChromeUnavailable):
        export_module.export_all(output, engine="chrome")

    assert not output.exists(), "a failed export should not create the output dir"


def test_run_export_reports_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import test_dash.export as export_module

    image = tmp_path / "a.png"
    image.write_bytes(b"x" * 2048)
    monkeypatch.setattr(export_module, "export_all", lambda *a, **k: [image])

    assert export_module.run_export(tmp_path) == 0


def test_run_export_reports_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import test_dash.export as export_module

    def boom(*args: object, **kwargs: object) -> list[Path]:
        raise export_module.ExportError("firefox vanished")

    monkeypatch.setattr(export_module, "export_all", boom)

    assert export_module.run_export("out") == 1
    assert "export failed: firefox vanished" in capsys.readouterr().err


def test_run_export_reports_an_unavailable_engine(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import test_dash.export as export_module

    def boom(*args: object, **kwargs: object) -> list[Path]:
        raise ChromeUnavailable("no Chromium browser found")

    monkeypatch.setattr(export_module, "export_all", boom)

    assert export_module.run_export("out", engine="chrome") == 1
    assert "export failed: no Chromium browser found" in capsys.readouterr().err


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


def test_export_defaults_are_sane() -> None:
    assert DEFAULT_WIDTH > 0 and DEFAULT_HEIGHT > 0


def test_mean_yield_chart_highlight_only_colours_one_bar() -> None:
    colors = list(mean_yield_chart(highlight="Wheat").data[0].marker.color)
    assert colors.count("#d8d8d8") == len(CROPS) - 1


# --------------------------------------------------------------------------- #
# Opt-in browser test
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("TEST_DASH_BROWSER_EXPORT") != "1",
    reason="set TEST_DASH_BROWSER_EXPORT=1 to render through headless Firefox",
)
def test_render_png_through_firefox(tmp_path: Path) -> None:
    preset = preset_figures()[0]
    destination = tmp_path / "figure.png"
    with FirefoxBiDi(find_firefox()) as browser:
        browser.render(preset.figure, destination, width=400, height=300, scale=1)

    data = destination.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 5_000


@pytest.mark.skipif(
    os.environ.get("TEST_DASH_BROWSER_EXPORT") != "1",
    reason="set TEST_DASH_BROWSER_EXPORT=1 to render through Kaleido/Chrome",
)
def test_render_png_through_chrome(tmp_path: Path) -> None:
    if not kaleido_installed():
        pytest.skip("kaleido not installed (uv sync --extra chrome)")

    preset = preset_figures()[0]
    destination = tmp_path / "figure.png"
    with ChromeRenderer(requested_browser()) as renderer:
        renderer.render(preset.figure, destination, width=400, height=300, scale=1)

    data = destination.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 5_000


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


@pytest.mark.skipif(
    os.environ.get("TEST_DASH_BROWSER_EXPORT") != "1",
    reason="set TEST_DASH_BROWSER_EXPORT=1 to render through headless Firefox",
)
def test_title_keeps_clear_of_the_legend_and_plot_area(tmp_path: Path) -> None:
    """Regression: the top legend used to be drawn straight through the title."""
    import json

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
