# Field Plot Explorer

A modest-complexity [Dash](https://dash.plotly.com/) example: an interactive
scatter plot over a synthetic field-trial dataset, plus an inspector panel that
fills in when you click a marker. It can also render every example figure to
PNG/SVG through Chrome/Kaleido when that is available, and fall back to
**headless Firefox** (no extra install) when it is not.

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)

## Features

### Web app
- **Interactive scatter plot** with selectable X/Y metrics, colour-by-crop
  grouping, marker size and opacity sliders.
- **Filtering** via a multi-select crop dropdown (including a friendly empty state).
- **Per-group OLS trend lines** computed with NumPy (no statsmodels dependency).
- **Box-select** as the default drag mode (`dragmode="select"`).
- **Click-to-inspect**: clicking a marker pushes its `customdata` into a detail
  panel with the full row and a mean-yield-per-crop bar chart highlighting the
  clicked crop.
- Custom styling via Dash's automatic `assets/` folder.

### Offline figure export
- `uv run test-dash --export-figures ./export` writes 9 images in one go.
- Two interchangeable backends, picked automatically: plotly's own
  **Chrome/Kaleido** path when it works, otherwise a **headless Firefox** one.
- Renders with the same plotly.js the app draws with — PNG or vector SVG, at any
  `--scale`.
- The Firefox route needs **no Chrome, no Kaleido, no geckodriver and no
  Selenium** — it drives the Firefox you already have via its built-in WebDriver
  BiDi socket. Nothing is ever downloaded or installed.

## Data

`src/test_dash/data.py` generates 300 field plots (5 crops × 60) in-process from
a fixed random seed — rainfall, temperature and soil pH drive a simple yield
model, so the correlations you see are real but reproducible.

## Getting started

The project uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management.

```bash
uv sync            # create .venv and install all dependencies (incl. dev group)
uv run test-dash   # launch the app at http://127.0.0.1:8050
```

Open <http://127.0.0.1:8050>, change axes, filter crops, box-select a region,
then click any single marker to see it in the inspector.

## Exporting figures

```bash
uv run test-dash --export-figures ./export                     # PNG at 2x
uv run test-dash --export-figures ./export --format svg        # real vector
uv run test-dash --export-figures ./export --scale 3 --width 1400 -v
uv run test-dash --export-figures ./export --engine firefox    # force Firefox
```

That writes:

| File | View |
| ---- | ---- |
| `01_rainfall_vs_yield` | all crops, OLS trend lines |
| `02_temperature_vs_yield` | |
| `03_soil_ph_vs_yield` | shows the quadratic pH penalty |
| `04_mean_yield_by_crop` | mean-yield bar chart |
| `05_crop_wheat` … `09_crop_soy` | rainfall vs yield, one crop each |

