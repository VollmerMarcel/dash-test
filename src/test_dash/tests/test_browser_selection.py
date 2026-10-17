"""Tests for engine selection (:func:`test_dash.browsers.select_renderer`).

Every probe is faked, so these cover the decision table - Chromium, Firefox,
fallbacks and the loud failures - without starting a browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import test_dash.browsers as browsers_module
import test_dash.browsers.chromium as chromium_module
from test_dash.browsers import (
    ChromiumRenderer,
    ChromiumUnavailable,
    FirefoxBiDi,
    probe_chromium,
)


def test_select_renderer_firefox_never_probes_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chromium", _probe)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox"))

    renderer = browsers_module.select_renderer("firefox")

    assert isinstance(renderer, FirefoxBiDi)
    assert renderer.engine == "firefox"
    assert probes == []


def test_select_renderer_auto_uses_chromium_when_the_probe_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No explicit browser: Kaleido picks, and the probe confirms it works."""
    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chromium", _probe)

    renderer = browsers_module.select_renderer("auto")

    assert isinstance(renderer, ChromiumRenderer)
    assert renderer.engine == "chromium"
    assert renderer.executable is None, "Kaleido's own choice is left unpinned"
    assert probes == [None], "no browser was named, so none may be pinned"


def test_select_renderer_pins_a_browser_the_user_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--chromium PATH is pinned for the probe, so Kaleido cannot pick another."""
    named = tmp_path / "msedge.exe"
    named.write_bytes(b"fake binary")
    probes: list[object] = []

    def _probe(browser: object = None) -> bool:
        probes.append(browser)
        return True

    monkeypatch.setattr(browsers_module, "probe_chromium", _probe)

    renderer = browsers_module.select_renderer("chromium", chromium=named)

    assert isinstance(renderer, ChromiumRenderer)
    assert renderer.executable == named
    assert probes == [named], "a named browser must be probed pinned"


def test_select_renderer_auto_falls_back_to_firefox_when_chromium_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browsers_module, "probe_chromium", lambda browser=None: False)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox"))

    renderer = browsers_module.select_renderer("auto")

    assert isinstance(renderer, FirefoxBiDi)
    assert renderer.engine == "firefox"


def test_select_renderer_auto_uses_firefox_without_kaleido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chromium_module, "kaleido_installed", lambda: False)
    monkeypatch.setattr(browsers_module, "find_firefox", lambda explicit=None: Path("firefox"))

    assert probe_chromium() is False, "a missing Kaleido must never render anything"
    assert isinstance(browsers_module.select_renderer("auto"), FirefoxBiDi)


def test_select_renderer_explicit_chromium_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    named = tmp_path / "chromium.exe"
    named.write_bytes(b"fake binary")
    monkeypatch.setattr(browsers_module, "probe_chromium", lambda browser=None: False)

    with pytest.raises(ChromiumUnavailable):
        browsers_module.select_renderer("chromium", chromium=named)


def test_select_renderer_explicit_chromium_reports_a_missing_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browsers_module, "probe_chromium", lambda browser=None: False)

    with pytest.raises(ChromiumUnavailable) as excinfo:
        browsers_module.select_renderer("chromium")
    assert "no Chromium" in str(excinfo.value)


def test_select_renderer_explicit_chromium_reports_missing_kaleido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chromium_module, "kaleido_installed", lambda: False)

    with pytest.raises(ChromiumUnavailable) as excinfo:
        browsers_module.select_renderer("chromium")
    assert "Kaleido" in str(excinfo.value)
