from __future__ import annotations

import pytest

from scraper_health.models import ResultCategory as C
from scraper_health.scoring import calculate_health_score, summarize


def test_empty_results_score_zero():
    assert calculate_health_score([]) == 0


def test_all_success_scores_100(make_result):
    results = [make_result(C.SUCCESS, status_code=200, response_time=0.2) for _ in range(5)]
    assert calculate_health_score(results) == 100


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (C.SUCCESS, 100),
        (C.REDIRECT, 90),
        (C.RATE_LIMITED, 30),
        (C.CLIENT_ERROR, 20),
        (C.BLOCKED, 20),
        (C.SERVER_ERROR, 10),
        (C.TIMEOUT, 0),
        (C.CONNECTION_ERROR, 0),
        (C.SSL_ERROR, 0),
        (C.UNKNOWN, 0),
    ],
)
def test_single_result_points(make_result, category, expected):
    assert calculate_health_score([make_result(category)]) == expected


def test_mixed_results_average(make_result):
    results = [make_result(C.SUCCESS, response_time=0.1) for _ in range(7)]
    results += [make_result(C.BLOCKED), make_result(C.RATE_LIMITED), make_result(C.TIMEOUT)]
    # (7 * 100 + 20 + 30 + 0) / 10
    assert calculate_health_score(results) == 75


def test_score_rounds_half_up(make_result):
    # (100 + 90 + 90 + 90) / 4 = 92.5 -> 93 (round-half-up, not banker's rounding)
    results = [make_result(C.SUCCESS), make_result(C.REDIRECT), make_result(C.REDIRECT), make_result(C.REDIRECT)]
    assert calculate_health_score(results) == 93


def test_slow_success_loses_points(make_result):
    slow = make_result(C.SUCCESS, response_time=6.0)
    assert calculate_health_score([slow]) == 75


def test_exactly_at_slow_threshold_is_not_penalised(make_result):
    assert calculate_health_score([make_result(C.SUCCESS, response_time=5.0)]) == 100


def test_slowness_does_not_affect_errors(make_result):
    assert calculate_health_score([make_result(C.SERVER_ERROR, response_time=9.0)]) == 10


def test_score_is_deterministic(make_result):
    results = [make_result(C.SUCCESS), make_result(C.BLOCKED), make_result(C.TIMEOUT)]
    assert len({calculate_health_score(results) for _ in range(10)}) == 1


def test_summary_counts_and_rate(make_result):
    results = [
        make_result(C.SUCCESS, status_code=200, response_time=0.2),
        make_result(C.REDIRECT, status_code=301, response_time=0.4),
        make_result(C.BLOCKED, status_code=403, response_time=0.6),
        make_result(C.TIMEOUT),
    ]
    summary = summarize(results)
    assert (summary.total, summary.successful, summary.failed) == (4, 2, 2)
    assert summary.success_rate == pytest.approx(50.0)


def test_summary_timing_ignores_unmeasured_results(make_result):
    results = [
        make_result(C.SUCCESS, status_code=200, response_time=0.2),
        make_result(C.SUCCESS, status_code=200, response_time=0.6),
        make_result(C.TIMEOUT),
        make_result(C.CONNECTION_ERROR),
    ]
    summary = summarize(results)
    assert summary.average_response_time == pytest.approx(0.4)
    assert summary.fastest_response_time == pytest.approx(0.2)
    assert summary.slowest_response_time == pytest.approx(0.6)


def test_summary_without_any_timing(make_result):
    summary = summarize([make_result(C.TIMEOUT)])
    assert summary.average_response_time is None
    assert summary.fastest_response_time is None
    assert summary.slowest_response_time is None


def test_status_counts_sorted_codes_then_errors(make_result):
    results = [
        make_result(C.TIMEOUT),
        make_result(C.BLOCKED, status_code=403),
        make_result(C.SUCCESS, status_code=200),
        make_result(C.SUCCESS, status_code=200),
        make_result(C.CONNECTION_ERROR),
    ]
    counts = summarize(results).status_counts
    assert list(counts.items()) == [("200", 2), ("403", 1), ("Connection error", 1), ("Timeout", 1)]


def test_category_counts_only_include_present_categories(make_result):
    summary = summarize([make_result(C.SUCCESS), make_result(C.BLOCKED), make_result(C.BLOCKED)])
    assert summary.category_counts == {"SUCCESS": 1, "BLOCKED": 2}
