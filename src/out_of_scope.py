"""Listings a human has judged not to be candidates (plan.md §9, Phase 3).

A mixed collection -- ITRefurbs' holds towers, gaming rigs and a workstation
beside its minis -- produces listings that are not parse failures but are not
machines this project is looking for either. Left alone they land in
`parse_ok = false` and are reported as parser breakage every day, which buries
the warning that catches real breakage (§8).

The list is keyed by URL and written by hand, deliberately not a title-keyword
rule. A rule is a guess about many listings at once: ITRefurbs' "ThinkStation
P340 Workstation" is a P340 Tiny, and excluding on "workstation" would have
hidden it without anyone ever deciding to. A URL entry is one decision about
one listing, with its reason next to it.
"""

from pathlib import Path

import yaml


def load(path):
    """Read out_of_scope.yaml. Returns a list of {url, reason}, possibly empty.

    Raises on an entry missing either key: an unexplained exclusion cannot be
    re-checked, and a candidate hidden by a stale one is the failure this list
    risks.
    """
    path = Path(path)
    if not path.exists():
        return []

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = document.get("out_of_scope") or []
    for index, entry in enumerate(entries):
        for key in ("url", "reason"):
            if not isinstance(entry, dict) or not entry.get(key):
                raise SystemExit(
                    f"{path.name}: entry #{index + 1} has no `{key}` -- got "
                    f"{entry!r}"
                )
    return entries


def reason_for(listing, entries):
    """The recorded reason this listing is out of scope, or None."""
    for entry in entries:
        if entry["url"] == listing["url"]:
            return entry["reason"]
    return None
