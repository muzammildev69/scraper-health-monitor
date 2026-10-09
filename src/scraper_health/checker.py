"""Input parsing and asynchronous URL checking."""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scraper_health import __version__
from scraper_health.models import CheckResult, InputError, ResultCategory

DEFAULT_TIMEOUT = 10.0
DEFAULT_CONCURRENCY = 5
DEFAULT_MAX_BYTES = 64 * 1024
DEFAULT_USER_AGENT = f"ScraperHealthMonitor/{__version__}"

_STATUS_NOTES = {
    401: "401 Unauthorized / authentication required",
    403: "403 Forbidden / possible access restriction",
    404: "404 Not Found",
    408: "408 Request Timeout reported by the server",
    409: "409 Conflict",
    425: "425 Too Early",
    429: "429 Too Many Requests / possible rate limiting",
}


@dataclass(frozen=True, slots=True)
class LoadedUrls:
    """URLs read from an input file plus the lines that were rejected."""

    urls: list[str]
    invalid: list[tuple[int, str]]
    """``(line_number, text)`` pairs for lines that are not valid http(s) URLs."""


def is_valid_url(candidate: str) -> bool:
    """Return True for an absolute http or https URL with a host."""
    if any(char.isspace() for char in candidate):
        return False
    try:
        parts = urlsplit(candidate)
        parts.port  # noqa: B018 - accessing .port raises ValueError for a bad port
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def parse_urls(lines: Iterable[str]) -> LoadedUrls:
    """Extract URLs from text lines, skipping blank lines and ``#`` comments."""
    urls: list[str] = []
    invalid: list[tuple[int, str]] = []
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if is_valid_url(line):
            urls.append(line)
        else:
            invalid.append((number, line))
    return LoadedUrls(urls=urls, invalid=invalid)


def load_urls(path: Path) -> LoadedUrls:
    """Read URLs from a UTF-8 text file, one per line.

    Raises:
        InputError: if the file cannot be read or decoded.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"Cannot read {path}: {exc}") from exc
    return parse_urls(text.splitlines())


def classify_status(status_code: int) -> ResultCategory:
    """Map an HTTP status code to a result category."""
    if 200 <= status_code < 300:
        return ResultCategory.SUCCESS
    if 300 <= status_code < 400:
        return ResultCategory.REDIRECT
    if status_code == 403:
        return ResultCategory.BLOCKED
    if status_code == 429:
        return ResultCategory.RATE_LIMITED
    if 400 <= status_code < 500:
        return ResultCategory.CLIENT_ERROR
    if 500 <= status_code < 600:
        return ResultCategory.SERVER_ERROR
    return ResultCategory.UNKNOWN


def describe_status(status_code: int) -> str:
    """Return a cautious, human-readable description of a status code."""
    if status_code in _STATUS_NOTES:
        return _STATUS_NOTES[status_code]
    try:
        return f"{status_code} {HTTPStatus(status_code).phrase}"
    except ValueError:
        return f"{status_code} (unrecognised status code)"


def classify_exception(exc: BaseException) -> tuple[ResultCategory, str]:
    """Map a request exception to a category and a short description."""
    if isinstance(exc, httpx.TimeoutException):
        return ResultCategory.TIMEOUT, "Request timed out"
    chain = list(_exception_chain(exc))
    if any(isinstance(item, ssl.SSLError) for item in chain):
        return ResultCategory.SSL_ERROR, "SSL/TLS error"
    if any(isinstance(item, socket.gaierror) for item in chain):
        return ResultCategory.CONNECTION_ERROR, "DNS resolution failed"
    if isinstance(exc, httpx.TransportError):
        return ResultCategory.CONNECTION_ERROR, "Connection failed"
    return ResultCategory.UNKNOWN, "Unexpected request error"


async def check_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> CheckResult:
    """Send one GET request and classify the outcome.

    Redirects are reported, not followed. At most roughly ``max_bytes`` of the
    body are read, and the response time covers that read. Request failures are
    returned as results; only cancellation propagates.
    """
    started = time.perf_counter()
    try:
        response = await client.get(url, follow_redirects=False)
        bytes_read = await _read_capped(response, max_bytes)
        elapsed = time.perf_counter() - started
        status = response.status_code
        return CheckResult(
            url=url,
            result=classify_status(status),
            status_code=status,
            response_time=elapsed,
            description=describe_status(status),
            redirect_location=response.headers.get("location") if 300 <= status < 400 else None,
            content_type=response.headers.get("content-type"),
            bytes_read=bytes_read,
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        category, description = classify_exception(exc)
        return CheckResult(
            url=url,
            result=category,
            description=description,
            error=_error_detail(exc),
        )


async def run_checks(
    urls: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    concurrency: int = DEFAULT_CONCURRENCY,
    user_agent: str = DEFAULT_USER_AGENT,
    delay: float = 0.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[CheckResult]:
    """Check every URL with bounded concurrency and return results in input order.

    ``delay`` is the pause, in seconds, each worker takes after a request before
    it starts the next one. ``transport`` exists so tests can inject
    ``httpx.MockTransport``. If the surrounding task is cancelled, in-flight
    requests are cancelled and the HTTP client is closed.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(client: httpx.AsyncClient, url: str) -> CheckResult:
        async with semaphore:
            result = await check_url(client, url, max_bytes=max_bytes)
            if delay > 0:
                await asyncio.sleep(delay)
            return result

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        limits=limits,
        headers={"User-Agent": user_agent},
        follow_redirects=False,
        transport=transport,
    ) as client:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(worker(client, url)) for url in urls]
    return [task.result() for task in tasks]


async def _read_capped(response: httpx.Response, max_bytes: int) -> int:
    """Read the raw body until ``max_bytes`` is reached; return the bytes read."""
    total = 0
    async for chunk in response.aiter_raw():
        total += len(chunk)
        if total >= max_bytes:
            break
    return total


def _exception_chain(exc: BaseException | None) -> Iterator[BaseException]:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _error_detail(exc: BaseException) -> str:
    message = str(exc)
    name = type(exc).__name__
    return f"{name}: {message}" if message else name
