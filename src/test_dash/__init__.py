"""Field Plot Explorer — a modest-complexity Dash scatter-plot example.

Besides serving the web app, the installed ``test-dash`` command can render every
example figure to PNG or SVG without installing anything extra::

    uv run test-dash --export-figures ./export

By default that prefers plotly's Chromium/Kaleido backend when it is available
and falls back to headless Firefox otherwise; ``--engine`` forces either one.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from .browsers import Engine

__all__ = ["main", "build_cli"]


def build_cli() -> click.Command:
    """Create the ``test-dash`` command.

    Built on demand rather than at import time: that keeps ``import test_dash``
    from pulling in plotly/websockets/tqdm, and it means running
    ``python -m test_dash.export`` does not make runpy warn about
    ``test_dash.export`` already being in :data:`sys.modules`.
    """
    from .export import export_options, run_export

    @click.command(
        name="test-dash",
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--export-figures",
        "export_figures",
        metavar="DIR",
        type=click.Path(file_okay=False, path_type=Path),
        default=None,
        help="render all example figures into DIR instead of serving the app",
    )
    @export_options
    def cli(
        export_figures: Path | None,
        image_format: str,
        scale: float,
        width: int | None,
        height: int | None,
        engine: Engine,
        chromium: str | None,
        firefox: str | None,
        verbose: bool,
    ) -> None:
        """Serve the Field Plot Explorer app, or export its figures offline.

        \b
        examples:
          uv run test-dash                                serve the app on :8050
          uv run test-dash --export-figures ./export      write PNGs at scale 2
          uv run test-dash --export-figures ./export --format svg
          uv run test-dash --export-figures ./export --engine firefox
        """
        if export_figures is not None:
            raise SystemExit(
                run_export(
                    export_figures,
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

        # Imported lazily so serving never needs the export machinery at hand.
        from .app import create_app

        create_app().run(debug=True)

    return cli


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point for the ``test-dash`` command."""
    build_cli().main(args=argv, prog_name="test-dash")


if __name__ == "__main__":
    main()
