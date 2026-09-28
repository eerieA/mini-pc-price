"""SQLite -> filter -> rank -> email (plan.md §6, Phase 2).

    python src/digest.py            send it, if anything moved
    python src/digest.py --force    send it regardless
    python src/digest.py --dry-run  print what would be sent, send nothing

Sends only when something moved since the last digest, or when the poll has gone
quiet (src/changes.py, src/coverage.py). Daily mail about ten SKUs that sit still
for weeks is how a digest trains its reader to ignore it (§6).

The body is `report.py`'s output verbatim. That is deliberate: the digest and
the console are one rendering with two destinations, and a second rendering of
the same ranking would drift until the one nobody reads became wrong (§7). The
shared filter-and-rank step lives in ranking.py.

Credentials are read from the environment and from nowhere else -- config/*.yaml
is committed. A git-ignored `.env` in the project root is loaded for local
testing (see `.env.example`); real environment variables always win over it.
"""

import os
import smtplib
import sys
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import yaml

import changes
import coverage
import db
import dotenv_lite
import ranking
import report

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"

SMTP_USER = "MINIPC_SMTP_USER"
SMTP_PASS = "MINIPC_SMTP_PASS"
DIGEST_TO = "MINIPC_DIGEST_TO"


def load_settings():
    return yaml.safe_load((CONFIG / "digest.yaml").read_text(encoding="utf-8"))


def credentials():
    """(user, password) from the environment, or exit naming what is missing.

    Exiting here rather than at SMTP auth matters: Gmail answers a missing
    password with a generic authentication failure, which reads like a wrong
    password rather than an unset variable.
    """
    user, password = os.environ.get(SMTP_USER), os.environ.get(SMTP_PASS)
    missing = [name for name, value in ((SMTP_USER, user), (SMTP_PASS, password))
               if not value]
    if missing:
        raise SystemExit(
            f"{' and '.join(missing)} not set. The Gmail app password is read "
            f"from the environment because config/*.yaml is committed -- see "
            f"config/digest.yaml for how to set it."
        )
    return user, password


def recipient(settings):
    """Where the digest goes. Environment wins over config."""
    address = os.environ.get(DIGEST_TO) or settings.get("to")
    if not address:
        raise SystemExit(
            f"No recipient: set {DIGEST_TO} or `to:` in config/digest.yaml."
        )
    return address


def body_for(listings, config, prefix=(), ranked=None):
    """The digest body, or None when there is nothing worth sending.

    An empty database is not an empty email. A digest that arrives saying
    nothing every morning trains its one reader to stop opening it, which costs
    more than the missed day (§6).

    `prefix` is the what-moved and coverage blocks, above the ranking because
    they are why this particular email exists today.
    """
    if not listings:
        return None
    return "\n".join([*prefix, report.build_report(listings, config, ranked)])


def build_message(body, settings, sender, to, date=None):
    message = EmailMessage()
    message["Subject"] = f"{settings['subject']} - {date}"
    message["From"] = sender
    message["To"] = to
    message.set_content(body)
    return message


def send(message, user, password, smtp):
    """Deliver, or exit non-zero with a line saying which step failed.

    Scheduled, this is the difference between a visible failure and an invisible
    one: an unhandled traceback in a log nobody opens looks exactly like a quiet
    morning with no new deals, which is the silent-breakage pattern §8 calls the
    main ongoing cost. Each failure below is a different fix, so each says so.
    """
    try:
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        # Never echo the password, not even a length. This is the most likely
        # failure of the four and the only one that is a credentials problem:
        # Gmail rejects an account password here, and requires 2FA before an
        # app password can exist at all.
        raise SystemExit(
            f"SMTP rejected the login for {user}. {SMTP_PASS} must be a 16-"
            f"character Google App Password, not the account password, and the "
            f"account needs 2-Step Verification enabled for one to be issued."
        )
    except (smtplib.SMTPException, OSError) as error:
        # OSError covers the network: DNS, refused connection, timeout. Grouped
        # with SMTPException because the response is identical -- the digest did
        # not go out, tomorrow's run will try again, and no data was lost.
        # Gmail answers an unknown account by dropping the connection rather
        # than returning an auth error, so a credentials problem can arrive
        # here too -- hence the pointer, which costs one line and saves
        # debugging the network when the password is what is wrong.
        raise SystemExit(
            f"Could not send via {smtp['host']}:{smtp['port']} -- "
            f"{type(error).__name__}: {error}\n"
            f"If the network is fine, check {SMTP_USER} and {SMTP_PASS}: Gmail "
            f"closes the connection on an unknown account."
        )


