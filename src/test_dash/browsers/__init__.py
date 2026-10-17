"""Static-image browser backends for the offline figure export.

There are two interchangeable backends, chosen by :func:`select_renderer` from
the ``--engine`` value:

* :mod:`test_dash.browsers.chrome` uses plotly's own static-image path
  (:func:`plotly.io.to_image`), which needs the optional Kaleido package *and* an
  installed Chromium browser (Chrome, Chromium, Edge or Brave).  Kaleido is not
  part of the default install; add it with ``uv sync --extra chrome``.
* :mod:`test_dash.browsers.firefox` is a self-contained fallback that needs
  nothing beyond Firefox (see that module for how it works).

``--engine auto`` (the default) uses Chrome when it can prove the backend works
and quietly falls back to Firefox otherwise.  Either way nothing is installed,
no server is started, and no window is ever shown.
"""

from __future__ import annotations

from pathlib import Path

from .base import (
    DEFAULT_FORMAT,
    DEFAULT_HEIGHT,
    DEFAULT_SCALE,
    DEFAULT_WIDTH,
    ENGINES,
    ChromeUnavailable,
    Engine,
    ExportError,
    FirefoxUnavailable,
    Renderer,
    StrPath,
)
from .chrome import (
    ChromeRenderer,
    chrome_failure_hint,
    kaleido_installed,
    probe_chrome,
    requested_browser,
)
from .firefox import (
    FirefoxBiDi,
    decode_data_url,
    figure_to_html,
    find_firefox,
    plotly_js_path,
)


def select_renderer(
    engine: Engine,
    *,
    chrome: StrPath | None = None,
    firefox: StrPath | None = None,
    log_path: Path | None = None,
) -> Renderer:
    """Build the renderer ``engine`` asks for, probing Chrome when needed.

    ``"auto"`` prefers Chrome but falls back to Firefox when the backend cannot
    be proven to work; an explicit ``"chrome"`` raises :class:`ChromeUnavailable`
    rather than quietly exporting through a backend the user did not ask for.

    With ``--chrome``/``TEST_DASH_CHROME`` the browser is named by the user and
    pinned for the probe.  Without either, Kaleido's own browser layer picks one,
    exactly as it would without this exporter, and the probe confirms it works.
    """
    if engine != "firefox":
        browser = requested_browser(chrome)
        if browser is not None:
            if probe_chrome(browser):
                return ChromeRenderer(browser)
        elif probe_chrome():
            return ChromeRenderer()
        if engine == "chrome":
            raise ChromeUnavailable(chrome_failure_hint(browser))
    return FirefoxBiDi(find_firefox(firefox), log_path=log_path)