`--scale` multiplies the raster size for PNG (SVG is resolution independent).
While exporting, a [tqdm](https://tqdm.github.io/) progress bar names the figure
currently being written (`writing 05_crop_wheat.png: 55%|███▏ | 5/9 [...]`); it
hides itself automatically whenever output is piped, so scripts and CI logs stay
clean. The CLI is built with [click](https://click.palletsprojects.com/) — run
any command with `-h` for full help. The same exporter is also reachable as
`uv run python -m test_dash.export DIR`.

### Export engines

`--engine` chooses the backend:

| Value | Backend | Needs |
| ----- | ------- | ----- |
| `auto` (default) | Chrome/Kaleido when it proves to work, else Firefox | either |
| `chrome` | `plotly.io.to_image()` — plotly's own static-image path | `kaleido` + a Chromium browser |
| `firefox` | headless Firefox over WebDriver BiDi | Firefox |

The **Chrome** engine is the "normal" plotly export. It needs the optional
`kaleido` package plus an installed Chromium-family browser (Chrome, Chromium,
Edge or Brave). Kaleido is an optional extra rather than a hard dependency, so
enable it with:

```bash
uv sync --extra chrome        # or: uv add kaleido choreographer
```

(The dev group already pulls in Kaleido, so a development checkout can exercise
both engines out of the box.)

The **Firefox** engine needs nothing but Firefox and is used as the `auto`
fallback:

1. serialises the figure into a self-contained HTML page that **inlines
   `plotly.min.js` from the installed plotly package** (no CDN, no network);
2. starts Firefox **headless** with a throwaway profile whose `user.js` disables
   updates, telemetry and first-run prompts — so the run is invisible and can
   never pop an update dialog;
3. talks to Firefox's built-in WebDriver BiDi endpoint (`--remote-debugging-port`,
   available since CDP was removed in Firefox 147) and calls
   `Plotly.toImage(...)`, writing the returned data URI to disk.

Whichever engine runs, nothing is downloaded or installed: the Firefox engine
keeps a single browser session for all 9 figures while the Chrome engine hands
each figure to Kaleido, and every temporary profile lives in `%TEMP%` and is
removed afterwards. (Chrome may additionally leave its own component-updater
downloads in `%TEMP%` — that is the browser's doing, not the exporter's.) Both engines produce the same layout and pixel size; only
tiny anti-aliasing differences distinguish them. The figures live in
`src/test_dash/figures.py`, which imports **plotly only** — the export path never
imports dash or flask. The two backends live in `src/test_dash/browsers/` — one
module each, plus a shared `Renderer` protocol in `base.py` — while `export.py`
only owns the figure gallery, the write loop and the CLI.

Browser discovery, in order: `--chrome`/`--firefox`, then `TEST_DASH_CHROME` /
`TEST_DASH_FIREFOX`, then Kaleido's own browser layer (`choreographer`, a
dependency of Kaleido) which checks `BROWSER_PATH`, the `PATH`, the Windows
registry and the usual install locations. Only a browser the user already had is
ever used — `skip_local` keeps choreographer away from any Chrome it could have
downloaded itself. A browser is only accepted after a one-off 100×100 probe
render actually succeeds, so `auto` never picks a backend it cannot use. The
choice is never handed back to Kaleido's own search either: a browser we did not
discover ourselves is never probed, so `auto` falls back to Firefox rather than
letting Kaleido attach to a browser it downloaded. `--engine chrome` fails loudly (exit code 1)
rather than silently falling back:

```console
$ uv run test-dash --export-figures ./export --engine chrome
export failed: the Chrome engine needs the optional Kaleido backend; install it
with 'uv sync --extra chrome' (or 'uv add kaleido')
```

### Useful commands

| Command                | Purpose                                    |
| ---------------------- | ------------------------------------------ |
| `uv run test-dash`     | Run the app with the debug server          |
| `uv run test-dash --export-figures DIR` | Export the figure gallery    |
| `uv sync`              | Sync the environment with `pyproject.toml` |
| `uv sync --extra chrome` | Also install Kaleido for the Chrome engine |
| `uv add <package>`     | Add a dependency                           |
| `uv add --dev <pkg>`   | Add a dev dependency                       |
| `uv run pytest`        | Run the test suite                         |
| `uv run mypy`          | Type-check `src/` (config in pyproject)     |
| `uv tree`              | Show the resolved dependency tree          |

## Project layout

```
src/test_dash/
├── data.py            # deterministic synthetic dataset builder
├── figures.py         # plotly-only figure builders (no dash import)
├── app.py             # dash layout, callbacks, create_app()
├── export.py          # figure gallery + write loop + CLI
├── browsers/          # the static-image backends
│   ├── base.py        # shared types: Engine, Renderer protocol, errors
│   ├── chrome.py      # Chrome/Kaleido backend (plotly.io.to_image)
│   └── firefox.py     # headless-Firefox WebDriver BiDi backend
├── __init__.py        # main(): serve the app, or --export-figures
├── assets/style.css   # auto-served static CSS
└── tests/             # unit tests (browser tests are opt-in)
```

## Tests

```bash
uv run pytest                                            # fast, no browser
TEST_DASH_BROWSER_EXPORT=1 uv run pytest -k "render or clear_of"   # really renders
```

The opt-in tests drive a real browser: one exports through Firefox, one through
Chrome/Kaleido (skipped when Kaleido is not installed), and one checks that the
title stays clear of the legend and plot area.

## Type checking

```bash
uv run mypy          # checks src/ with the config in pyproject.toml
```

Hints are verified against `pandas-stubs`, `types-tqdm` and the `dash` /
`websockets` hints those packages ship. Plotly has no stubs, so its names are
treated as `Any`. Types that are used in more than one place get a name:
`StrPath` and `Engine` in `browsers/base.py`, `JsonObject` in
`browsers/firefox.py`, `ClickData` in `app.py`, and `Profile` / `MeanSd` in
`data.py`.