KNOWN_FLAGS = {"--dry-run", "--force"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    # An unrecognised flag is an error, not something to ignore. The workflow
    # builds this argument from an expression (.github/workflows/daily.yml), and
    # a flag that silently does nothing would look exactly like a successful run
    # that decided not to send -- which is the failure this project keeps
    # finding in other forms (§8).
    unknown = [a for a in argv if a not in KNOWN_FLAGS]
    if unknown:
        raise SystemExit(f"Unknown argument(s): {' '.join(unknown)}. "
                         f"Valid flags: {', '.join(sorted(KNOWN_FLAGS))}.")

    dry_run = "--dry-run" in argv

    # Local convenience only, and it never overrides a variable already set --
    # a stale .env silently beating a scheduled task's real environment is the
    # failure this ordering exists to prevent (src/dotenv_lite.py).
    if dotenv_lite.load(ROOT / ".env"):
        print("Loaded .env")

    settings = load_settings()
    conn = db.connect(ROOT / "data" / "tracker.db")
    listings = db.fetch_current(conn)
    config = ranking.load_config()

    # Ranked exactly once, here, and the result is reused. ranking.rank()
    # applies overrides by mutating the listing dicts, so a second call on the
    # same objects sees a value the first call already wrote and reports the
    # override as redundant -- correctly, which is why this is fixed by not
    # ranking twice rather than by relaxing that check (src/overrides.py).
    ranking_result = ranking.rank(listings, config)
    qualifiers, _ = ranking.split(ranking_result[0])
    # A duplicate folded under a qualifier qualifies too -- collapse_duplicates
    # never folds across the qualifier/near-miss line.
    qualifying_urls = {l["url"] for _, lead, _, _ in qualifiers
                       for l in [lead, *(o for _, o in lead["also_at"])]}

    # Out-of-scope listings are left out of the comparison on both sides. A
    # gaming tower's price move is not a reason to send, and dropping one from
    # only the current side would report it as "gone" the day it was dismissed.
    dismissed = {l["url"] for l, _ in ranking_result[3]}
    coverage_status = coverage.report(conn)
    previous = changes.load_previous(conn)
    if previous is not None:
        previous["listings"] = {url: v for url, v in previous["listings"].items()
                                if url not in dismissed}
    current = changes.snapshot([l for l in listings if l["url"] not in dismissed],
                               qualifying_urls)
    moved = changes.diff(previous, current)

    # A dead poll must not be silenced by "nothing changed" -- when nothing is
    # being fetched, nothing CAN change, so the suppression rule would hide
    # exactly the failure it most matters to report (§8).
    nothing_to_say = changes.is_empty(moved) and not coverage_status["alert"]
    if nothing_to_say and not ("--force" in argv or dry_run):
        print(f"No change since {previous['sent_at'][:16]}; nothing sent. "
              f"(--force to send anyway)")
        conn.close()
        return 0

    prefix = [*changes.format_changes(moved, previous),
              *coverage.lines(coverage_status)]
    body = body_for(listings, config, prefix, ranking_result)
    if body is None:
        print("No listings in the database; nothing sent. Run poll.py first.")
        conn.close()
        return 1

    if dry_run:
        # Resolve the recipient but not the credentials: --dry-run has to work
        # on a machine where the password was never set, which is the machine
        # someone is most likely to be debugging the output on.
        print(f"--dry-run: would send to {recipient(settings)}\n")
        sys.stdout.reconfigure(encoding="ascii", errors="replace")
        print(body)
        conn.close()
        return 0

    user, password = credentials()
    to = recipient(settings)
    message = build_message(body, settings, user, to,
                            date=date.today().isoformat())
    send(message, user, password, settings["smtp"])

    # Only now. Recording before the send would mean a transient SMTP failure
    # silently consumed the movement it was meant to report.
    changes.record_sent(conn, datetime.now(timezone.utc).isoformat(
        timespec="seconds"), current)
    conn.commit()
    conn.close()
    print(f"Sent to {to} ({len(listings)} listings).")
    return 0




if __name__ == "__main__":
    sys.exit(main())
