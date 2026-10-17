"""Offline figure export through whichever browser is available.

There are two backends behind ``--engine``:

* ``chrome`` uses plotly's own static-image path (:func:`plotly.io.to_image`),
  which needs the optional Kaleido package *and* an installed Chromium browser
  (Chrome, Chromium, Edge or Brave).  Kaleido is not part of the default install;
  add it with ``uv sync --extra chrome``.
* ``firefox`` is a self-contained fallback that needs nothing beyond Firefox:

  1. each figure is serialised and dropped into a self-contained HTML page that
     inlines ``plotly.min.js`` from the installed plotly package (no CDN, no
     network);
  2. Firefox is started **headless** with a throwaway profile, which makes it
     expose its built-in WebDriver BiDi endpoint (Firefox 147 has no CDP support
     left and no geckodriver is needed);
  3. the page is asked for ``Plotly.toImage(...)``, which renders with the very
     same plotly.js that draws the web app, and the returned data URI is written
     straight to disk.

``--engine auto`` (the default) uses Chrome when it can prove the backend works
and quietly falls back to Firefox otherwise.  Either way nothing is installed,
no server is started, and no window is ever shown.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.metadata
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Literal, TypeVar

import click
import plotly
import plotly.graph_objects as go
import websockets
from tqdm import tqdm
from websockets.asyncio.client import ClientConnection

from .data import CROPS
from .figures import build_figure, mean_yield_chart

#: Default raster size (CSS pixels); ``--scale`` multiplies this for PNG.
DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 700

DEFAULT_FORMAT = "png"
DEFAULT_SCALE = 2.0

#: How ``--engine`` selects a static-image backend.
Engine = Literal["auto", "chrome", "firefox"]

#: Accepted ``--engine`` values, in help order.
ENGINES: tuple[str, ...] = ("auto", "chrome", "firefox")

#: Chromium-family executables searched on ``PATH``, most preferred first.
_CHROMIUM_NAMES = ("chrome", "chromium", "msedge", "brave")

#: Anything usable as a filesystem path: a ``str`` or an :class:`os.PathLike`.
StrPath = str | os.PathLike[str]

#: One JSON object exchanged with the BiDi endpoint (command parameters/results).
JsonObject = dict[str, Any]

#: A callable being wrapped by a click option/argument decorator.
_DecoratorT = TypeVar("_DecoratorT", bound=Callable[..., Any])

# Prefs written into the throwaway profile *before* Firefox starts.  They keep
# export runs invisible (no update prompt, no first-run wizard, no default
# browser check) and deterministic (no telemetry / update downloads).
_PROFILE_PREFS = """\
user_pref("app.update.enabled", false);
user_pref("app.update.auto", false);
user_pref("app.update.service.enabled", false);
user_pref("app.update.url.install", "");
user_pref("extensions.update.enabled", false);
user_pref("extensions.systemAddon.update.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_override_url", "");
user_pref("browser.startup.homepage", "about:blank");
user_pref("browser.newtabpage.enabled", false);
user_pref("browser.startup.page", 0);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("extensions.getAddons.cache.enabled", false);
user_pref("browser.uitour.enabled", false);
user_pref("app.normandy.enabled", false);
user_pref("network.captive-portal-service.enabled", false);
"""

_SCATTER_DEFAULTS: dict[str, Any] = {
    "color_by": "crop",
    "marker_size": 11,
    "opacity": 0.75,
    "crops": list(CROPS),
    "show_trendline": True,
}


class FirefoxUnavailable(RuntimeError):
    """Raised when no Firefox binary could be located."""


class ExportError(RuntimeError):
    """Raised when Firefox failed to render or return an image."""


class ChromeUnavailable(ExportError):
    """Raised when the Chrome/Kaleido backend was requested but cannot be used."""


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Preset:
    """One named figure to export."""

    name: str
    figure: go.Figure
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT


def preset_figures() -> list[Preset]:
    """The gallery written by ``--export-figures``."""

    def scatter(x_col: str, y_col: str, **overrides: Any) -> go.Figure:
        return build_figure(x_col=x_col, y_col=y_col, **{**_SCATTER_DEFAULTS, **overrides})

    presets = [
        Preset("01_rainfall_vs_yield", scatter("rainfall_mm", "yield_t_per_ha")),
        Preset("02_temperature_vs_yield", scatter("temperature_c", "yield_t_per_ha")),
        Preset("03_soil_ph_vs_yield", scatter("soil_ph", "yield_t_per_ha")),
        Preset(
            "04_mean_yield_by_crop",
            mean_yield_chart(color_by_crop=True),
            width=DEFAULT_WIDTH,
            height=460,
        ),
    ]
    presets += [
        Preset(
            f"{index:02d}_crop_{crop.lower()}",
            scatter("rainfall_mm", "yield_t_per_ha", crops=[crop]),
        )
        for index, crop in enumerate(CROPS, start=5)
    ]
    return presets


# --------------------------------------------------------------------------- #
# Firefox discovery
# --------------------------------------------------------------------------- #
def find_firefox(explicit: StrPath | None = None) -> Path:
    """Locate ``firefox.exe`` without installing anything.

    Order: explicit argument, ``TEST_DASH_FIREFOX`` env var, ``PATH``, then the
    usual Windows install locations.
    """
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise FirefoxUnavailable(f"no firefox executable at {candidate}")

    env_override = os.environ.get("TEST_DASH_FIREFOX")
    if env_override:
        candidate = Path(env_override)
        if candidate.is_file():
            return candidate
        raise FirefoxUnavailable(f"TEST_DASH_FIREFOX points at a missing file: {candidate}")

    on_path = shutil.which("firefox")
    if on_path:
        return Path(on_path)

    candidates: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Mozilla Firefox" / "firefox.exe")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Mozilla Firefox" / "firefox.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FirefoxUnavailable(
        "could not find firefox.exe; pass --firefox PATH or set TEST_DASH_FIREFOX"
    )


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


# --------------------------------------------------------------------------- #
# HTML page
# --------------------------------------------------------------------------- #
def plotly_js_path() -> Path:
    """Path to the plotly.js bundle shipped inside the plotly package."""
    path = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    if not path.is_file():
        raise ExportError(f"plotly.js bundle not found at {path}")
    return path


def figure_to_html(
    figure: go.Figure,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    include_plotlyjs: bool = True,
) -> str:
    """Return a self-contained HTML page rendering ``figure``.

    The figure JSON is fetched through a promise stored on ``window.__plotReady``
    so the renderer can wait for it before asking for an image.
    """
    spec = json.loads(figure.to_json())
    layout = spec.setdefault("layout", {})
    layout["width"] = width
    layout["height"] = height
    layout["autosize"] = False

    # ``\u003c`` escaping keeps a stray "</script>" inside the JSON harmless.
    spec_json = json.dumps(spec).replace("<", "\\u003c")

    if include_plotlyjs:
        script = plotly_js_path().read_text(encoding="utf-8").replace("</script", "<\\/script")
        library = f"<script>{script}</script>"
    else:
        library = ""

    title_spec = layout.get("title")
    if isinstance(title_spec, dict):
        title = str(title_spec.get("text") or "figure")
    else:
        title = str(title_spec or "figure")

    return (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="utf-8">'
        f"<title>{html_escape(title)}</title>\n"
        "<style>html,body{margin:0;padding:0;background:#fff;}"
        f"#fig{{width:{width}px;height:{height}px;}}</style>\n"
        "</head><body>\n"
        '<div id="fig"></div>\n'
        f"{library}\n"
        "<script>\n"
        f"const spec = {spec_json};\n"
        "window.__plotReady = Plotly.newPlot('fig', spec.data, spec.layout,\n"
        "  {staticPlot: false, displayModeBar: false, responsive: false});\n"
        "</script>\n"
        "</body></html>\n"
    )


def decode_data_url(data_url: str) -> bytes:
    """Decode a ``data:`` URI as returned by ``Plotly.toImage``.

    PNG/JPEG come back base64 encoded, while SVG is usually percent-encoded,
    so both shapes are handled.
    """
    header, _, payload = data_url.partition(",")
    if not header.startswith("data:"):
        raise ExportError(f"unexpected image payload: {data_url[:60]!r}")
    if ";base64" in header:
        return base64.b64decode(payload)
    return urllib.parse.unquote_to_bytes(payload)


# --------------------------------------------------------------------------- #
# BiDi transport
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int, timeout: float = 0.5) -> bool:
    with socket.socket() as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, timeout: float, process: subprocess.Popen[bytes]) -> None:
    """Wait until the BiDi endpoint answers.

    On Windows Firefox may **relaunch itself** (e.g. to apply an update), which
    makes the process we spawned exit long before the browser is ready.  An early
    exit is therefore *not* fatal - only a missing port is.
    """
    deadline = time.monotonic() + timeout
    exited: int | None = None
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        code = process.poll()
        if code is not None and exited is None:
            exited = code
        time.sleep(0.2)
    detail = f" (spawned process exited with code {exited})" if exited is not None else ""
    raise ExportError(
        f"firefox remote agent did not open port {port} within {timeout:.0f}s{detail}"
    )


def _pid_owning_port(port: int) -> int | None:
    """Best-effort lookup of the process listening on ``port``."""
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except Exception:  # noqa: BLE001
            return None
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}"):
                if parts[3].upper() == "LISTENING" and parts[4].isdigit():
                    return int(parts[4])
        return None

    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    for token in out.stdout.split():
        if token.strip().isdigit():
            return int(token)
    return None


def _kill_process_tree(pid: int) -> None:
    """Forcefully stop ``pid`` and its children (Windows) / the process itself."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        pass


