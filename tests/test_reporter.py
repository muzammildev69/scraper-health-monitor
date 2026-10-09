from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from scraper_health.models import OutputError
from scraper_health.models import ResultCategory as C
from scraper_health.reporter import (
    format_duration,
    prepare_output_path,
    render_report,
    report_to_json,
    shorten_url,
    write_report,
)


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, width=100, color_system=None, force_terminal=False), buffer


@pytest.fixture
def sample_report(make_result, make_report):
    return make_report(
        [
            make_result(C.SUCCESS, url="https://example.com", status_code=200, response_time=0.21),
            make_result(C.BLOCKED, url="https://example.com/admin", status_code=403, response_time=0.31),
            make_result(C.RATE_LIMITED, url="https://example.com/api", status_code=429, response_time=0.52),
            make_result(C.TIMEOUT, url="https://example.com/slow", error="ReadTimeout: timed out"),
        ]
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.184, "0.184s"), (1.2034, "1.203s"), (4.8921, "4.892s"), (0, "0.000s"), (None, "n/a")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_shorten_url_strips_https_and_bare_slash():
    assert shorten_url("https://example.com/") == "example.com"
    assert shorten_url("https://example.com/products") == "example.com/products"


def test_shorten_url_keeps_http_scheme():
    assert shorten_url("http://example.com/x") == "http://example.com/x"


def test_shorten_url_truncates_in_the_middle():
    url = "https://example.com/" + "a" * 100 + "/end"
    short = shorten_url(url, max_length=30)
    assert len(short) == 30
    assert short.startswith("example.com/")
    assert short.endswith("/end")
    assert "…" in short


def test_json_output_is_valid_and_matches_schema(sample_report):
    data = json.loads(report_to_json(sample_report))
    assert data["schema_version"] == 1
    assert data["generated_at"] == "2026-10-08T12:20:00+00:00"
    assert data["summary"]["total"] == 4
    assert data["summary"]["successful"] == 1
    assert data["summary"]["health_score"] == sample_report.summary.health_score
    assert data["summary"]["status_counts"] == {"200": 1, "403": 1, "429": 1, "Timeout": 1}
    first, *_, last = data["results"]
    assert first["result"] == "SUCCESS" and first["status_code"] == 200
    assert last["result"] == "TIMEOUT" and last["response_time"] is None
    assert last["error"] == "ReadTimeout: timed out"


def test_render_report_contains_key_information(sample_report):
    console, buffer = _console()
    render_report(sample_report, console)
    text = buffer.getvalue()
    assert "Scraper Health Monitor" in text
    assert "URLs Checked" in text
    assert "Success Rate" in text
    assert f"{sample_report.summary.health_score}/100" in text
    assert "example.com/admin" in text
    assert "BLOCKED" in text
    assert "RATE LIMITED" in text


def test_render_report_lists_details_for_problem_results(make_result, make_report):
    report = make_report([make_result(C.BLOCKED, status_code=403, response_time=0.1)])
    console, buffer = _console()
    render_report(report, console)
    assert "Details" in buffer.getvalue()


def test_render_report_survives_markup_like_text(make_result, make_report):
    report = make_report([make_result(C.TIMEOUT, url="https://example.com/[bold]x", error="[red]oops[/red]")])
    console, buffer = _console()
    render_report(report, console)
    assert "[bold]" in buffer.getvalue()
    assert "[red]oops[/red]" in buffer.getvalue()


def test_full_urls_are_not_truncated(make_result, make_report):
    long_url = "https://example.com/" + "segment/" * 20 + "end"
    report = make_report([make_result(url=long_url, status_code=200, response_time=0.1)])
    console, buffer = _console()
    render_report(report, console, full_urls=True)
    assert "".join(buffer.getvalue().split()).count("end") >= 1
    assert "…" not in buffer.getvalue()


def test_write_report_creates_valid_json_file(tmp_path, sample_report):
    path = tmp_path / "reports" / "report.json"
    write_report(sample_report, path)
    assert json.loads(path.read_text(encoding="utf-8"))["summary"]["total"] == 4


def test_write_report_to_directory_raises(tmp_path, sample_report):
    with pytest.raises(OutputError):
        write_report(sample_report, tmp_path)


def test_prepare_output_path_rejects_directory(tmp_path):
    with pytest.raises(OutputError):
        prepare_output_path(tmp_path)


def test_prepare_output_path_when_parent_is_a_file(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(OutputError):
        prepare_output_path(blocker / "report.json")
