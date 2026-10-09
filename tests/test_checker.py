from __future__ import annotations

import asyncio
import socket
import ssl

import httpx
import pytest

from scraper_health.checker import (
    DEFAULT_USER_AGENT,
    check_url,
    classify_status,
    describe_status,
    is_valid_url,
    load_urls,
    parse_urls,
    run_checks,
)
from scraper_health.models import InputError
from scraper_health.models import ResultCategory as C


def run(handler, urls, **kwargs):
    transport = httpx.MockTransport(handler)
    return asyncio.run(run_checks(urls, transport=transport, **kwargs))


def check_one(handler, url="https://example.com/"):
    return run(handler, [url])[0]


# --- URL loading -----------------------------------------------------------


def test_parse_urls_keeps_valid_urls_in_order():
    loaded = parse_urls(["https://example.com", "http://example.org/products?page=2"])
    assert loaded.urls == ["https://example.com", "http://example.org/products?page=2"]
    assert loaded.invalid == []


def test_parse_urls_skips_blank_lines_and_comments():
    lines = ["# targets", "", "   ", "https://example.com", "  # indented comment", "https://example.org  "]
    loaded = parse_urls(lines)
    assert loaded.urls == ["https://example.com", "https://example.org"]
    assert loaded.invalid == []


def test_parse_urls_url_fragment_is_not_a_comment():
    assert parse_urls(["https://example.com/page#section"]).urls == ["https://example.com/page#section"]


@pytest.mark.parametrize(
    "bad",
    [
        "example.com",
        "ftp://example.com",
        "http://",
        "https:///path",
        "https://exa mple.com",
        "https://example.com:notaport",
        "javascript:alert(1)",
        "https://[::1",
    ],
)
def test_parse_urls_reports_malformed_urls_with_line_numbers(bad):
    loaded = parse_urls(["https://example.com", bad])
    assert loaded.urls == ["https://example.com"]
    assert loaded.invalid == [(2, bad)]
    assert not is_valid_url(bad)


def test_load_urls_reads_file_with_bom(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# comment\nhttps://example.com\n", encoding="utf-8-sig")
    assert load_urls(path).urls == ["https://example.com"]


def test_load_urls_missing_file_raises_input_error(tmp_path):
    with pytest.raises(InputError):
        load_urls(tmp_path / "missing.txt")


def test_load_urls_undecodable_file_raises_input_error(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(InputError):
        load_urls(path)


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (200, C.SUCCESS),
        (204, C.SUCCESS),
        (301, C.REDIRECT),
        (302, C.REDIRECT),
        (403, C.BLOCKED),
        (404, C.CLIENT_ERROR),
        (408, C.CLIENT_ERROR),
        (409, C.CLIENT_ERROR),
        (425, C.CLIENT_ERROR),
        (429, C.RATE_LIMITED),
        (500, C.SERVER_ERROR),
        (503, C.SERVER_ERROR),
        (599, C.SERVER_ERROR),
        (100, C.UNKNOWN),
        (600, C.UNKNOWN),
    ],
)
def test_classify_status(status, category):
    assert classify_status(status) is category


def test_403_description_is_cautious():
    assert describe_status(403) == "403 Forbidden / possible access restriction"
    assert "possible rate limiting" in describe_status(429)


def test_describe_unknown_status_code():
    assert "unrecognised" in describe_status(799)


# --- HTTP results (mocked) ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "category"),
    [(200, C.SUCCESS), (403, C.BLOCKED), (404, C.CLIENT_ERROR), (429, C.RATE_LIMITED), (500, C.SERVER_ERROR)],
)
def test_check_url_classifies_responses(status, category):
    result = check_one(lambda request: httpx.Response(status, text="body"))
    assert result.result is category
    assert result.status_code == status
    assert result.response_time is not None and result.response_time >= 0
    assert result.error is None


@pytest.mark.parametrize("status", [301, 302])
def test_redirects_are_reported_not_followed(status):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://example.com/new"})

    result = check_one(handler)
    assert result.result is C.REDIRECT
    assert result.status_code == status
    assert result.redirect_location == "https://example.com/new"
    assert len(calls) == 1


