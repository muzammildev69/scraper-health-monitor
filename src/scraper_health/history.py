"""Optional run history stored in a local SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scraper_health.models import HistoryError, Report, ResultCategory

DEFAULT_DB_PATH = Path(".scraper-health") / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,
    response_time REAL,
    result TEXT NOT NULL,
    error TEXT,
    health_score INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checks_checked_at ON checks (checked_at);
CREATE INDEX IF NOT EXISTS idx_checks_url ON checks (url);
"""

_INSERT = """
INSERT INTO checks
    (checked_at, url, status_code, response_time, result, error, health_score)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_RECENT = """
SELECT checked_at, url, status_code, response_time, result, error, health_score
FROM checks
ORDER BY checked_at DESC, id ASC
LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    """One stored check. ``health_score`` is the score of the run it belongs to."""

    checked_at: datetime
    url: str
    status_code: int | None
    response_time: float | None
    result: ResultCategory
    error: str | None
    health_score: int


class HistoryStore:
    """Reads and writes check history in a SQLite file.

    The file and its parent directories are created on first save. Reading a
    database that does not exist yields no records and creates nothing.
    """

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)

    def save_report(self, report: Report) -> int:
        """Store one row per result and return the number of rows written.

        All rows from a run share the report's UTC timestamp.

        Raises:
            HistoryError: if the database cannot be created or written.
        """
        timestamp = report.generated_at.astimezone(UTC).isoformat()
        rows = [
            (
                timestamp,
                result.url,
                result.status_code,
                result.response_time,
                result.result.value,
                result.error,
                report.summary.health_score,
            )
            for result in report.results
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as connection:
                connection.executescript(_SCHEMA)
                with connection:
                    connection.executemany(_INSERT, rows)
        except (OSError, sqlite3.Error) as exc:
            raise HistoryError(f"Could not save history to {self.path}: {exc}") from exc
        return len(rows)

    def recent(self, limit: int = 20) -> list[HistoryRecord]:
        """Return up to ``limit`` records, newest run first, in input order within a run.

        Raises:
            HistoryError: if the database exists but cannot be read.
        """
        if not self.path.exists():
            return []
        try:
            with closing(sqlite3.connect(self.path)) as connection:
                rows = connection.execute(_SELECT_RECENT, (limit,)).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"Could not read history from {self.path}: {exc}") from exc
        return [
            HistoryRecord(
                checked_at=datetime.fromisoformat(checked_at),
                url=url,
                status_code=status_code,
                response_time=response_time,
                result=ResultCategory(result),
                error=error,
                health_score=health_score,
            )
            for checked_at, url, status_code, response_time, result, error, health_score in rows
        ]
