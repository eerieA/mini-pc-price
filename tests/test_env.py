"""`.env` loading tests (plan.md §6).

The file holds a real Gmail app password on a developer machine, so the tests
that matter are the ones that keep it narrow: the real environment wins, a
malformed line is not silently swallowed, and nothing is logged.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dotenv_lite  # noqa: E402


def test_reads_simple_pairs(tmp_path):
    path = tmp_path / ".env"
    path.write_text("MINIPC_SMTP_USER=me@gmail.com\nMINIPC_DIGEST_TO=you@x.ca\n",
                    encoding="utf-8")
    assert dotenv_lite.parse(path) == {
        "MINIPC_SMTP_USER": "me@gmail.com",
        "MINIPC_DIGEST_TO": "you@x.ca",
    }


def test_value_may_contain_spaces_and_equals(tmp_path):
    """A Google app password is shown in four space-separated groups, and
    splitting on every '=' would truncate a value that contains one."""
    path = tmp_path / ".env"
    path.write_text("MINIPC_SMTP_PASS=abcd efgh ijkl mnop\nOTHER=a=b=c\n",
                    encoding="utf-8")
    parsed = dotenv_lite.parse(path)
    assert parsed["MINIPC_SMTP_PASS"] == "abcd efgh ijkl mnop"
    assert parsed["OTHER"] == "a=b=c"


@pytest.mark.parametrize("line, expected", [
    ('KEY="quoted value"', "quoted value"),
    ("KEY='single'", "single"),
    ("KEY=  padded  ", "padded"),
    ("export KEY=shell style", "shell style"),
])
def test_quotes_and_padding_are_stripped(tmp_path, line, expected):
    path = tmp_path / ".env"
    path.write_text(line + "\n", encoding="utf-8")
    assert dotenv_lite.parse(path)["KEY"] == expected


def test_comments_and_blank_lines_are_ignored(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# a comment\n\n   \nKEY=value\n", encoding="utf-8")
    assert dotenv_lite.parse(path) == {"KEY": "value"}


def test_a_line_without_equals_is_an_error(tmp_path):
    """Silently skipping it means the variable is unset for a reason the file
    does not show -- the failure then surfaces as a Gmail auth error."""
    path = tmp_path / ".env"
    path.write_text("MINIPC_SMTP_PASS abcd efgh\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        dotenv_lite.parse(path)
    assert "line 1" in str(caught.value)


def test_error_message_never_quotes_the_line(tmp_path):
    """The malformed line may be a password with a typo'd key. Naming the line
    number is enough to fix it; echoing it puts the secret in the terminal."""
    path = tmp_path / ".env"
    path.write_text("MINIPC_SMTP_PASS abcd-secret-efgh\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        dotenv_lite.parse(path)
    assert "abcd-secret-efgh" not in str(caught.value)


def test_real_environment_wins(tmp_path, monkeypatch):
    """A stale .env in the working directory must not override a scheduled
    task's properly-set variables."""
    monkeypatch.setenv("MINIPC_SMTP_USER", "real@gmail.com")
    path = tmp_path / ".env"
    path.write_text("MINIPC_SMTP_USER=stale@gmail.com\nNEW_ONE=set\n",
                    encoding="utf-8")
    dotenv_lite.load(path)
    import os
    assert os.environ["MINIPC_SMTP_USER"] == "real@gmail.com"
    assert os.environ["NEW_ONE"] == "set"


def test_missing_file_is_not_an_error(tmp_path):
    """Running without a .env is the normal case in production."""
    assert dotenv_lite.load(tmp_path / "absent") is False


def test_example_file_carries_no_real_password():
    """.env.example is committed. A real value pasted in here would ship."""
    root = Path(__file__).resolve().parents[1]
    parsed = dotenv_lite.parse(root / ".env.example")
    # Every value must still be a placeholder: all-x for the password, and the
    # documented example address for the two email fields.
    assert set(parsed["MINIPC_SMTP_PASS"].replace(" ", "")) == {"x"}
    assert parsed["MINIPC_SMTP_USER"] == "you@gmail.com"
    assert parsed["MINIPC_DIGEST_TO"] == "you@gmail.com"
