"""Poll coverage tests (plan.md §8).

Coverage is what keeps a change-gated digest honest. Once mail only arrives when
something moved, a poll that has stopped working produces silence -- which is
also what a quiet week produces. These tests pin the distinction.

Coverage is computed from the observation log rather than from a success flag,
so the fixtures here write observations directly: a poller that dies before
writing has no opinion about whether it ran.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import coverage  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "t.db")
    connection.execute(
        "INSERT INTO listings (source_id, url, title_raw, first_seen, "
        "last_seen, parse_ok) VALUES ('etek', 'https://x/a', 'A', '', '', 1)")
    return connection


def observe(conn, days_ago):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    conn.execute(
        "INSERT INTO observations (listing_id, observed_at, price, in_stock) "
        "VALUES (1, ?, 100.0, 1)", (when.isoformat(timespec="seconds"),))


def test_empty_database_reports_never_polled(conn):
    status = coverage.report(conn)
    assert status["last_polled"] is None
    assert "never succeeded" in "\n".join(coverage.lines(status))


def test_unbroken_run_has_no_gaps(conn):
    for day in range(30):
        observe(conn, day)
    status = coverage.report(conn, days=30)
    assert status["missing"] == []
    assert status["silent_days"] == 0
    assert status["alert"] is False


def test_gaps_are_listed(conn):
    for day in (0, 1, 3, 4):
        observe(conn, day)
    status = coverage.report(conn, days=5)
    assert status["polled"] == 4
    assert len(status["missing"]) == 1


def test_a_silent_poll_raises_an_alert(conn):
    """The case the whole module exists for: mail stopped arriving and the
    reason is a dead poll, not a quiet market."""
    observe(conn, coverage.ALERT_AFTER_SILENT_DAYS + 1)
    status = coverage.report(conn)
    assert status["alert"] is True
    rendered = "\n".join(coverage.lines(status))
    assert "HAS NOT RUN" in rendered


def test_one_missed_day_is_not_an_alert(conn):
    """A closed laptop or a queued runner is normal. Alerting on it would train
    the reader to ignore the line that matters."""
    observe(conn, 1)
    assert coverage.report(conn)["alert"] is False


def test_coverage_prints_even_when_healthy(conn):
    """Always shown, so the number can be trusted. A line that appears only on
    failure is one the reader learns to skim past."""
    for day in range(5):
        observe(conn, day)
    rendered = "\n".join(coverage.lines(coverage.report(conn, days=5)))
    assert "polled 5 of the last 5 days" in rendered


def test_coverage_lines_are_ascii(conn):
    observe(conn, 10)
    "\n".join(coverage.lines(coverage.report(conn))).encode("ascii")
