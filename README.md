# Field Plot Explorer

A small [Dash](https://dash.plotly.com/) app for exploring a field-trial
dataset: an interactive scatter plot with filtering, trend lines and a detail
panel that fills in when you click a marker. Its example figures can also be
exported to PNG or SVG in a single command — without installing anything extra.

![Python 3.13](https://img.shields.io/badge/python-3.13-blue)

## What you can do

**In the app**

- Choose the X and Y metrics, how points are coloured, how big and how
  transparent they are.
- Show or hide crops, and switch the per-crop trend lines on or off.
- Lasso a group of points by dragging a box over the plot (the default drag
  mode).
- Click any single point to inspect it: the panel on the right shows the whole
  row plus a bar chart with that crop's mean yield highlighted.

**With the export**

- One command writes all 9 example figures as PNG or SVG.
- Nothing extra to install: the export drives a browser you already have,
  opens no window, and downloads nothing.

## Getting started

You need [Python 3.13](https://www.python.org/downloads/) and
[uv](https://docs.astral.sh/uv/). Then:

```bash
uv sync            # install the project and its dependencies (first time only)
uv run test-dash   # launch the app at http://127.0.0.1:8050
```

Open <http://127.0.0.1:8050>, change the axes, filter crops, drag a box over the
plot, then click a point to see it in the inspector.

The dataset is generated on the fly from a fixed random seed — 300 plots, 5
crops × 60 — so it is synthetic, but always the same numbers on every run.

## Exporting figures

```bash
uv run test-dash --export-figures ./export
```

That writes nine files into `./export`:

| File | Shows |
| ---- | ----- |
| `01_rainfall_vs_yield` | every crop, with trend lines |
| `02_temperature_vs_yield` | temperature against yield |
| `03_soil_ph_vs_yield` | soil pH against yield |
| `04_mean_yield_by_crop` | average yield per crop |
| `05_crop_wheat` … `09_crop_soy` | rainfall against yield, one crop each |

### Options

| Option | What it does |
| ------ | ------------ |
| `--format png\|svg` | PNG (default) or resolution-independent SVG |
| `--scale 3` | makes PNGs sharper (default `2`); ignored for SVG |
| `--width 1400` / `--height 900` | resizes every figure, in CSS pixels |
| `-v` | also prints the settings and every file written |
| `-h` | the full option list |

```bash
uv run test-dash --export-figures ./export --format svg   # real vector output
uv run test-dash --export-figures ./export --scale 3 -v   # sharper, more output
```

While it runs, a progress bar names the figure being written, so you can see it
making its way through the nine.

### Which browser is used

You rarely have to think about this: the default `--engine auto` tries the fast
Chromium route and quietly falls back to headless Firefox if it cannot prove
that it works. `--engine chromium` or `--engine firefox` forces one of them.

- **Chromium** — plotly's standard rendering path. It works with *any*
  Chromium-based browser — Chrome, Edge, Brave, Chromium itself — and needs the
  optional `kaleido` package, which a development checkout already includes;
  otherwise add it with `uv sync --extra chromium`.
- **Firefox** — needs nothing but the Firefox you already have. It runs entirely
  in the background: no window, no downloads, no extra driver to install.
- If the wrong browser is picked, name the right one yourself with
  `--chromium PATH` or `--firefox PATH`.

Only a browser that is already installed is ever used, and it is accepted only
after a quick test render succeeds. If you ask for `--engine chromium` and it is
not usable, you get a clear error rather than a silent fallback.

## Tests

```bash
uv run pytest                         # fast, needs no browser
uv run mypy                           # type checking
TEST_DASH_BROWSER_EXPORT=1 uv run pytest -k "render or clear_of"   # real rendering
```

The last one actually renders through Firefox and Chromium; without that
variable it is skipped, so the normal test run stays quick and works everywhere.
