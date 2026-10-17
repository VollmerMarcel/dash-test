"""Tests for the click commands (:mod:`test_dash` and :mod:`test_dash.export`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


def test_cli_defaults_to_serving_the_app() -> None:
    from test_dash import build_cli

    params = {param.name: param for param in build_cli().params}
    assert params["export_figures"].default is None
    assert params["image_format"].default == "png"
    assert params["scale"].default == pytest.approx(2.0)
    assert params["engine"].default == "auto"
    assert params["chromium"].default is None


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
            "--chromium", r"C:\browsers\chromium.exe",
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
    assert captured["chromium"] == r"C:\browsers\chromium.exe"
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
    assert captured["chromium"] is None
    assert captured["firefox"] is None
