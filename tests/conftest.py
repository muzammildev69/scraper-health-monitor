from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scraper_health.models import CheckResult, Report, ResultCategory
from scraper_health.scoring import build_report


@pytest.fixture
def make_result():
    """Factory for CheckResult objects with sensible defaults."""

    def factory(
        category: ResultCategory = ResultCategory.SUCCESS,
        *,
        url: str = "https://example.com/",
        status_code: int | None = None,
        response_time: float | None = None,
        error: str | None = None,
    ) -> CheckResult:
        return CheckResult(
            url=url,
            result=category,
            status_code=status_code,
            response_time=response_time,
            description=category.label,
            error=error,
        )

    return factory


@pytest.fixture
def make_report():
    """Factory that builds a Report with a fixed, timezone-aware timestamp."""

    def factory(results, generated_at: datetime | None = None) -> Report:
        return build_report(results, generated_at or datetime(2026, 10, 8, 12, 20, tzinfo=UTC))

    return factory
