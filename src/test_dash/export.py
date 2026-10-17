"""Export the example figure gallery to PNG/SVG, entirely offline.

This module owns the gallery (:func:`preset_figures`), the write loop
(:func:`export_all`) and the command line (:func:`cli`).  The static-image
renderers live in :mod:`test_dash.browsers`, which knows how to drive either
plotly's Chromium/Kaleido backend or headless Firefox; ``--engine`` chooses one
and ``auto`` (the default) prefers Chromium, falling back to Firefox when
Chromium cannot be proven to work.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import click
import plotly.graph_objects as go
from tqdm import tqdm

from .browsers import (
    DEFAULT_FORMAT,
    DEFAULT_HEIGHT,
    DEFAULT_SCALE,
    DEFAULT_WIDTH,
    ENGINES,
    Engine,
    ExportError,
    FirefoxUnavailable,
    Renderer,
    StrPath,
    select_renderer,
)
from .data import CROPS
from .figures import build_figure, mean_yield_chart

#: A callable being wrapped by a click option/argument decorator.
_DecoratorT = TypeVar("_DecoratorT", bound=Callable[..., Any])

_SCATTER_DEFAULTS: dict[str, Any] = {
    "color_by": "crop",
    "marker_size": 11,
    "opacity": 0.75,
    "crops": list(CROPS),
    "show_trendline": True,
}


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
# Entry point
# --------------------------------------------------------------------------- #
def export_all(
    output_dir: StrPath,
    image_format: str = DEFAULT_FORMAT,
    scale: float = DEFAULT_SCALE,
    width: int | None = None,
    height: int | None = None,
    engine: Engine = "auto",
    chromium: StrPath | None = None,
    firefox: StrPath | None = None,
    presets: Sequence[Preset] | None = None,
    verbose: bool = False,
    progress: bool = True,
) -> list[Path]:
    """Export every preset into ``output_dir`` through one renderer.

    ``width``/``height`` override every preset when given; otherwise each preset
    keeps its own size.  ``engine`` picks the backend (see
    :func:`test_dash.browsers.select_renderer`).  A :mod:`tqdm` bar names the
    figure currently being written and steps once per file; it disables itself
    automatically when stderr is not a terminal (pipes, CI, tests), so logs stay
    clean.
    """
    directory = Path(output_dir)
    figures = list(presets) if presets is not None else preset_figures()
    log_path = directory / "firefox.log"

    # Resolve the backend first: a missing browser or an unusable engine should
    # not leave an empty output directory behind.
    renderer: Renderer = select_renderer(
        engine, chromium=chromium, firefox=firefox, log_path=log_path
    )
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if verbose:
        print(f"engine:   {renderer.engine}")
        print(f"browser:  {renderer.executable or 'autodetected'}")
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
            help="static-image backend: chromium when available, else firefox",
        ),
        click.option(
            "--chromium",
            metavar="PATH",
            default=None,
            help="path to a Chromium-based browser, Chrome/Edge/Brave/... "
            "(autodetected by default)",
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
    chromium: StrPath | None = None,
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
            chromium=chromium,
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
    chromium: str | None,
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
            chromium=chromium,
            firefox=firefox,
            verbose=verbose,
        )
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Module entry point for ``python -m test_dash.export``."""
    cli.main(args=argv, prog_name="python -m test_dash.export")


if __name__ == "__main__":
    main()
