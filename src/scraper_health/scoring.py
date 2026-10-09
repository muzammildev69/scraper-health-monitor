"""Deterministic health scoring and run summaries.

The score is a plain average of per-URL points. It is a quick signal, not a
scientific measure; the rules are documented in the README.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from scraper_health.models import CheckResult, Report, ResultCategory, Summary

CATEGORY_POINTS: dict[ResultCategory, int] = {
    ResultCategory.SUCCESS: 100,
    ResultCategory.REDIRECT: 90,
    ResultCategory.RATE_LIMITED: 30,
    ResultCategory.CLIENT_ERROR: 20,
    ResultCategory.BLOCKED: 20,
    ResultCategory.SERVER_ERROR: 10,
    ResultCategory.TIMEOUT: 0,
    ResultCategory.CONNECTION_ERROR: 0,
    ResultCategory.SSL_ERROR: 0,
    ResultCategory.UNKNOWN: 0,
}
"""Points awarded to each result category (out of 100)."""

SLOW_THRESHOLD_SECONDS = 5.0
"""Successful responses slower than this earn only part of their points."""

SLOW_MULTIPLIER = 0.75
"""Fraction of points kept by a successful but slow response."""


def score_result(result: CheckResult) -> float:
    """Return the points (0-100) earned by a single result."""
    points = float(CATEGORY_POINTS[result.result])
    is_slow = result.response_time is not None and result.response_time > SLOW_THRESHOLD_SECONDS
    if result.result.is_successful and is_slow:
        points *= SLOW_MULTIPLIER
    return points


def calculate_health_score(results: Sequence[CheckResult]) -> int:
    """Return the average per-URL points, rounded half up to a 0-100 integer.

    An empty sequence scores 0.
    """
    if not results:
        return 0
    average = sum(score_result(result) for result in results) / len(results)
    return math.floor(average + 0.5)


def summarize(results: Sequence[CheckResult]) -> Summary:
    """Compute counts, timing statistics and the health score for a run.

    Timing statistics only include results where a response time was measured.
    """
    total = len(results)
    successful = sum(1 for result in results if result.result.is_successful)
    times = [r.response_time for r in results if r.response_time is not None]
    return Summary(
        total=total,
        successful=successful,
        failed=total - successful,
        success_rate=(successful / total * 100) if total else 0.0,
        average_response_time=(sum(times) / len(times)) if times else None,
        fastest_response_time=min(times, default=None),
        slowest_response_time=max(times, default=None),
        health_score=calculate_health_score(results),
        status_counts=_count_statuses(results),
        category_counts=_count_categories(results),
    )


def build_report(results: Sequence[CheckResult], generated_at: datetime | None = None) -> Report:
    """Bundle results, their summary and a UTC timestamp into a :class:`Report`."""
    return Report(
        generated_at=generated_at or datetime.now(UTC),
        summary=summarize(results),
        results=list(results),
    )


def _count_statuses(results: Sequence[CheckResult]) -> dict[str, int]:
    counts = Counter(result.status_label for result in results)
    # Numeric status codes first (ascending), then error labels alphabetically.
    ordered = sorted(counts.items(), key=lambda item: _status_sort_key(item[0]))
    return dict(ordered)


def _status_sort_key(label: str) -> tuple[int, int, str]:
    if label.isdigit():
        return (0, int(label), label)
    return (1, 0, label)


def _count_categories(results: Sequence[CheckResult]) -> dict[str, int]:
    counts = Counter(result.result for result in results)
    return {category.value: counts[category] for category in ResultCategory if counts[category]}
