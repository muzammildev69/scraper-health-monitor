"""Data models and exceptions shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1
"""Version of the JSON report layout. Bumped only on breaking schema changes."""


class ScraperHealthError(Exception):
    """Base class for errors that should be shown to the user without a traceback."""


class InputError(ScraperHealthError):
    """The URL input file could not be read."""


class OutputError(ScraperHealthError):
    """The report file could not be written."""


class HistoryError(ScraperHealthError):
    """The SQLite history database could not be read or written."""


class ResultCategory(StrEnum):
    """The outcome of checking a single URL."""

    SUCCESS = "SUCCESS"
    REDIRECT = "REDIRECT"
    CLIENT_ERROR = "CLIENT_ERROR"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    SSL_ERROR = "SSL_ERROR"
    UNKNOWN = "UNKNOWN"

    @property
    def label(self) -> str:
        """Human-friendly label, e.g. ``RATE LIMITED``."""
        return self.value.replace("_", " ")

    @property
    def is_successful(self) -> bool:
        """Whether the endpoint answered without an error (2xx or 3xx)."""
        return self in _SUCCESSFUL_CATEGORIES


_SUCCESSFUL_CATEGORIES = frozenset({ResultCategory.SUCCESS, ResultCategory.REDIRECT})

_ERROR_STATUS_LABELS = {
    ResultCategory.TIMEOUT: "Timeout",
    ResultCategory.CONNECTION_ERROR: "Connection error",
    ResultCategory.SSL_ERROR: "SSL error",
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of a single GET request.

    ``response_time`` is in seconds and is ``None`` whenever no response was
    received, so failed requests never skew timing statistics.
    """

    url: str
    result: ResultCategory
    status_code: int | None = None
    response_time: float | None = None
    description: str = ""
    error: str | None = None
    redirect_location: str | None = None
    content_type: str | None = None
    bytes_read: int | None = None

    @property
    def status_label(self) -> str:
        """The status code as text, or a short label when there was no response."""
        if self.status_code is not None:
            return str(self.status_code)
        return _ERROR_STATUS_LABELS.get(self.result, "Unknown error")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON report layout."""
        return {
            "url": self.url,
            "status_code": self.status_code,
            "response_time": _round_or_none(self.response_time, 3),
            "result": self.result.value,
            "description": self.description,
            "error": self.error,
            "redirect_location": self.redirect_location,
            "content_type": self.content_type,
            "bytes_read": self.bytes_read,
        }


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregate statistics for one run."""

    total: int
    successful: int
    failed: int
    success_rate: float
    average_response_time: float | None
    fastest_response_time: float | None
    slowest_response_time: float | None
    health_score: int
    status_counts: dict[str, int] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON report layout."""
        return {
            "total": self.total,
            "successful": self.successful,
            "failed": self.failed,
            "success_rate": round(self.success_rate, 1),
            "average_response_time": _round_or_none(self.average_response_time, 3),
            "fastest_response_time": _round_or_none(self.fastest_response_time, 3),
            "slowest_response_time": _round_or_none(self.slowest_response_time, 3),
            "health_score": self.health_score,
            "status_counts": dict(self.status_counts),
            "category_counts": dict(self.category_counts),
        }


@dataclass(frozen=True, slots=True)
class Report:
    """A complete report: when it was generated, the summary and every result."""

    generated_at: datetime
    summary: Summary
    results: list[CheckResult]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the documented JSON report layout."""
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at.astimezone(UTC).isoformat(timespec="seconds"),
            "summary": self.summary.to_dict(),
            "results": [result.to_dict() for result in self.results],
        }


def _round_or_none(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
