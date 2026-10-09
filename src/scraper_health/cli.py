"""Command-line interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, NoReturn, Optional

import click
import typer
from rich.console import Console
from typer.core import TyperGroup

from scraper_health import __version__
from scraper_health.checker import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    load_urls,
    run_checks,
)
from scraper_health.history import DEFAULT_DB_PATH, HistoryStore
from scraper_health.models import InputError, OutputError, ScraperHealthError
from scraper_health.reporter import (
    prepare_output_path,
    render_history,
    render_report,
    report_to_json,
    write_report,
)
from scraper_health.scoring import build_report

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class _DefaultToCheckGroup(TyperGroup):
    """Treat ``scraper-health urls.txt`` as ``scraper-health check urls.txt``."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = ["check", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=_DefaultToCheckGroup,
    name="scraper-health",
    help="Check the health of the URLs your scrapers depend on.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"scraper-health {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Check the health of the URLs your scrapers depend on."""


def _fail(console: Console, message: str, code: int) -> NoReturn:
    console.print(f"Error: {message}", style="red", markup=False, highlight=False)
    raise typer.Exit(code)


@app.command()
def check(
    urls_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Text file with one URL per line. Blank lines and # comments are ignored.",
        ),
    ],
    timeout: Annotated[
        float,
        typer.Option("--timeout", "-t", min=0.1, help="Per-request timeout in seconds."),
    ] = DEFAULT_TIMEOUT,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", "-c", min=1, max=50, help="Maximum simultaneous requests."),
    ] = DEFAULT_CONCURRENCY,
    delay: Annotated[
        float,
        typer.Option("--delay", min=0.0, help="Seconds each worker pauses between requests."),
    ] = 0.0,
    user_agent: Annotated[
        str,
        typer.Option("--user-agent", help="User-Agent header to send."),
    ] = DEFAULT_USER_AGENT,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the report as JSON instead of the terminal report."),
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Also write the JSON report to this file."),
    ] = None,
    save_history: Annotated[
        bool,
        typer.Option("--save-history", help="Store this run in the SQLite history database."),
    ] = False,
    db: Annotated[
        Path,
        typer.Option("--db", envvar="SCRAPER_HEALTH_DB", help="History database path."),
    ] = DEFAULT_DB_PATH,
    full_urls: Annotated[
        bool,
        typer.Option("--full-urls", help="Show complete URLs instead of shortening them."),
    ] = False,
) -> None:
    """Check every URL in URLS_FILE and print a health report."""
    err = Console(stderr=True)

    try:
        loaded = load_urls(urls_file)
    except InputError as exc:
        _fail(err, str(exc), EXIT_USAGE)
    for number, text in loaded.invalid:
        err.print(f"Warning: line {number}: skipping invalid URL {text!r}", markup=False, highlight=False)
    if not loaded.urls:
        _fail(err, f"No valid URLs found in {urls_file}", EXIT_USAGE)

    if output is not None:
        try:
            prepare_output_path(output)
        except OutputError as exc:
            _fail(err, str(exc), EXIT_ERROR)

    try:
        with err.status(f"Checking {len(loaded.urls)} URL(s)..."):
            results = asyncio.run(
                run_checks(
                    loaded.urls,
                    timeout=timeout,
                    concurrency=concurrency,
                    user_agent=user_agent,
                    delay=delay,
                )
            )
    except KeyboardInterrupt:
        _fail(err, "Interrupted.", EXIT_INTERRUPTED)

    report = build_report(results)
    if json_output:
        typer.echo(report_to_json(report))
    else:
        render_report(report, Console(), full_urls=full_urls)

    try:
        if output is not None:
            write_report(report, output)
            err.print(f"Report written to {output}", markup=False, highlight=False)
        if save_history:
            count = HistoryStore(db).save_report(report)
            err.print(f"Saved {count} result(s) to {db}", markup=False, highlight=False)
    except ScraperHealthError as exc:
        _fail(err, str(exc), EXIT_ERROR)


@app.command()
def history(
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, help="Number of recent checks to show."),
    ] = 20,
    db: Annotated[
        Path,
        typer.Option("--db", envvar="SCRAPER_HEALTH_DB", help="History database path."),
    ] = DEFAULT_DB_PATH,
    full_urls: Annotated[
        bool,
        typer.Option("--full-urls", help="Show complete URLs instead of shortening them."),
    ] = False,
) -> None:
    """Show recent checks stored with --save-history."""
    console = Console()
    try:
        records = HistoryStore(db).recent(limit)
    except ScraperHealthError as exc:
        _fail(Console(stderr=True), str(exc), EXIT_ERROR)
    if not records:
        console.print(
            f"No history found at {db}. Run `scraper-health check URLS_FILE --save-history` first.",
            markup=False,
            highlight=False,
        )
        return
    render_history(records, console, full_urls=full_urls)


if __name__ == "__main__":
    app()
