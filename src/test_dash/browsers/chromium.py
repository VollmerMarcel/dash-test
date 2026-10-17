"""The Chromium/Kaleido static-image backend.

Uses plotly's own static-image path (:func:`plotly.io.to_image`), which needs the
optional ``kaleido`` package *and* an installed Chromium-based browser (Chrome,
Edge, Brave or any other build of Chromium).  Nothing is ever downloaded: the
browser must already be on the machine and ``kaleido`` must already be installed
(``uv sync --extra chromium``).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .base import (
    DEFAULT_FORMAT,
    DEFAULT_HEIGHT,
    DEFAULT_SCALE,
    DEFAULT_WIDTH,
    ChromiumUnavailable,
    Engine,
    StrPath,
)

if TYPE_CHECKING:
    import plotly.graph_objects as go


def requested_browser(explicit: StrPath | None = None) -> Path | None:
    """The browser ``--chromium PATH`` names, else ``None``.

    A path that does not exist is a loud error rather than a silent fall back to
    autodetection.  ``None`` means "no preference": Kaleido's own browser layer
    then picks any Chromium-based browser it can find, exactly as it would
    without us.

    Nothing is ever downloaded here - ``plotly.io.get_chrome()`` and
    ``kaleido.get_chrome_sync()`` are never called, so the browser must already
    be on the machine (including one the user fetched themselves).
    """
    if explicit is None:
        return None
    candidate = Path(explicit)
    if candidate.is_file():
        return candidate
    raise ChromiumUnavailable(f"--chromium points at a missing file: {candidate}")


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


def probe_chromium(browser: StrPath | None = None) -> bool:
    """Render a 100x100 test figure through the Chromium/Kaleido backend.

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


def chromium_failure_hint(browser: Path | None) -> str:
    """Explain why an explicitly requested Chromium engine is unusable."""
    if not kaleido_installed():
        return (
            "the Chromium engine needs the optional Kaleido backend; install it with "
            "'uv sync --extra chromium' (or 'uv add kaleido')"
        )
    if browser is None:
        return (
            "no Chromium-based browser Kaleido could drive (Chrome, Edge, Brave, ...); "
            "install one, run plotly.io.get_chrome(), or pass --chromium PATH"
        )
    return f"Kaleido could not render with {browser}; pass --chromium PATH to override"


class ChromiumRenderer:
    """Static images through plotly's Kaleido + any Chromium-based browser.

    Used as a context manager so an explicitly chosen browser can be pinned via
    ``BROWSER_PATH`` for the duration of the export and then restored.  Unlike
    :class:`~test_dash.browsers.firefox.FirefoxBiDi` there is no process or
    profile to manage: Kaleido starts and stops its own browser for every image.
    """

    engine: Engine = "chromium"

    def __init__(self, browser: StrPath | None = None) -> None:
        self.executable: Path | None = Path(browser) if browser is not None else None
        self._previous_browser_path: str | None = None

    def __enter__(self) -> ChromiumRenderer:
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