def test_content_type_is_recorded():
    result = check_one(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, text="hi"))
    assert result.content_type == "text/html"
    assert result.bytes_read == 2


def test_timeout_is_classified_without_timing():
    def handler(request):
        raise httpx.ReadTimeout("timed out")

    result = check_one(handler)
    assert result.result is C.TIMEOUT
    assert result.status_code is None
    assert result.response_time is None
    assert "ReadTimeout" in result.error


def test_connection_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    result = check_one(handler)
    assert result.result is C.CONNECTION_ERROR
    assert result.response_time is None


def test_dns_failure_is_a_connection_error_with_clear_description():
    def handler(request):
        try:
            raise socket.gaierror(-2, "Name or service not known")
        except socket.gaierror as exc:
            raise httpx.ConnectError("lookup failed") from exc

    result = check_one(handler)
    assert result.result is C.CONNECTION_ERROR
    assert result.description == "DNS resolution failed"


def test_ssl_error():
    def handler(request):
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLError as exc:
            raise httpx.ConnectError("tls failure") from exc

    result = check_one(handler)
    assert result.result is C.SSL_ERROR


def test_unexpected_http_error_is_unknown():
    def handler(request):
        raise httpx.DecodingError("bad encoding")

    assert check_one(handler).result is C.UNKNOWN


# --- timing ------------------------------------------------------------------


def test_response_time_is_measured():
    async def handler(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200)

    result = check_one(handler)
    assert 0.04 <= result.response_time < 2.0


def test_slow_and_fast_requests_get_different_times():
    async def handler(request):
        if request.url.path == "/slow":
            await asyncio.sleep(0.08)
        return httpx.Response(200)

    fast, slow = run(handler, ["https://example.com/fast", "https://example.com/slow"])
    assert slow.response_time > fast.response_time


# --- run_checks behaviour ------------------------------------------------------


def test_results_keep_input_order():
    async def handler(request):
        # Later URLs finish first.
        await asyncio.sleep(0.05 if request.url.path == "/0" else 0)
        return httpx.Response(200)

    urls = [f"https://example.com/{i}" for i in range(6)]
    assert [r.url for r in run(handler, urls)] == urls


def test_concurrency_limit_is_respected():
    in_flight = 0
    peak = 0

    async def handler(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200)

    urls = [f"https://example.com/{i}" for i in range(12)]
    results = run(handler, urls, concurrency=3)
    assert len(results) == 12
    assert 1 < peak <= 3


def test_default_user_agent_and_override_are_sent():
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200)

    run(handler, ["https://example.com/"])
    run(handler, ["https://example.com/"], user_agent="MyScraper/1.0")
    assert seen == [DEFAULT_USER_AGENT, "MyScraper/1.0"]
    assert DEFAULT_USER_AGENT.startswith("ScraperHealthMonitor/")


def test_large_bodies_are_not_fully_downloaded():
    produced = 0

    async def body():
        nonlocal produced
        for _ in range(1000):
            produced += 1
            yield b"x" * 1024

    result = check_one(lambda request: httpx.Response(200, content=body()))
    # The default cap is 64 KiB, so far fewer than 1000 chunks should be pulled.
    assert result.bytes_read >= 64 * 1024
    assert produced < 1000


def test_one_failure_does_not_stop_other_checks():
    def handler(request):
        if request.url.path == "/boom":
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    results = run(handler, ["https://example.com/ok", "https://example.com/boom", "https://example.com/ok2"])
    assert [r.result for r in results] == [C.SUCCESS, C.CONNECTION_ERROR, C.SUCCESS]


def test_invalid_concurrency_is_rejected():
    with pytest.raises(ValueError):
        asyncio.run(run_checks(["https://example.com"], concurrency=0))


def test_cancellation_stops_in_flight_requests():
    async def scenario():
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.sleep(30)
            return httpx.Response(200)

        task = asyncio.create_task(
            run_checks(["https://example.com/a", "https://example.com/b"], transport=httpx.MockTransport(handler))
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_check_url_can_be_used_with_a_client_directly():
    async def scenario():
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            return await check_url(client, "https://example.com/")

    assert asyncio.run(scenario()).result is C.SUCCESS
