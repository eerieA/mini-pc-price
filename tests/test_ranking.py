"""Ranking tests for what Phase 3 added: out-of-scope listings and dedup.

Both are partitions of the ranking rather than changes to scoring, so these
tests build listings as dicts and check where each one lands. score() itself is
unchanged and is exercised end to end by report.py against real data.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import out_of_scope  # noqa: E402
import ranking  # noqa: E402
import report  # noqa: E402

RULES = {
    "gates": {"nested_virt": True},
    "requirements": {
        "ram_max_gb": {"min": 64, "else_penalty": 325},
        "cpu_cores": {"min": 6, "else_penalty": 80},
        "storage_nvme": {"min": True, "else_penalty": 40},
    },
    "prefer_shipped_ram_gb": 32,
}
PARTS = {"ram": {"ddr4_sodimm_32gb": 325, "ddr4_udimm_32gb_kit": 150},
         "storage": {"nvme_512gb": None}, "priced_on": "2026-09-22"}


def _config(out=(), sources=None):
    return {
        "rules": RULES, "parts": PARTS, "chassis": {}, "watches": [],
        "overrides": [], "out_of_scope": list(out),
        "sources": sources or {"etek": {"source_adjustment": 40},
                               "itrefurbs": {"source_adjustment": 25}},
    }


def _listing(url, source="etek", price=400.0, key="hp:800-g5-mini:i5-9500t:16gb:256gb",
             parse_ok=1, cores=6, storage_type="nvme"):
    return {
        "url": url, "source_id": source, "title_raw": f"HP EliteDesk {url}",
        "price": price, "in_stock": 1, "observed_at": "2026-09-27T12:00:00+00:00",
        "parse_ok": parse_ok, "parse_notes": None if parse_ok else "no chassis key",
        "canonical_key": key, "chassis_key": "hp-elitedesk-800-g5-mini",
        "cpu": "i5-9500T", "cpu_cores": cores, "nested_virt": 1,
        "ram_gb": 16, "ram_slots": 2, "ram_max_gb": 64,
        "storage_gb": 256, "storage_type": storage_type,
    }


# ── Out of scope ─────────────────────────────────────────────────────────────

def test_out_of_scope_listing_is_neither_ranked_nor_a_parse_failure():
    """The distinction Phase 3 exists to draw. A gaming tower is not a parse
    failure -- reporting it as one daily, forever, would train the reader to
    skip the warning that catches real parser breakage (§8)."""
    tower = _listing("tower", parse_ok=0, key=None)
    entry = {"url": "tower", "reason": "gaming tower"}
    ranked, unparsed, excluded, dismissed = ranking.rank([tower], _config([entry]))
    assert (ranked, unparsed, excluded) == ([], [], [])
    assert dismissed == [(tower, "gaming tower")]


def test_out_of_scope_applies_to_a_listing_that_parses():
    """A hand decision outranks the parser: the list says "not a candidate",
    so a tower that happens to parse must not rank either."""
    listing = _listing("parses")
    _, _, _, dismissed = ranking.rank(
        [listing], _config([{"url": "parses", "reason": "tower"}]))
    assert dismissed == [(listing, "tower")]


def test_listing_not_on_the_list_is_untouched():
    listing = _listing("candidate")
    ranked, _, _, dismissed = ranking.rank(
        [listing], _config([{"url": "other", "reason": "tower"}]))
    assert len(ranked) == 1 and dismissed == []


def test_entry_without_a_reason_is_rejected(tmp_path):
    """An exclusion nobody can explain cannot be re-checked -- and the whole
    risk of this list is a candidate hidden by a stale decision."""
    path = tmp_path / "out_of_scope.yaml"
    path.write_text("out_of_scope:\n  - url: https://x/y\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="reason"):
        out_of_scope.load(path)


def test_entry_without_a_url_is_rejected(tmp_path):
    path = tmp_path / "out_of_scope.yaml"
    path.write_text("out_of_scope:\n  - reason: tower\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="url"):
        out_of_scope.load(path)


def test_real_config_loads():
    root = Path(__file__).resolve().parents[1]
    entries = out_of_scope.load(root / "config" / "out_of_scope.yaml")
    assert all(e["reason"] for e in entries)


# ── Dedup on canonical_key ───────────────────────────────────────────────────

def test_same_configuration_at_two_vendors_is_one_line():
    """§9's diagram: one canonical_key, two vendors, one line in the digest --
    the cheaper effective price leads and the other is carried beneath it."""
    etek = _listing("etek-url", "etek", price=350.0)   # + 650 RAM + 40 src
    itr = _listing("itr-url", "itrefurbs", price=399.0)   # + 650 RAM + 25 src
    ranked, _, _, _ = ranking.rank([itr, etek], _config())
    assert len(ranked) == 1
    price, lead, _, _ = ranked[0]
    assert lead["url"] == "etek-url"
    assert [(p, l["url"]) for p, l in lead["also_at"]] == [(1074.0, "itr-url")]


def test_different_configurations_are_not_collapsed():
    """The case the plan expects to be normal: same chassis, different CPU."""
    a = _listing("a", key="hp:800-g4-mini:i7-8700t:16gb:256gb")
    b = _listing("b", key="hp:800-g4-mini:i5-8500:16gb:256gb")
    ranked, _, _, _ = ranking.rank([a, b], _config())
    assert len(ranked) == 2


def test_qualifier_is_never_folded_under_a_near_miss():
    """Same canonical_key, but a hand override can make one unit qualify and
    the other not. Collapsing across that line would hide a qualifier inside
    the near-miss block, so dedup stays within a block."""
    qualifies = _listing("confirmed", price=500.0, storage_type="nvme")
    misses = _listing("unconfirmed", price=300.0, storage_type="ssd")
    ranked, _, _, _ = ranking.rank([qualifies, misses], _config())
    qualifiers, near_misses = ranking.split(ranked)
    assert [r[1]["url"] for r in qualifiers] == ["confirmed"]
    assert [r[1]["url"] for r in near_misses] == ["unconfirmed"]


def test_duplicate_is_printed_with_its_url():
    """A duplicate folded out of sight is still a listing someone may buy from,
    so it keeps a clickable line of its own."""
    etek = _listing("https://etek/x", "etek", price=350.0)
    itr = _listing("https://itr/x", "itrefurbs", price=399.0)
    text = report.build_report([etek, itr], _config())
    assert "also itrefurbs $1074.00 https://itr/x" in text


# ── Report header with several sources ───────────────────────────────────────

def test_header_counts_every_source():
    """Phase 1's header named listings[0]'s source, which with two vendors
    silently described whichever one happened to sort first."""
    listings = [_listing("e1", "etek"), _listing("i1", "itrefurbs", key=None,
                                                  parse_ok=0)]
    text = report.build_report(listings, _config([{"url": "i1",
                                                   "reason": "tower"}]))
    header = text.splitlines()[0]
    assert "etek 1 (1 ok)" in header
    assert "itrefurbs 1 (0 ok, 1 out of scope)" in header


def test_a_stale_source_is_named_even_when_another_is_fresh():
    """One source failing while the other polls must not hide behind the fresh
    one's timestamp -- that source's prices are as old as its last poll."""
    fresh = _listing("e1", "etek")
    stale = dict(_listing("i1", "itrefurbs", key=None),
                 observed_at="2026-09-20T12:00:00+00:00")
    fresh["observed_at"] = report.datetime.now(report.timezone.utc).isoformat()
    stale_sources = report._stale_sources([fresh, stale])
    assert list(stale_sources) == ["itrefurbs"]


@pytest.mark.parametrize("title, expected", [
    ("Refurbished (Excellent) - HP EliteDesk 800 G4 Desktop Mini w/ Keyboard",
     "HP EliteDesk 800 G4 Desktop Mini w/..."),
    ("Open Box - Asus ROG GM700 58L", "Asus ROG GM700 58L"),
    # eTek's trailing "Refurbished" is not a prefix and must survive.
    ("Lenovo M73 Refurbished", "Lenovo M73 Refurbished"),
])
def test_short_title_drops_the_condition_prefix(title, expected):
    assert report.short_title(title, 40) == expected
