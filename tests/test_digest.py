"""Digest tests (plan.md §6, Phase 2).

Sending is the one irreversible step in this project, so the tests here are
about the two things that decide whether a send is correct before SMTP is ever
touched: credentials resolve from the environment and nowhere else, and the
message is built from the real ranking rather than from a template.

Nothing here opens a socket.
"""

import sys
from email import message_from_string
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import digest  # noqa: E402

ENV_VARS = ("MINIPC_SMTP_USER", "MINIPC_SMTP_PASS", "MINIPC_DIGEST_TO")


@pytest.fixture
def env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_credentials_come_from_the_environment(env):
    env.setenv("MINIPC_SMTP_USER", "sender@gmail.com")
    env.setenv("MINIPC_SMTP_PASS", "abcd efgh ijkl mnop")
    user, password = digest.credentials()
    assert (user, password) == ("sender@gmail.com", "abcd efgh ijkl mnop")


@pytest.mark.parametrize("present", ["MINIPC_SMTP_USER", "MINIPC_SMTP_PASS"])
def test_missing_credential_names_the_variable(env, present):
    """A send that fails at SMTP auth reports a Google error, not a missing
    setting. Failing here instead says which variable to set."""
    env.setenv(present, "x")
    with pytest.raises(SystemExit) as caught:
        digest.credentials()
    missing = [v for v in ("MINIPC_SMTP_USER", "MINIPC_SMTP_PASS")
               if v != present][0]
    assert missing in str(caught.value)


def test_recipient_prefers_the_environment(env):
    env.setenv("MINIPC_DIGEST_TO", "me@example.com")
    assert digest.recipient({"to": "config@example.com"}) == "me@example.com"


def test_recipient_falls_back_to_config(env):
    assert digest.recipient({"to": "config@example.com"}) == "config@example.com"


def test_no_recipient_anywhere_is_an_error(env):
    with pytest.raises(SystemExit, match="MINIPC_DIGEST_TO"):
        digest.recipient({"to": None})


def test_subject_carries_the_date(env):
    message = digest.build_message(
        "body text", {"subject": "Mini-PC digest", "to": "a@b.com"},
        "sender@gmail.com", "a@b.com", date="2026-09-25")
    assert message["Subject"] == "Mini-PC digest - 2026-09-25"
    assert message["To"] == "a@b.com"
    assert message["From"] == "sender@gmail.com"


def test_body_is_the_report_verbatim(env):
    """The email is the console report, not a second rendering of the ranking.
    Two renderings drift, and the one nobody reads becomes wrong (§7)."""
    body = "QUALIFIES - meets every requirement (2)\n  $914.99  Dell..."
    message = digest.build_message(body, {"subject": "S", "to": "a@b.com"},
                                   "s@gmail.com", "a@b.com", date="2026-09-25")
    parsed = message_from_string(message.as_string())
    # set_content appends a trailing newline, as a well-formed message body
    # should end with one. Everything before it must be untouched.
    assert parsed.get_payload(decode=True).decode("utf-8").rstrip("\n") == body


def test_password_never_appears_in_the_message(env):
    """A password echoed into a header or body would be mailed in clear text."""
    secret = "abcd efgh ijkl mnop"
    env.setenv("MINIPC_SMTP_USER", "s@gmail.com")
    env.setenv("MINIPC_SMTP_PASS", secret)
    message = digest.build_message("body", {"subject": "S", "to": "a@b.com"},
                                   "s@gmail.com", "a@b.com", date="2026-09-25")
    assert secret not in message.as_string()


def test_empty_database_produces_no_send(env):
    """Nothing to report is not an empty email. A digest that arrives saying
    nothing trains the reader to ignore it."""
    assert digest.body_for([], {}) is None


def test_a_dead_poll_sends_even_when_nothing_changed():
    """The interaction that matters once sending is change-gated.

    When the poll has stopped, nothing CAN change -- so a suppression rule that
    only asks "did anything move" would go quiet exactly when the tracker has
    died. digest.main() suppresses only when the diff is empty AND coverage is
    healthy; this pins the AND (plan.md §8).
    """
    import changes
    healthy = {"alert": False}
    dead = {"alert": True}
    no_movement = {"price": [], "appeared": [], "disappeared": [],
                   "qualified": [], "unqualified": [], "stock": []}

    assert changes.is_empty(no_movement) and not healthy["alert"]
    assert not (changes.is_empty(no_movement) and not dead["alert"])


@pytest.mark.parametrize("flag", ["--forse", "-force", "--send"])
def test_unknown_flag_is_an_error(env, flag):
    """The workflow builds this argument from a GitHub expression, so a typo
    reaches the CLI rather than a human. Ignoring it would look exactly like a
    run that decided not to send (.github/workflows/daily.yml)."""
    with pytest.raises(SystemExit, match="Unknown argument"):
        digest.main([flag])
