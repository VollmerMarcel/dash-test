"""Tests for the offline figure exporter (:mod:`test_dash.export`).

The preset gallery, the ``export_all`` loop and ``run_export``'s reporting live
here; the click commands that wrap them are in ``test_cli.py``.  Nothing here
drives a real browser - the fakes below stand in for one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Literal

import pytest

from test_dash.browsers import ChromiumUnavailable, Engine
from test_dash.data import CROPS
from test_dash.export import DEFAULT_HEIGHT, DEFAULT_WIDTH, Preset

# --------------------------------------------------------------------------- #
# Fakes: a browser and a progress bar, so the loop runs hermetically
# --------------------------------------------------------------------------- #
class _FakeBrowser:
    """Stands in for :class:`FirefoxBiDi` so the loop needs no real browser."""

    engine: Engine = "firefox"

    def __init__(self, *args: object, **kwargs: object) -> None:
        # FirefoxBiDi is handed the executable positionally; mirror it so the
        # verbose configuration report has something to print.
        self.firefox = Path(str(args[0])) if args else Path("firefox")

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


def _make_bar(bars: list[_FakeBar]) -> Callable[..., _FakeBar]:
    """A tqdm stand-in factory that records every bar it builds."""

    def make_bar(iterable: Iterable[object], **kwargs: object) -> _FakeBar:
        bar = _FakeBar(iterable, **kwargs)
        bars.append(bar)
        return bar

    return make_bar


#: The patched export module plus the progress bars it was handed.
FakeExport = tuple[ModuleType, list[_FakeBar]]


@pytest.fixture
def fake_export(monkeypatch: pytest.MonkeyPatch) -> FakeExport:
    """Patch the browser and the progress bar out of the export machinery."""
    import test_dash.browsers as browsers_module
    import test_dash.export as export_module

    bars: list[_FakeBar] = []

    # With no --chromium and a probe that never passes, ``auto`` resolves to the
    # Firefox engine without ever touching Kaleido - so the suite stays hermetic.
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox"))
    monkeypatch.setattr(browsers_module, "probe_chromium", lambda browser=None: False)
    monkeypatch.setattr(browsers_module, "FirefoxBiDi", _FakeBrowser)
    monkeypatch.setattr(export_module, "tqdm", _make_bar(bars))
    return export_module, bars


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
# Export loop + tqdm progress
# --------------------------------------------------------------------------- #
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
        chromium: str | None = None,
        firefox: str | None = None,
        log_path: Path | None = None,
    ) -> _FakeBrowser:
        seen.update(engine=engine, chromium=chromium, firefox=firefox)
        return _FakeBrowser()

    monkeypatch.setattr(export_module, "select_renderer", fake_select)
    monkeypatch.setattr(export_module, "tqdm", _make_bar(bars))

    written = export_module.export_all(
        tmp_path / "out", engine="firefox", chromium="chromium.exe", verbose=True
    )

    assert seen == {"engine": "firefox", "chromium": "chromium.exe", "firefox": None}
    assert len(written) == 9
    assert len(bars) == 1

    out = capsys.readouterr().out
    assert "engine:   firefox" in out
    assert "browser:  firefox" in out
    assert "output:" in out


def test_export_all_verbose_reports_the_chromium_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import plotly.io as pio
    import test_dash.browsers as browsers_module
    import test_dash.export as export_module

    # Hedge the whole Chromium path so it is exercised without a real browser.
    monkeypatch.setattr(browsers_module, "probe_chromium", lambda browser=None: True)
    monkeypatch.setattr(pio, "to_image", lambda figure, **kwargs: b"\x89PNG\r\n\x1a\n")

    written = export_module.export_all(tmp_path / "out", engine="chromium", verbose=True)

    assert len(written) == 9
    assert all(path.read_bytes()[:4] == b"\x89PNG" for path in written)

    out = capsys.readouterr().out
    assert "engine:   chromium" in out
    assert "browser:  autodetected" in out


def test_export_all_leaves_no_directory_when_the_engine_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_dash.browsers as browsers_module
    import test_dash.export as export_module

    monkeypatch.setattr(browsers_module, "probe_chromium", lambda browser=None: False)

    output = tmp_path / "out"
    with pytest.raises(ChromiumUnavailable):
        export_module.export_all(output, engine="chromium")

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
        raise ChromiumUnavailable("no Chromium browser found")

    monkeypatch.setattr(export_module, "export_all", boom)

    assert export_module.run_export("out", engine="chromium") == 1
    assert "export failed: no Chromium browser found" in capsys.readouterr().err


def test_export_defaults_are_sane() -> None:
    assert DEFAULT_WIDTH > 0 and DEFAULT_HEIGHT > 0
