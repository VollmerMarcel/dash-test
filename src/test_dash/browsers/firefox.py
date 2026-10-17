"""The self-contained headless-Firefox static-image backend.

Needs nothing beyond Firefox itself:

1. each figure is serialised and dropped into a self-contained HTML page that
   inlines ``plotly.min.js`` from the installed plotly package (no CDN, no
   network);
2. Firefox is started **headless** with a throwaway profile, which makes it
   expose its built-in WebDriver BiDi endpoint (Firefox 147 has no CDP support
   left and no geckodriver is needed);
3. the page is asked for ``Plotly.toImage(...)``, which renders with the very
   same plotly.js that draws the web app, and the returned data URI is written
   straight to disk.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from html import escape as html_escape
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import plotly
import plotly.graph_objects as go
import websockets
from websockets.asyncio.client import ClientConnection

from .base import (
    DEFAULT_FORMAT,
    DEFAULT_HEIGHT,
    DEFAULT_SCALE,
    DEFAULT_WIDTH,
    Engine,
    ExportError,
    FirefoxUnavailable,
    StrPath,
)

#: One JSON object exchanged with the BiDi endpoint (command parameters/results).
JsonObject = dict[str, Any]

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


# --------------------------------------------------------------------------- #
# Firefox discovery
# --------------------------------------------------------------------------- #
def find_firefox(explicit: StrPath | None = None) -> Path:
    """Locate ``firefox.exe`` without installing anything.

    Order: the ``--firefox`` argument, ``PATH``, then the usual Windows install
    locations.
    """
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise FirefoxUnavailable(f"--firefox points at a missing file: {candidate}")

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

    raise FirefoxUnavailable("could not find firefox.exe; pass --firefox PATH")


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

    engine: Engine = "firefox"

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

    @property
    def executable(self) -> Path | None:
        """The Firefox binary this session drives."""
        return self.firefox

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
