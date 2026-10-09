"""Terminal, JSON and file output for reports and history."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scraper_health.history import HistoryRecord
from scraper_health.models import CheckResult, OutputError, Report, ResultCategory

URL_DISPLAY_WIDTH = 42

_CATEGORY_STYLES = {
    ResultCategory.SUCCESS: "green",
    ResultCategory.REDIRECT: "green",
    ResultCategory.CLIENT_ERROR: "yellow",
    ResultCategory.BLOCKED: "yellow",
    ResultCategory.RATE_LIMITED: "yellow",
}


def format_duration(seconds: float | None) -> str:
    """Format seconds as e.g. ``0.184s``; ``n/a`` when not measured."""
    return "n/a" if seconds is None else f"{seconds:.3f}s"


def shorten_url(url: str, max_length: int = URL_DISPLAY_WIDTH) -> str:
    """Drop ``https://`` and a bare trailing slash, then middle-truncate with an ellipsis."""
    display = url.removeprefix("https://")
    if display.endswith("/") and display.count("/") == 1:
        display = display[:-1]
    max_length = max(max_length, 8)
    if len(display) <= max_length:
        return display
    keep = max_length - 1
    tail = keep // 2
    head = keep - tail
    return display[:head] + "…" + display[len(display) - tail :]


def report_to_json(report: Report) -> str:
    """Render a report as pretty-printed JSON."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def prepare_output_path(path: Path) -> None:
    """Create parent directories and check that ``path`` can plausibly be written.

    Raises:
        OutputError: if the path is a directory or its parent cannot be created.
    """
    if path.is_dir():
        raise OutputError(f"Output path {path} is a directory; give a file name.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"Cannot create directory for {path}: {exc}") from exc


def write_report(report: Report, path: Path) -> None:
    """Write the JSON report to ``path``.

    Raises:
        OutputError: if the file cannot be written.
    """
    prepare_output_path(path)
    try:
        path.write_text(report_to_json(report) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputError(f"Cannot write report to {path}: {exc}") from exc


def render_report(report: Report, console: Console, *, full_urls: bool = False) -> None:
    """Print the summary panel, results table and details of non-successful checks."""
    summary = report.summary
    console.print(Panel(Align.center(Text("Scraper Health Monitor", style="bold")), box=box.ROUNDED))

    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column(justify="right")
    grid.add_row("URLs Checked", str(summary.total))
    grid.add_row("Successful", str(summary.successful))
    grid.add_row("Failed", str(summary.failed))
    grid.add_row("", "")
    grid.add_row("Success Rate", f"{summary.success_rate:.1f}%")
    grid.add_row("Average Response", format_duration(summary.average_response_time))
    grid.add_row("Fastest", format_duration(summary.fastest_response_time))
    grid.add_row("Slowest", format_duration(summary.slowest_response_time))
    grid.add_row("", "")
    grid.add_row(Text("Health Score", style="bold"), Text(f"{summary.health_score}/100", style="bold"))
    console.print(grid)

    console.print()
    console.print(Text("Status Codes", style="bold"))
    codes = Table.grid(padding=(0, 2))
    codes.add_column()
    codes.add_column(justify="right")
    for label, count in summary.status_counts.items():
        codes.add_row(Text(f"  {label}"), str(count))
    console.print(codes)

    console.print()
    console.print(_results_table(report.results, full_urls))

    details = [r for r in report.results if r.result is not ResultCategory.SUCCESS]
    if details:
        console.print()
        console.print(Text("Details", style="bold"))
        for result in details:
            console.print(_detail_line(result, full_urls))


def render_history(records: Sequence[HistoryRecord], console: Console, *, full_urls: bool = False) -> None:
    """Print stored history records as a table. Times are shown in UTC."""
    console.print(Text("Recent Health Checks", style="bold"))
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("Time (UTC)", no_wrap=True)
    table.add_column("URL", overflow="fold" if full_urls else "ellipsis", no_wrap=not full_urls)
    table.add_column("Status", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Result")
    table.add_column("Score", justify="right")
    for record in records:
        url = record.url if full_urls else shorten_url(record.url)
        table.add_row(
            record.checked_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M"),
            Text(url),
            "—" if record.status_code is None else str(record.status_code),
            format_duration(record.response_time),
            Text(record.result.label, style=_CATEGORY_STYLES.get(record.result, "red")),
            str(record.health_score),
        )
    console.print(table)


def _results_table(results: Sequence[CheckResult], full_urls: bool) -> Table:
    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("URL", overflow="fold" if full_urls else "ellipsis", no_wrap=not full_urls)
    table.add_column("Status", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Result")
    for result in results:
        url = result.url if full_urls else shorten_url(result.url)
        table.add_row(
            Text(url),
            "—" if result.status_code is None else str(result.status_code),
            format_duration(result.response_time),
            Text(result.result.label, style=_CATEGORY_STYLES.get(result.result, "red")),
        )
    return table


def _detail_line(result: CheckResult, full_urls: bool) -> Text:
    url = result.url if full_urls else shorten_url(result.url)
    message = result.description
    if result.redirect_location:
        message += f" -> {result.redirect_location}"
    if result.error:
        message += f" ({result.error})"
    return Text(f"  {url}: {message}")
