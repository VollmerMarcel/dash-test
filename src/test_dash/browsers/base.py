"""Shared types and settings for the static-image browser backends.

The two backends (:mod:`test_dash.browsers.firefox` and
:mod:`test_dash.browsers.chrome`) are interchangeable behind :class:`Renderer`;
this module holds what they have in common so neither has to import the other.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Literal, Protocol, Self

if TYPE_CHECKING:
    import plotly.graph_objects as go

#: Default raster size (CSS pixels); ``--scale`` multiplies this for PNG.
DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 700

DEFAULT_FORMAT = "png"
DEFAULT_SCALE = 2.0

#: How ``--engine`` selects a static-image backend.
Engine = Literal["auto", "chrome", "firefox"]

#: Accepted ``--engine`` values, in help order.
ENGINES: tuple[str, ...] = ("auto", "chrome", "firefox")

#: Anything usable as a filesystem path: a ``str`` or an :class:`os.PathLike`.
StrPath = str | os.PathLike[str]


class FirefoxUnavailable(RuntimeError):
    """Raised when no Firefox binary could be located."""


class ExportError(RuntimeError):
    """Raised when a backend failed to render or return an image."""


class ChromeUnavailable(ExportError):
    """Raised when the Chrome/Kaleido backend was requested but cannot be used."""


class Renderer(Protocol):
    """A context-managed static-image backend.

    The backends are structurally interchangeable: :func:`select_renderer` picks
    one, the export loop in :mod:`test_dash.export` drives it, and neither side
    has to know which implementation it got.
    """

    #: The ``--engine`` value this renderer implements.
    engine: Engine

    @property
    def executable(self) -> Path | None:
        """The browser executable in use, or ``None`` when it is autodetected."""
        ...

    def __enter__(self) -> Self:
        """Start the backend (and, for Firefox, its browser process)."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop the backend and release its temporary resources."""
        ...

    def render(
        self,
        figure: go.Figure,
        destination: Path,
        image_format: str = DEFAULT_FORMAT,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        scale: float = DEFAULT_SCALE,
    ) -> Path:
        """Write ``figure`` to ``destination`` and return the written path."""
        ...
