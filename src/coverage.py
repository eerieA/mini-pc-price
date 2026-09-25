"""Did the poll actually run? (plan.md §8).

Once the digest only sends on change, a poll that has stopped working produces
exactly the same thing as a week of steady prices: nothing in the inbox. That is
the silent-breakage failure §8 calls the main ongoing cost, and moving the poll
to a CI runner makes it likelier -- GitHub disables scheduled workflows after 60
days of repo inactivity, and its cron fires late or not at all under load.

So coverage is computed from the observation log itself rather than from a
success flag written by the poller. A poller that dies before writing has no
opinion about whether it ran; the observations either exist or they do not.
"""

from datetime import datetime, timedelta, timezone

# A day with no observation. Not an error on its own -- a laptop was closed, a
# runner was queued -- but a run of them is.
ALERT_AFTER_SILENT_DAYS = 3


def polled_days(conn, days=30):
    """The set of UTC dates in the window that have at least one observation."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows = conn.execute(
        "SELECT DISTINCT substr(observed_at, 1, 10) AS day FROM observations "
        "WHERE substr(observed_at, 1, 10) >= ? ORDER BY day",
        (since,),
    ).fetchall()
    return [r["day"] for r in rows]


def report(conn, days=30):
    """Coverage over the window: which days polled, how long since the last one."""
    polled = polled_days(conn, days)
    today = datetime.now(timezone.utc).date()
    window = [(today - timedelta(days=n)).isoformat() for n in range(days)]
    missing = [d for d in window if d not in set(polled)]

    last = max(polled) if polled else None
    silent_days = None
    if last:
        silent_days = (today - datetime.fromisoformat(last).date()).days

    return {
        "days": days,
        "polled": len(polled),
        "missing": missing,
        "last_polled": last,
        "silent_days": silent_days,
        "alert": silent_days is not None and silent_days >= ALERT_AFTER_SILENT_DAYS,
    }


def lines(status, width=78):
    """The coverage block for the digest. Always printed, not only on failure.

    Printed even when coverage is perfect because the useful message is "the
    history you are about to chart has no holes in it" -- a number you can only
    trust if it is always there. A warning that appears only when something is
    wrong trains the reader to skim past the section that carries it.
    """
    out = ["POLL COVERAGE", "-" * width]
    if status["last_polled"] is None:
        out.append("  no observations at all - the poll has never succeeded here.")
        out.append("")
        return out

    out.append(f"  polled {status['polled']} of the last {status['days']} days"
               f" | last {status['last_polled']}"
               f" | silent {status['silent_days']}d")
    if status["alert"]:
        out.append(f"  ! THE POLL HAS NOT RUN FOR {status['silent_days']} DAYS."
                   f" Prices below are that old.")
        out.append("  ! Check the Actions tab: GitHub disables scheduled runs"
                   " after 60 days of repo inactivity.")
    if status["missing"] and not status["alert"]:
        recent = [d for d in status["missing"][:5]]
        out.append(f"  gaps: {', '.join(recent)}"
                   f"{' ...' if len(status['missing']) > 5 else ''}")
    out.append("")
    return out
