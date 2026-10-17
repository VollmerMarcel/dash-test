"""The Chrome/Kaleido static-image backend.

Uses plotly's own static-image path (:func:`plotly.io.to_image`), which needs the
optional ``kaleido`` package *and* an installed Chromium-family browser.  Nothing
is ever downloaded: the browser must already be on the machine and ``kaleido``
must already be installed (``uv sync --extra chrome``).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .base import (
    DEFAULT_FORMAT,
    DEFAULT_HEIGHT,
    DEFAULT_SCALE,
    DEFAULT_WIDTH,
    ChromeUnavailable,
    Engine,
    StrPath,
)

if TYPE_CHECKING:
    import plotly.graph_objects as go

#: Chromium-family executables searched on ``PATH``, most preferred first.
_CHROMIUM_NAMES = ("chrome", "chromium", "msedge", "brave")


def find_chromium_browser(explicit: StrPath | None = None) -> Path | None:
    """Locate a Chromium-family browser for the Chrome/Kaleido backend.

    Order: explicit argument, ``TEST_DASH_CHROME`` env var, ``PATH`` (Chrome,
    Chromium, Edge, then Brave), and finally the usual Windows install
    locations.  Returns ``None`` when nothing is installed; an explicit path or
    env override that does not exist raises :class:`ChromeUnavailable` instead,
    so a typo is never silently ignored.
    """
    if explicit is not None:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise ChromeUnavailable(f"no Chrome/Chromium executable at {candidate}")

    env_override = os.environ.get("TEST_DASH_CHROME")
    if env_override:
        candidate = Path(env_override)
        if candidate.is_file():
            return candidate
        raise ChromeUnavailable(f"TEST_DASH_CHROME points at a missing file: {candidate}")

    for name in _CHROMIUM_NAMES:
        on_path = shutil.which(name)
        if on_path:
            return Path(on_path)

    candidates: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if not base:
            continue
        root = Path(base)
        candidates += [
            root / "Google" / "Chrome" / "Application" / "chrome.exe",
            root / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            root / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        root = Path(local_appdata)
        candidates += [
            root / "Google" / "Chrome" / "Application" / "chrome.exe",
            root / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def kaleido_installed() -> bool:
    """Whether the optional Kaleido 1.x backend is importable."""
    if importlib.util.find_spec("kaleido") is None:
        return False
    try:
        version = importlib.metadata.version("kaleido")
    except importlib.metadata.PackageNotFoundError:
        return False
    major = version.split(".", 1)[0]
    return major.isdigit() and int(major) >= 1


def probe_chrome(browser: StrPath | None = None) -> bool:
    """Render a 100x100 test figure through the Chrome/Kaleido backend.

    ``browser`` pins the executable for the probe via ``BROWSER_PATH`` - the
    variable the underlying ``choreographer`` package consults when looking for
    a browser.  Every failure (Kaleido missing, no browser, a browser that
    cannot render) is reported as ``False`` rather than raised, so callers can
    fall back cleanly; the previous environment is always restored.
    """
    if not kaleido_installed():
        return False

    import plotly.graph_objects as go
    import plotly.io as pio

    figure = go.Figure(go.Scatter(x=[0, 1], y=[1, 0]))
    previous = os.environ.get("BROWSER_PATH")
    if browser is not None:
        os.environ["BROWSER_PATH"] = str(browser)
    try:
        pio.to_image(figure, format="png", width=100, height=100)
    except Exception:  # noqa: BLE001 - any failure simply means "not usable"
        return False
    finally:
        if previous is None:
            os.environ.pop("BROWSER_PATH", None)
        else:
            os.environ["BROWSER_PATH"] = previous
    return True


def chrome_failure_hint(browser: Path | None) -> str:
    """Explain why an explicitly requested Chrome engine is unusable."""
    if not kaleido_installed():
        return (
            "the Chrome engine needs the optional Kaleido backend; install it with "
            "'uv sync --extra chrome' (or 'uv add kaleido')"
        )
    if browser is None:
        return (
            "no Chrome, Chromium, Edge or Brave binary found; "
            "pass --chrome PATH or set TEST_DASH_CHROME"
        )
    return f"Kaleido could not render with {browser}; pass --chrome PATH to override"


class ChromeRenderer:
    """Static images through plotly's Kaleido + Chromium backend.

    Used as a context manager so an explicitly chosen browser can be pinned via
    ``BROWSER_PATH`` for the duration of the export and then restored.  Unlike
    :class:`~test_dash.browsers.firefox.FirefoxBiDi` there is no process or
    profile to manage: Kaleido starts and stops its own browser for every image.
    """

    engine: Engine = "chrome"

    def __init__(self, browser: StrPath | None = None) -> None:
        self.executable: Path | None = Path(browser) if browser is not None else None
        self._previous_browser_path: str | None = None

    def __enter__(self) -> ChromeRenderer:
        if self.executable is not None:
            self._previous_browser_path = os.environ.get("BROWSER_PATH")
            os.environ["BROWSER_PATH"] = str(self.executable)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.executable is None:
            return
        if self._previous_browser_path is None:
            os.environ.pop("BROWSER_PATH", None)
        else:
            os.environ["BROWSER_PATH"] = self._previous_browser_path
        self._previous_browser_path = None

    def render(
        self,
        figure: go.Figure,
        destination: Path,
        image_format: str = DEFAULT_FORMAT,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        scale: float = DEFAULT_SCALE,
    ) -> Path:
        """Render ``figure`` to ``destination`` and return the written path."""
        import plotly.io as pio

        options: dict[str, Any] = {}
        if image_format != "svg":
            # SVG is resolution independent, and Kaleido rejects ``scale`` for it.
            options["scale"] = scale
        image = pio.to_image(
            figure, format=image_format, width=width, height=height, **options
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(image)
        return destination
