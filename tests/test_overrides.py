"""Override application tests (config/listing_overrides.yaml).

An override is a hand-entered claim that outranks observed data. The tests that
matter are the ones that keep it narrow: it must not touch a listing it does not
name, must not invent fields, and must be visible in the output when it fires.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import overrides  # noqa: E402

URL = "https://shop.ca/products/a"
OTHER = "https://shop.ca/products/b"


def _entry(**kwargs):
    base = {"url": URL, "asked": "2026-09-24", "via": "vendor email"}
    base.update(kwargs)
    return [base]


def test_applies_the_named_field():
    listing = {"url": URL, "storage_type": "ssd"}
    assert overrides.apply(listing, _entry(storage_type="nvme")) is True
    assert listing["storage_type"] == "nvme"


def test_leaves_other_listings_alone():
    listing = {"url": OTHER, "storage_type": "ssd"}
    assert overrides.apply(listing, _entry(storage_type="nvme")) is False
    assert listing["storage_type"] == "ssd"


def test_provenance_keys_are_not_written_onto_the_listing():
    """`via` and `asked` describe the claim, not the machine."""
    listing = {"url": URL, "storage_type": "ssd"}
    overrides.apply(listing, _entry(storage_type="nvme", note="x"))
    assert set(listing) == {"url", "storage_type"}


def test_unknown_field_is_rejected():
    """A typo'd key would otherwise be a silent no-op -- the override sits in
    the file looking applied and changes nothing."""
    listing = {"url": URL, "storage_type": "ssd"}
    with pytest.raises(SystemExit, match="storag_type"):
        overrides.apply(listing, _entry(storag_type="nvme"))


def test_override_without_provenance_is_rejected():
    listing = {"url": URL, "storage_type": "ssd"}
    with pytest.raises(SystemExit, match="via"):
        overrides.apply(listing, [{"url": URL, "storage_type": "nvme"}])


def test_entry_that_changes_nothing_is_rejected():
    """An override matching the parsed value means the vendor now states it, or
    it was never needed. Either way it should be deleted, not left to rot."""
    listing = {"url": URL, "storage_type": "nvme"}
    with pytest.raises(SystemExit, match="already"):
        overrides.apply(listing, _entry(storage_type="nvme"))


def test_load_returns_empty_for_a_missing_file(tmp_path):
    assert overrides.load(tmp_path / "absent.yaml") == []


def test_load_rejects_an_entry_without_a_url(tmp_path):
    path = tmp_path / "o.yaml"
    path.write_text("overrides:\n  - storage_type: nvme\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="url"):
        overrides.load(path)


def test_real_config_targets_listings_that_exist(tmp_path):
    """The shipped file names two eTek URLs. A URL typo makes an override that
    never fires and never complains, so check them against the database's own
    listing URLs rather than trusting the strings."""
    import sqlite3
    root = Path(__file__).resolve().parents[1]
    entries = overrides.load(root / "config" / "listing_overrides.yaml")
    if not entries:
        pytest.skip("no overrides configured")

    db_path = root / "data" / "tracker.db"
    if not db_path.exists():
        pytest.skip("no database yet")
    conn = sqlite3.connect(db_path)
    known = {u for (u,) in conn.execute("SELECT url FROM listings")}
    conn.close()

    for entry in entries:
        assert entry["url"] in known, f"override targets unknown URL: {entry['url']}"
