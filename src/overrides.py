"""Vendor-confirmed facts that a listing page does not state.

An override outranks parsed data, which makes it the one place in this project
where a hand-typed value beats an observed one. Everything here exists to keep
that narrow: it applies only to the URL it names, only to fields score() reads,
and only when it actually changes something.

The parsed value is never modified in the database. Overrides are applied to the
row in memory on the way into the report, so the next poll overwrites nothing and
the record of what the page said stays intact.
"""

from pathlib import Path

import yaml

# Fields score() reads. Anything else is a typo or a misunderstanding of what an
# override is for -- it is not an escape hatch for arbitrary listing edits.
OVERRIDABLE = {"storage_type", "cpu_cores", "ram_gb", "ram_max_gb"}

# Describe the claim rather than the machine, so they must not land on the row.
PROVENANCE = {"url", "asked", "answered", "via", "note"}


def load(path):
    """Read listing_overrides.yaml. Returns a list of entries, possibly empty."""
    path = Path(path)
    if not path.exists():
        return []

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = document.get("overrides") or []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("url"):
            raise SystemExit(
                f"{path.name}: override #{index + 1} has no `url` key -- got "
                f"{entry!r}"
            )
    return entries


def apply(listing, entries):
    """Apply any override matching this listing's URL. Returns True if one did.

    Raises rather than warns on a bad entry. An override that silently fails to
    apply is worse than no override: the file says the fact is recorded, the
    ranking says otherwise, and nothing points at the discrepancy.
    """
    applied = False
    for entry in entries:
        if entry["url"] != listing["url"]:
            continue

        fields = {k: v for k, v in entry.items() if k not in PROVENANCE}
        if not fields:
            raise SystemExit(f"override for {entry['url']} sets no fields")
        if not entry.get("via"):
            raise SystemExit(
                f"override for {entry['url']} has no `via` -- an unattributed "
                f"claim that outranks observed data cannot be checked later"
            )

        unknown = set(fields) - OVERRIDABLE
        if unknown:
            raise SystemExit(
                f"override for {entry['url']} sets unknown field(s) "
                f"{sorted(unknown)}; overridable: {sorted(OVERRIDABLE)}"
            )

        for field, value in fields.items():
            if listing.get(field) == value:
                raise SystemExit(
                    f"override for {entry['url']} sets {field}={value!r}, which "
                    f"the listing already says. Delete it -- either the vendor "
                    f"now states this on the page, or it was never needed."
                )
            listing[field] = value
            applied = True
    return applied


def describe(entries, listing_url):
    """The provenance line for a listing, for the report. None if not overridden."""
    for entry in entries:
        if entry["url"] == listing_url:
            fields = sorted(k for k in entry if k not in PROVENANCE)
            when = entry.get("answered") or entry.get("asked") or "date unknown"
            return f"{', '.join(fields)} from {entry.get('via')}, {when}"
    return None
