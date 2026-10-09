from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scraper_health.history import HistoryStore
from scraper_health.models import HistoryError
from scraper_health.models import ResultCategory as C


def _sample_results(make_result):
    return [
        make_result(C.SUCCESS, url="https://example.com/", status_code=200, response_time=0.21),
        make_result(C.BLOCKED, url="https://example.org/", status_code=403, response_time=0.31),
        make_result(C.TIMEOUT, url="https://example.net/", error="ReadTimeout: timed out"),
    ]


def test_save_and_retrieve_multiple_urls(tmp_path, make_result, make_report):
    store = HistoryStore(tmp_path / "history.db")
    report = make_report(_sample_results(make_result))

    assert store.save_report(report) == 3
    records = store.recent()

    assert [r.url for r in records] == ["https://example.com/", "https://example.org/", "https://example.net/"]
    assert [r.status_code for r in records] == [200, 403, None]
    assert [r.result for r in records] == [C.SUCCESS, C.BLOCKED, C.TIMEOUT]
    assert records[0].response_time == pytest.approx(0.21)
    assert records[2].response_time is None
    assert records[2].error == "ReadTimeout: timed out"
    assert {r.health_score for r in records} == {report.summary.health_score}


def test_timestamp_round_trips_as_utc(tmp_path, make_result, make_report):
    when = datetime(2026, 10, 8, 12, 20, 5, tzinfo=UTC)
    store = HistoryStore(tmp_path / "history.db")
    store.save_report(make_report(_sample_results(make_result), when))
    assert all(r.checked_at == when for r in store.recent())


def test_newest_run_comes_first(tmp_path, make_result, make_report):
    store = HistoryStore(tmp_path / "history.db")
    older = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    newer = older + timedelta(days=1)
    store.save_report(make_report([make_result(url="https://old.example/")], older))
    store.save_report(make_report([make_result(url="https://new.example/")], newer))
    assert [r.url for r in store.recent()] == ["https://new.example/", "https://old.example/"]


def test_limit_is_applied(tmp_path, make_result, make_report):
    store = HistoryStore(tmp_path / "history.db")
    store.save_report(make_report(_sample_results(make_result)))
    assert len(store.recent(limit=2)) == 2


def test_missing_database_returns_nothing_and_creates_nothing(tmp_path):
    path = tmp_path / "nested" / "history.db"
    assert HistoryStore(path).recent() == []
    assert not path.exists()


def test_save_creates_parent_directories(tmp_path, make_result, make_report):
    path = tmp_path / "a" / "b" / "history.db"
    HistoryStore(path).save_report(make_report([make_result()]))
    assert path.exists()


def test_rows_are_appended_across_runs(tmp_path, make_result, make_report):
    store = HistoryStore(tmp_path / "history.db")
    store.save_report(make_report([make_result()], datetime(2026, 1, 1, tzinfo=UTC)))
    store.save_report(make_report([make_result()], datetime(2026, 1, 2, tzinfo=UTC)))
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 2


def test_corrupt_database_raises_history_error(tmp_path):
    path = tmp_path / "history.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(HistoryError):
        HistoryStore(path).recent()


def test_unwritable_location_raises_history_error(tmp_path, make_result, make_report):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(HistoryError):
        HistoryStore(blocker / "history.db").save_report(make_report([make_result()]))
