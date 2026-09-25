"""Change detection tests (plan.md §6).

Two failure modes matter here and they are opposites, so both are tested
directly rather than through the digest:

  - a change that does not send is a deal you never heard about
  - an absence of change that sends is the daily noise §6 exists to prevent

The third, subtler one: a suppression rule that also suppresses "the poll is
dead" would hide the failure it most matters to report. That interaction is
tested in test_digest.py, where both inputs meet.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import changes  # noqa: E402


def listing(url="https://x/products/a", price=100.0, in_stock=True,
            title="Dell OptiPlex 3080 Micro i5-10500T 32GB"):
    return {"url": url, "price": price, "in_stock": int(in_stock),
            "title_raw": title}


def snap(*listings, qualifying=()):
    return changes.snapshot(list(listings), set(qualifying))


def previous(snapshot, sent_at="2026-09-20T08:00:00"):
    return {"sent_at": sent_at, "listings": snapshot}


def test_never_sent_returns_none_not_empty():
    """None and "nothing changed" must not be confused: a fresh checkout has
    no previous digest, and treating that as no-change would sit silent until
    a price happened to move."""
    assert changes.diff(None, snap(listing())) is None
    assert changes.is_empty(None) is False


def test_identical_snapshots_are_empty():
    current = snap(listing())
    assert changes.is_empty(changes.diff(previous(current), current)) is True


def test_price_change_carries_both_ends_and_the_delta():
    before = snap(listing(price=549.99))
    after = snap(listing(price=499.99))
    moved = changes.diff(previous(before), after)["price"]
    assert len(moved) == 1
    assert (moved[0]["was"], moved[0]["now"]) == (549.99, 499.99)
    assert moved[0]["delta"] == pytest.approx(-50.0)


def test_a_cent_is_a_change():
    """Not a tolerance question. A vendor moving a price at all is a signal,
    and rounding one away would be a silent editorial decision."""
    before = snap(listing(price=100.00))
    after = snap(listing(price=100.01))
    assert changes.is_empty(changes.diff(previous(before), after)) is False


def test_new_listing_appears():
    before = snap(listing(url="https://x/products/a"))
    after = snap(listing(url="https://x/products/a"),
                 listing(url="https://x/products/b"))
    result = changes.diff(previous(before), after)
    assert [i["url"] for i in result["appeared"]] == ["https://x/products/b"]
    assert result["price"] == []


def test_removed_listing_disappears_with_its_last_price():
    """The price is carried because the listing is gone -- there is nowhere
    else left to read what it cost."""
    before = snap(listing(url="https://x/products/a", price=429.99))
    after = snap(listing(url="https://x/products/b"))
    gone = changes.diff(previous(before), after)["disappeared"]
    assert len(gone) == 1 and gone[0]["price"] == 429.99


def test_newly_qualifying_is_tracked_apart_from_its_price():
    """The most important thing this project can report. A price move usually
    causes it, but the two are separate lines because one is a number and the
    other is an answer to the buying question."""
    url = "https://x/products/a"
    before = snap(listing(url=url, price=600.0))
    after = snap(listing(url=url, price=600.0), qualifying=[url])
    result = changes.diff(previous(before), after)
    assert len(result["qualified"]) == 1
    assert result["price"] == []


def test_losing_qualification_is_reported_too():
    url = "https://x/products/a"
    before = snap(listing(url=url), qualifying=[url])
    after = snap(listing(url=url))
    result = changes.diff(previous(before), after)
    assert len(result["unqualified"]) == 1 and result["qualified"] == []


def test_stock_change_is_a_change():
    before = snap(listing(in_stock=True))
    after = snap(listing(in_stock=False))
    result = changes.diff(previous(before), after)
    assert len(result["stock"]) == 1
    assert result["stock"][0]["in_stock"] is False


def test_snapshot_is_keyed_on_url_not_listing_id():
    """The snapshot is written into a database that travels between a CI runner
    and a desktop. A listing id is local to one database; the URL is what §4
    keys on and the only identifier that survives the trip."""
    current = snap(listing(url="https://x/products/a"))
    assert list(current) == ["https://x/products/a"]


def test_first_digest_says_so_rather_than_listing_everything_as_new():
    rendered = "\n".join(changes.format_changes(None, None))
    assert "first digest" in rendered.lower()


def test_rendered_changes_are_ascii():
    """The console prints this too, and a Windows console is cp1252 -- a single
    arrow glyph raises UnicodeEncodeError and kills the run (§9)."""
    url = "https://x/products/a"
    before = snap(listing(url=url, price=200.0))
    after = snap(listing(url=url, price=150.0, in_stock=False),
                 listing(url="https://x/products/new"), qualifying=[url])
    rendered = "\n".join(
        changes.format_changes(changes.diff(previous(before), after),
                               previous(before)))
    rendered.encode("ascii")  # raises if a glyph slipped in


def test_price_lines_are_ordered_biggest_drop_first():
    """A drop is the actionable direction, so the reader should not have to
    scan for it."""
    before = snap(listing(url="https://x/a", price=100.0),
                  listing(url="https://x/b", price=100.0),
                  listing(url="https://x/c", price=100.0))
    after = snap(listing(url="https://x/a", price=110.0),
                 listing(url="https://x/b", price=60.0),
                 listing(url="https://x/c", price=90.0))
    rendered = changes.format_changes(changes.diff(previous(before), after),
                                      previous(before))
    price_lines = [l for l in rendered if "->" in l]
    deltas = [float(l.split("(")[1].split(")")[0]) for l in price_lines]
    assert deltas == sorted(deltas)