class FirefoxBiDi:
    """Headless Firefox driven through its built-in WebDriver BiDi socket.

    Used as a context manager; the browser process and its throwaway profile are
    cleaned up on exit.  No geckodriver and no Chrome are involved.
    """

    def __init__(
        self,
        firefox: Path,
        timeout: float = 60.0,
        log_path: Path | None = None,
    ) -> None:
        self.firefox = firefox
        self.timeout = timeout
        self._log_path = log_path
        self._log_handle: IO[bytes] | None = None
        self._profile: Path | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: ClientConnection | None = None
        self._message_id = 0
        self._port: int | None = None
        self._port_pid: int | None = None
        self.context_id: str | None = None

    def _write_profile_prefs(self) -> None:
        """Disable updates, telemetry and first-run prompts in the temp profile."""
        assert self._profile is not None
        (self._profile / "user.js").write_text(_PROFILE_PREFS, encoding="utf-8")

    # -- lifecycle -------------------------------------------------------- #
    def __enter__(self) -> FirefoxBiDi:
        self._profile = Path(tempfile.mkdtemp(prefix="test-dash-export-"))
        self._write_profile_prefs()
        self._port = port = _free_port()

        if self._log_path is not None:
            self._log_handle = open(self._log_path, "wb")
            stderr: int | IO[bytes] = self._log_handle
        else:
            stderr = subprocess.DEVNULL

        self._process = subprocess.Popen(
            [
                str(self.firefox),
                "-headless",
                "-no-remote",
                "--profile",
                str(self._profile),
                "--remote-debugging-port",
                str(port),
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )

        try:
            _wait_for_port(port, self.timeout, self._process)
            # Firefox may have relaunched itself, so remember who serves the port.
            self._port_pid = _pid_owning_port(port)
            self._loop = asyncio.new_event_loop()

            async def _connect() -> ClientConnection:
                return await websockets.connect(
                    f"ws://127.0.0.1:{port}/session", open_timeout=self.timeout
                )

            self._ws = self._loop.run_until_complete(_connect())
            result = self.call("session.new", {"capabilities": {}})
            if not result.get("sessionId"):
                raise ExportError(f"session.new returned no sessionId: {result}")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # 1. graceful: ask the browser to shut itself down.
        if self._ws is not None and self._loop is not None:
            try:
                self._send("browser.close", {})
            except Exception:  # noqa: BLE001 - teardown is best effort
                pass
            try:
                self._loop.run_until_complete(self._ws.close())
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        if self._loop is not None:
            self._loop.close()
            self._loop = None

        # 2. reap the handle we spawned (it may already be gone after a relaunch).
        if self._process is not None:
            if self._process.poll() is None:
                try:
                    self._process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None

        # 3. Firefox may have relaunched itself; if the agent still owns the
        #    port, kill the process tree that serves it.
        if self._port is not None and _port_open(self._port):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _port_open(self._port):
                time.sleep(0.2)
            if _port_open(self._port):
                pid = self._port_pid or _pid_owning_port(self._port)
                if pid is not None:
                    _kill_process_tree(pid)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and _port_open(self._port):
                    time.sleep(0.2)
        self._port = None
        self._port_pid = None

        # 4. close the log and drop the throwaway profile.
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

        if self._profile is not None:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None

    # -- protocol --------------------------------------------------------- #
    def _send(self, method: str, params: JsonObject) -> int:
        assert self._ws is not None and self._loop is not None
        self._message_id += 1
        payload: JsonObject = {"id": self._message_id, "method": method, "params": params}
        self._loop.run_until_complete(self._ws.send(json.dumps(payload)))
        return self._message_id

    def call(self, method: str, params: JsonObject) -> JsonObject:
        """Send one BiDi command and return its result, skipping async events."""
        assert self._ws is not None and self._loop is not None
        message_id = self._send(method, params)
        while True:
            raw = self._loop.run_until_complete(
                asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
            )
            message = json.loads(raw)
            if message.get("id") != message_id:
                continue  # an event (contextCreated/log.entryAdded/...) - ignore
            if message.get("type") == "error":
                raise ExportError(f"{method} failed: {message.get('error')}")
            return dict(message.get("result") or {})

    def open_context(self) -> str:
        """Create a fresh browsing context (tab) and remember its id."""
        result = self.call("browsingContext.create", {"type": "tab"})
        self.context_id = str(result["context"])
        return self.context_id

    def navigate(self, url: str, context: str | None = None) -> None:
        self.call(
            "browsingContext.navigate",
            {"context": context or self.context_id, "url": url, "wait": "complete"},
        )

    def evaluate(self, expression: str, context: str | None = None) -> Any:
        """Evaluate a JS expression (awaited) and unwrap its value."""
        result = self.call(
            "script.evaluate",
            {
                "expression": expression,
                "target": {"context": context or self.context_id},
                "awaitPromise": True,
                "resultOwnership": "root",
            },
        )
        if result.get("type") == "exception":
            raise ExportError(f"javascript exception: {result.get('exception')}")
        return result.get("result", {}).get("value")

    # -- rendering -------------------------------------------------------- #
    def render(
        self,
        figure: go.Figure,
        destination: Path,
        image_format: str = DEFAULT_FORMAT,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        scale: float = DEFAULT_SCALE,
        keep_html: bool = False,
    ) -> Path:
        """Render ``figure`` to ``destination`` and return the written path."""
        assert self._profile is not None
        html_path = self._profile / "figure.html"
        html_path.write_text(
            figure_to_html(figure, width=width, height=height), encoding="utf-8"
        )

        context = self.open_context()
        try:
            self.navigate(html_path.as_uri(), context)

            expression = (
                "(async () => {"
                "  await window.__plotReady;"
                f"  return await Plotly.toImage(document.getElementById('fig'),"
                f" {{format: '{image_format}', width: {width}, height: {height},"
                f" scale: {scale}}});"
                "})()"
            )
            data_url = self.evaluate(expression, context)
            if not isinstance(data_url, str):
                raise ExportError(f"expected a data URI, got {type(data_url).__name__}")
        finally:
            try:
                self.call("browsingContext.close", {"context": context})
            except Exception:  # noqa: BLE001 - a leaked tab must not fail the export
                pass

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(decode_data_url(data_url))

        if not keep_html:
            html_path.unlink(missing_ok=True)
        return destination


# --------------------------------------------------------------------------- #
# Chrome engine (plotly's own static-image backend)
# --------------------------------------------------------------------------- #
class ChromeRenderer:
    """Static images through plotly's Kaleido + Chromium backend.

    Used as a context manager so an explicitly chosen browser can be pinned via
    ``BROWSER_PATH`` for the duration of the export and then restored.  Unlike
    :class:`FirefoxBiDi` there is no process or profile to manage: Kaleido
    starts and stops its own browser for every image.
    """

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


#: The two renderer implementations :func:`export_all` can drive.
Renderer = ChromeRenderer | FirefoxBiDi


def engine_name(renderer: Renderer) -> Engine:
    """The ``--engine`` value a renderer corresponds to."""
    return "chrome" if isinstance(renderer, ChromeRenderer) else "firefox"


def _chrome_failure_hint(browser: Path | None) -> str:
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

    A discovered browser is always pinned for the probe, so an explicit
    ``--chrome`` choice can never be overruled by the backend's own
    autodetection; only when nothing was discovered is the backend trusted to
    find a browser itself.
    """
    if engine != "firefox":
        browser = find_chromium_browser(chrome)
        if browser is not None and probe_chrome(browser):
            return ChromeRenderer(browser)
        if browser is None and probe_chrome():
            return ChromeRenderer()
        if engine == "chrome":
            raise ChromeUnavailable(_chrome_failure_hint(browser))
    return FirefoxBiDi(find_firefox(firefox), log_path=log_path)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def export_all(
    output_dir: StrPath,
    image_format: str = DEFAULT_FORMAT,
    scale: float = DEFAULT_SCALE,
    width: int | None = None,
    height: int | None = None,
    engine: Engine = "auto",
    chrome: StrPath | None = None,
    firefox: StrPath | None = None,
    presets: Sequence[Preset] | None = None,
    verbose: bool = False,
    progress: bool = True,
) -> list[Path]:
    """Export every preset into ``output_dir`` through one renderer.

    ``width``/``height`` override every preset when given; otherwise each preset
    keeps its own size.  ``engine`` picks the backend (see :func:`select_renderer`).
    A :mod:`tqdm` bar names the figure currently being written and steps once per
    file; it disables itself automatically when stderr is not a terminal (pipes,
    CI, tests), so logs stay clean.
    """
    directory = Path(output_dir)
    figures = list(presets) if presets is not None else preset_figures()
    log_path = directory / "firefox.log"

    # Resolve the backend first: a missing browser or an unusable engine should
    # not leave an empty output directory behind.
    renderer = select_renderer(engine, chrome=chrome, firefox=firefox, log_path=log_path)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if verbose:
        print(f"engine:   {engine_name(renderer)}")
        if isinstance(renderer, ChromeRenderer):
            print(f"browser:  {renderer.executable or 'autodetected'}")
        else:
            print(f"firefox:  {renderer.firefox}")
        print(f"output:   {directory.resolve()}")
        print(f"format:   {image_format} (scale {scale:g})")
        print(f"figures:  {len(figures)}")

    bar = tqdm(
        figures,
        desc="exporting figures",
        unit="fig",
        disable=None if progress else True,
    )
    with renderer as browser, bar:
        for preset in bar:
            destination = directory / f"{preset.name}.{image_format}"
            bar.set_description_str(f"writing {destination.name}")
            browser.render(
                preset.figure,
                destination,
                image_format=image_format,
                width=width if width is not None else preset.width,
                height=height if height is not None else preset.height,
                scale=scale,
            )
            written.append(destination)
            if verbose:
                size_kib = destination.stat().st_size / 1024
                bar.write(f"  wrote {destination.name} ({size_kib:.0f} KiB)")

    log_path.unlink(missing_ok=True)
    return written


# --------------------------------------------------------------------------- #
# Command line interface
# --------------------------------------------------------------------------- #
def export_options(func: _DecoratorT) -> _DecoratorT:
    """Attach the export flags shared by both click commands.

    ``click`` collects options in reverse order of application, so the
    decorators are applied back-to-front to keep the ``--help`` listing in the
    order written below.
    """
    decorators = [
        click.option(
            "--format",
            "image_format",
            type=click.Choice(["png", "svg"]),
            default=DEFAULT_FORMAT,
            show_default=True,
            help="output format",
        ),
        click.option(
            "--scale",
            type=click.FloatRange(min=0.1),
            default=DEFAULT_SCALE,
            show_default=True,
            help="PNG resolution multiplier (ignored for SVG)",
        ),
        click.option(
            "--width",
            type=click.IntRange(min=1),
            default=None,
            metavar="PX",
            help="override figure width in CSS px",
        ),
        click.option(
            "--height",
            type=click.IntRange(min=1),
            default=None,
            metavar="PX",
            help="override figure height in CSS px",
        ),
        click.option(
            "--engine",
            type=click.Choice(list(ENGINES)),
            default="auto",
            show_default=True,
            help="static-image backend: chrome when available, else firefox",
        ),
        click.option(
            "--chrome",
            metavar="PATH",
            default=None,
            help="path to a Chrome/Chromium/Edge binary (autodetected by default)",
        ),
        click.option(
            "--firefox",
            metavar="PATH",
            default=None,
            help="path to firefox.exe (autodetected by default)",
        ),
        click.option(
            "-v",
            "--verbose",
            is_flag=True,
            help="also print the configuration and every file written",
        ),
    ]
    for decorator in reversed(decorators):
        func = decorator(func)
    return func


def run_export(
    output_dir: StrPath,
    *,
    image_format: str = DEFAULT_FORMAT,
    scale: float = DEFAULT_SCALE,
    width: int | None = None,
    height: int | None = None,
    engine: Engine = "auto",
    chrome: StrPath | None = None,
    firefox: StrPath | None = None,
    verbose: bool = False,
    progress: bool = True,
) -> int:
    """Export the gallery and report the outcome; returns a process exit code."""
    try:
        written = export_all(
            output_dir,
            image_format=image_format,
            scale=scale,
            width=width,
            height=height,
            engine=engine,
            chrome=chrome,
            firefox=firefox,
            verbose=verbose,
            progress=progress,
        )
    except (FirefoxUnavailable, ExportError) as exc:
        click.secho(f"export failed: {exc}", fg="red", err=True)
        return 1

    total_kib = sum(path.stat().st_size for path in written) / 1024
    click.secho(
        f"exported {len(written)} figure(s) to {Path(output_dir).resolve()} "
        f"({total_kib:.0f} KiB, {image_format})",
        fg="green",
    )
    return 0


@click.command(
    name="python -m test_dash.export",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("output_dir", type=click.Path(file_okay=False, path_type=Path))
@export_options
def cli(
    output_dir: Path,
    image_format: str,
    scale: float,
    width: int | None,
    height: int | None,
    engine: Engine,
    chrome: str | None,
    firefox: str | None,
    verbose: bool,
) -> None:
    """Render the example figures to PNG or SVG.

    \b
    examples:
      python -m test_dash.export ./export
      python -m test_dash.export ./export --format svg --scale 3
      python -m test_dash.export ./export --engine firefox
    """
    raise SystemExit(
        run_export(
            output_dir,
            image_format=image_format,
            scale=scale,
            width=width,
            height=height,
            engine=engine,
            chrome=chrome,
            firefox=firefox,
            verbose=verbose,
        )
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Module entry point for ``python -m test_dash.export``."""
    cli.main(args=argv, prog_name="python -m test_dash.export")


if __name__ == "__main__":
    main()
