from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from scraper_health import checker, cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test in an empty directory so stray files can't leak in or out."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def urls_file(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text(
        "# demo\nhttps://example.com/\nhttps://example.com/admin\nhttps://example.com/api\nhttps://example.com/slow\n"
    )
    return path


def demo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/admin":
        return httpx.Response(403)
    if path == "/api":
        return httpx.Response(429)
    if path == "/slow":
        raise httpx.ReadTimeout("timed out")
    return httpx.Response(200, text="ok")


@pytest.fixture
def mock_network(monkeypatch):
    """Route the CLI's HTTP traffic through an in-memory handler."""

    def install(handler=demo_handler):
        real_run_checks = checker.run_checks

        async def patched(urls, **kwargs):
            return await real_run_checks(urls, transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(cli, "run_checks", patched)

    install()
    return install


def test_help_lists_commands():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output
    assert "history" in result.output


def test_version():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("scraper-health ")


def test_no_arguments_shows_help():
    result = runner.invoke(cli.app, [])
    assert "Usage" in result.output


def test_bare_file_argument_runs_check(urls_file, mock_network):
    result = runner.invoke(cli.app, [str(urls_file)])
    assert result.exit_code == 0, result.output
    assert "Scraper Health Monitor" in result.output
    assert "URLs Checked" in result.output
    assert "BLOCKED" in result.output
    assert "RATE LIMITED" in result.output
    assert "TIMEOUT" in result.output


def test_explicit_check_command_matches_bare_form(urls_file, mock_network):
    result = runner.invoke(cli.app, ["check", str(urls_file)])
    assert result.exit_code == 0, result.output
    assert "Health Score" in result.output


def test_json_output_is_valid_and_clean(urls_file, mock_network):
    result = runner.invoke(cli.app, [str(urls_file), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["summary"]["total"] == 4
    assert data["summary"]["successful"] == 1
    assert [r["result"] for r in data["results"]] == ["SUCCESS", "BLOCKED", "RATE_LIMITED", "TIMEOUT"]
    assert "Scraper Health Monitor" not in result.stdout


def test_output_file_is_written(urls_file, mock_network, tmp_path):
    out = tmp_path / "out" / "report.json"
    result = runner.invoke(cli.app, [str(urls_file), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert "Health Score" in result.output
    assert json.loads(out.read_text())["summary"]["total"] == 4


def test_json_and_output_together(urls_file, mock_network, tmp_path):
    out = tmp_path / "report.json"
    result = runner.invoke(cli.app, [str(urls_file), "--json", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == json.loads(out.read_text())


def test_output_path_that_is_a_directory_fails_before_checking(urls_file, mock_network, tmp_path):
    result = runner.invoke(cli.app, [str(urls_file), "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert "directory" in result.output
    assert "Scraper Health Monitor" not in result.output


def test_invalid_urls_are_skipped_with_a_warning(tmp_path, mock_network):
    path = tmp_path / "mixed.txt"
    path.write_text("https://example.com/\nnot-a-url\n")
    result = runner.invoke(cli.app, [str(path), "--json"])
    assert result.exit_code == 0, result.output
    assert "line 2" in result.output
    assert json.loads(result.stdout)["summary"]["total"] == 1


def test_file_without_valid_urls_exits_with_usage_error(tmp_path, mock_network):
    path = tmp_path / "bad.txt"
    path.write_text("# nothing here\nnot-a-url\n")
    result = runner.invoke(cli.app, [str(path)])
    assert result.exit_code == 2
    assert "No valid URLs" in result.output


def test_missing_input_file_is_a_usage_error(mock_network):
    result = runner.invoke(cli.app, ["does-not-exist.txt"])
    assert result.exit_code == 2


@pytest.mark.parametrize("args", [["--timeout", "0"], ["--concurrency", "0"], ["--delay", "-1"]])
def test_invalid_option_values_are_rejected(urls_file, mock_network, args):
    result = runner.invoke(cli.app, [str(urls_file), *args])
    assert result.exit_code == 2


def test_options_are_passed_to_the_checker(urls_file, monkeypatch):
    captured = {}

    async def fake_run_checks(urls, **kwargs):
        captured.update(kwargs, urls=urls)
        return []

    monkeypatch.setattr(cli, "run_checks", fake_run_checks)
    result = runner.invoke(
        cli.app,
        [str(urls_file), "--timeout", "3.5", "--concurrency", "2", "--delay", "0.25", "--user-agent", "MyScraper/1.0"],
    )
    assert result.exit_code == 0, result.output
    assert captured["timeout"] == 3.5
    assert captured["concurrency"] == 2
    assert captured["delay"] == 0.25
    assert captured["user_agent"] == "MyScraper/1.0"
    assert len(captured["urls"]) == 4


def test_user_agent_reaches_the_server(urls_file, mock_network):
    seen = set()

    def handler(request):
        seen.add(request.headers["user-agent"])
        return httpx.Response(200)

    mock_network(handler)
    result = runner.invoke(cli.app, [str(urls_file), "--user-agent", "MyScraper/1.0", "--json"])
    assert result.exit_code == 0, result.output
    assert seen == {"MyScraper/1.0"}


def test_history_is_not_saved_by_default(urls_file, mock_network, tmp_path):
    result = runner.invoke(cli.app, [str(urls_file)])
    assert result.exit_code == 0
    assert not (tmp_path / ".scraper-health").exists()


def test_save_history_then_history_command(urls_file, mock_network, tmp_path):
    db = tmp_path / "h.db"
    result = runner.invoke(cli.app, [str(urls_file), "--save-history", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert db.exists()

    shown = runner.invoke(cli.app, ["history", "--db", str(db)])
    assert shown.exit_code == 0, shown.output
    assert "Recent Health Checks" in shown.output
    assert "example.com/admin" in shown.output
    assert "403" in shown.output

    limited = runner.invoke(cli.app, ["history", "--db", str(db), "--limit", "1"])
    assert "example.com/admin" not in limited.output


def test_history_uses_default_database_location(urls_file, mock_network, tmp_path):
    runner.invoke(cli.app, [str(urls_file), "--save-history"])
    assert (tmp_path / ".scraper-health" / "history.db").exists()


def test_history_env_var_sets_database(urls_file, mock_network, tmp_path, monkeypatch):
    db = tmp_path / "env.db"
    monkeypatch.setenv("SCRAPER_HEALTH_DB", str(db))
    runner.invoke(cli.app, [str(urls_file), "--save-history"])
    assert db.exists()


def test_history_with_no_database(tmp_path):
    result = runner.invoke(cli.app, ["history", "--db", str(tmp_path / "none.db")])
    assert result.exit_code == 0
    assert "No history found" in result.output


def test_history_with_corrupt_database(tmp_path):
    db = tmp_path / "bad.db"
    db.write_bytes(b"garbage" * 200)
    result = runner.invoke(cli.app, ["history", "--db", str(db)])
    assert result.exit_code == 1
    assert "Could not read history" in result.output
