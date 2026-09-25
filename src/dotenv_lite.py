"""Read a `.env` file into the environment (plan.md §6).

Fifteen lines instead of a dependency. `python-dotenv` does more than this --
interpolation, multi-line values, encoding detection -- and none of it is needed
for three variables in a single-user tool whose §1 argument is that it should
stay two scripts and no machinery.

The one rule worth stating: **the real environment always wins.** A `.env` left
behind in the working directory must not override a scheduled task that sets its
variables properly, because that failure is invisible -- the digest would send
from the wrong account, or not at all, with nothing pointing at the stale file.
"""

import os
from pathlib import Path

QUOTES = ("'", '"')


def parse(path):
    """Read `path` into a dict. Raises SystemExit on a malformed line."""
    values = {}
    text = Path(path).read_text(encoding="utf-8")

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        if "=" not in line:
            # Never echo the line. A malformed entry is most likely a password
            # whose key was typed wrong, and printing it puts the secret in the
            # terminal, the scrollback and any log capturing stdout.
            raise SystemExit(
                f"{Path(path).name} line {number}: expected KEY=value. "
                f"(The line is not shown -- it may contain a password.)"
            )

        key, _, value = line.partition("=")   # partition, not split: a value
        value = value.strip()                 # may itself contain '='
        if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
            value = value[1:-1]
        values[key.strip()] = value

    return values


def load(path):
    """Load `path` into os.environ without overriding what is already set.

    Returns True if a file was read. A missing file is the normal production
    case, not an error.
    """
    path = Path(path)
    if not path.exists():
        return False

    for key, value in parse(path).items():
        os.environ.setdefault(key, value)
    return True
