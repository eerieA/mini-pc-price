"""SQLite -> filter -> rank -> email (plan.md §6, Phase 2).

    python src/digest.py            send it
    python src/digest.py --dry-run  print what would be sent, send nothing

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
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import yaml

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


def body_for(listings, config):
    """The digest body, or None when there is nothing worth sending.

    An empty database is not an empty email. A digest that arrives saying
    nothing every morning trains its one reader to stop opening it, which costs
    more than the missed day (§6).
    """
    if not listings:
        return None
    return report.build_report(listings, config)


def build_message(body, settings, sender, to, date=None):
    message = EmailMessage()
    message["Subject"] = f"{settings['subject']} - {date}"
    message["From"] = sender
    message["To"] = to
    message.set_content(body)
    return message


def send(message, user, password, smtp):
    with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(message)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in argv

    # Local convenience only, and it never overrides a variable already set --
    # a stale .env silently beating a scheduled task's real environment is the
    # failure this ordering exists to prevent (src/dotenv_lite.py).
    if dotenv_lite.load(ROOT / ".env"):
        print("Loaded .env")

    settings = load_settings()
    conn = db.connect(ROOT / "data" / "tracker.db")
    listings = db.fetch_current(conn)
    conn.close()

    body = body_for(listings, ranking.load_config())
    if body is None:
        print("No listings in the database; nothing sent. Run poll.py first.")
        return 1

    if dry_run:
        # Resolve the recipient but not the credentials: --dry-run has to work
        # on a machine where the password was never set, which is the machine
        # someone is most likely to be debugging the output on.
        print(f"--dry-run: would send to {recipient(settings)}\n")
        sys.stdout.reconfigure(encoding="ascii", errors="replace")
        print(body)
        return 0

    user, password = credentials()
    to = recipient(settings)
    message = build_message(body, settings, user, to,
                            date=date.today().isoformat())
    send(message, user, password, settings["smtp"])
    print(f"Sent to {to} ({len(listings)} listings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
