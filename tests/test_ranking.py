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
    "import_adjustment": {"CA": 0, "US": "TBD", "default": "TBD"},
    "gates": {"nested_virt": True},
    "requirements": {
        "ram_max_gb": {"min": 64, "else_penalty": 325},
        "cpu_cores": {"min": 6, "else_penalty": 80},
        "storage_nvme": {"min": True, "else_penalty": 40},
    },
    "prefer_shipped_ram_gb": 32,
}
PARTS = {"ram": {"ddr4_sodimm_32gb": 325, "ddr4_udimm_32gb_kit": 150,
                 "ddr5_sodimm_32gb": 560},
         "storage": {"nvme_512gb": None}, "priced_on": "2026-09-22"}
SELLERS = {"gates": {"min_feedback_percent": 98.0, "min_feedback_score": 100,
                     "returns_accepted": True},
           "fulfillment_adjustment": {"seller_fulfilled": 50},
           "blocked_sellers": {"refurbio": "REFURB.io"}}

CHASSIS = {"hp-elitedesk-800-g5-mini": {"ram_type": "DDR4"},
           "lenovo-m70q-gen5-tiny": {"ram_type": "DDR5"}}

def _config(out=(), sources=None):
    return {
        "rules": RULES, "parts": PARTS, "chassis": CHASSIS, "watches": [],
        "sellers": SELLERS,
        "overrides": [], "out_of_scope": list(out),
        "sources": sources or {"etek": {"source_adjustment": 40},
                               "itrefurbs": {"source_adjustment": 25},
                               "ebay": {"exclude_keywords": ["caddy", "bezel"]}},
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
    ranked, unparsed, excluded, dismissed, _ = ranking.rank([tower], _config([entry]))
    assert (ranked, unparsed, excluded) == ([], [], [])
    assert dismissed == [(tower, "gaming tower")]


def test_out_of_scope_applies_to_a_listing_that_parses():
    """A hand decision outranks the parser: the list says "not a candidate",
    so a tower that happens to parse must not rank either."""
    listing = _listing("parses")
    _, _, _, dismissed, _ = ranking.rank(
        [listing], _config([{"url": "parses", "reason": "tower"}]))
    assert dismissed == [(listing, "tower")]


def test_listing_not_on_the_list_is_untouched():
    listing = _listing("candidate")
    ranked, _, _, dismissed, _ = ranking.rank(
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


# ── RAM upgrade priced by the chassis's memory type ─────────────────────────

def test_ddr5_chassis_is_priced_with_the_ddr5_part():
    """M70q Gen 5 takes DDR5. Pricing it with the DDR4 module, as the parser
    did while one key covered every generation, was $470 off for two modules."""
    listing = _listing("gen5")
    listing["chassis_key"] = "lenovo-m70q-gen5-tiny"
    price, terms, _ = ranking.score(listing, _config())
    assert "+ 1120 RAM (2x DDR5 SODIMM)" in terms
    assert price == 400.0 + 1120 + 40


def test_memory_type_without_a_price_raises_rather_than_borrowing_one():
    with pytest.raises(KeyError):
        ranking.ram_upgrade_cost({"ram_gb": 16, "ram_slots": 2}, "DDR3", PARTS)


# ── Dedup on canonical_key ───────────────────────────────────────────────────

def test_same_configuration_at_two_vendors_is_one_line():
    """§9's diagram: one canonical_key, two vendors, one line in the digest --
    the cheaper effective price leads and the other is carried beneath it."""
    etek = _listing("etek-url", "etek", price=350.0)   # + 650 RAM + 40 src
    itr = _listing("itr-url", "itrefurbs", price=399.0)   # + 650 RAM + 25 src
    ranked, _, _, _, _ = ranking.rank([itr, etek], _config())
    assert len(ranked) == 1
    price, lead, _, _ = ranked[0]
    assert lead["url"] == "etek-url"
    assert [(p, l["url"]) for p, l in lead["also_at"]] == [(1074.0, "itr-url")]


def test_different_configurations_are_not_collapsed():
    """The case the plan expects to be normal: same chassis, different CPU."""
    a = _listing("a", key="hp:800-g4-mini:i7-8700t:16gb:256gb")
    b = _listing("b", key="hp:800-g4-mini:i5-8500:16gb:256gb")
    ranked, _, _, _, _ = ranking.rank([a, b], _config())
    assert len(ranked) == 2


def test_qualifier_is_never_folded_under_a_near_miss():
    """Same canonical_key, but a hand override can make one unit qualify and
    the other not. Collapsing across that line would hide a qualifier inside
    the near-miss block, so dedup stays within a block."""
    qualifies = _listing("confirmed", price=500.0, storage_type="nvme")
    misses = _listing("unconfirmed", price=300.0, storage_type="ssd")
    ranked, _, _, _, _ = ranking.rank([qualifies, misses], _config())
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


# ── eBay (plan.md §2, §3, §6) ────────────────────────────────────────────────
# A marketplace listing carries a seller, shipping and an origin; the ranking
# sets aside what is not a candidate -- counted, never dropped -- and holds out
# what is missing an input.

def _ebay(url, price=300.0, blocked=None, rating=99.5, reviews=5000,
          shipping=0.0, ships_from="CA", title=None, **extra):
    listing = _listing(url, "ebay", price=price)
    listing.update(seller=None, seller_blocked=blocked, seller_rating=rating,
                   seller_reviews=reviews, returns_accepted=1,
                   fulfillment="seller_fulfilled", ships_from=ships_from,
                   condition="Used", currency="CAD", shipping=shipping,
                   shipping_estimated=0, price_max=None)
    if title:
        listing["title_raw"] = title
    listing.update(extra)
    return listing


def _set_aside(listings):
    return {e["listing"]["url"]: (e["kind"], e["detail"])
            for e in ranking.rank(listings, _config())[4]}


def test_ebay_listing_pays_seller_shipping_and_ranks():
    """$300 + 650 RAM + 50 seller + 25 ship, CA origin at 0 import."""
    ranked = ranking.rank([_ebay("e", shipping=25.0)], _config())[0]
    price, _, terms, _ = ranked[0]
    assert price == 1025.0
    assert "+ 50 seller" in terms and "+ 25.00 ship" in terms
    assert not any("import" in t for t in terms)


def test_estimated_shipping_is_marked_in_the_arithmetic():
    terms = ranking.rank([_ebay("e", shipping=31.5, shipping_estimated=1)],
                         _config())[0][0][2]
    assert "+ ~31.50 ship" in terms


@pytest.mark.parametrize("listing, kind, detail", [
    (_ebay("k", title="HDD caddy for Dell Optiplex Micro 3080"), "keyword", "caddy"),
    # Plural, and case: "Bezels" is the same part.
    (_ebay("p", title="Dell OptiPlex 3080 Micro Bezels"), "keyword", "bezel"),
    (_ebay("f", condition="For parts or not working"), "for parts",
     "For parts or not working"),
    # Matched at poll time (src/ebay.py); the row keeps only the reason.
    (_ebay("b", blocked="REFURB.io"), "blocked seller", "REFURB.io"),
    (_ebay("r", rating=97.9), "seller gate", "feedback 97.9%"),
    (_ebay("n", reviews=99), "seller gate", "99 reviews"),
    # Missing feedback fails the gate: a missing input never passes as good.
    (_ebay("m", rating=None), "seller gate", "feedback None%"),
    (_ebay("nr", returns_accepted=0), "no returns", "seller accepts none"),
    (_ebay("nu", returns_accepted=None), "no returns", "seller accepts none"),
])
def test_marketplace_filters_set_aside_with_a_reason(listing, kind, detail):
    assert _set_aside([listing]) == {listing["url"]: (kind, detail)}


def test_keyword_matches_whole_words_only():
    """"bezel" must not hide a title that merely contains the letters."""
    assert _set_aside([_ebay("x", title="Dell OptiPlex 3080 Micro rebezeled")]) == {}


def test_import_tbd_holds_out_with_its_score():
    """A US listing waits on import_adjustment rather than ranking at 0 -- the
    flattering default -- and keeps its score so the digest can say what it
    would cost before that term."""
    ranked, _, _, _, set_aside = ranking.rank([_ebay("us", ships_from="US")],
                                              _config())
    assert ranked == []
    [entry] = set_aside
    assert entry["kind"] == "held out"
    assert entry["detail"] == "ships from US, import_adjustment TBD"
    assert entry["row"][0] == 1000.0


def test_unlisted_origin_falls_back_to_default():
    assert _set_aside([_ebay("au", ships_from="AU")])["au"][1] == \
        "ships from AU, import_adjustment TBD"


def test_numeric_import_adjustment_is_charged():
    config = _config()
    config["rules"] = dict(RULES, import_adjustment={"CA": 0, "US": 40,
                                                     "default": "TBD"})
    price, _, terms, _ = ranking.rank([_ebay("us", ships_from="US")], config)[0][0]
    assert price == 1040.0 and "+ 40 import" in terms


def test_non_cad_price_is_held_out_never_converted():
    assert _set_aside([_ebay("usd", currency="USD")])["usd"] == \
        ("held out", "priced in USD")


@pytest.mark.parametrize("ceiling, kind", [(64, "multi-config"),
                                           (32, "multi-config capped")])
def test_multi_configuration_listing_is_never_ranked(ceiling, kind):
    """A price range is not one machine (§6), so it never scores -- shown in
    its own section where the chassis can reach 64 GB, counted where not."""
    listing = _ebay("mc", price=149.99, price_max=439.99, ram_max_gb=ceiling,
                    parse_ok=0)
    assert _set_aside([listing])["mc"][0] == kind


def test_report_counts_set_aside_listings_by_detail():
    listings = [_ebay("k1", title="Optiplex caddy"),
                _ebay("k2", title="Optiplex caddy x2"),
                _ebay("k3", title="Optiplex bezel")]
    text = report.build_report(listings, _config())
    assert "ebay: 3 excluded by keyword (caddy 2, bezel 1)" in text


def test_report_tallies_marketplace_parse_failures_instead_of_listing_them():
    """eBay's unparsed volume would be a hundred warning lines a day. Counted
    by reason, with an unknown chassis named by brand and model number."""
    a = _ebay("a", title="Dell OptiPlex 7080 Micro i5-10500T 16GB 256GB SSD",
              parse_ok=0, parse_notes="no chassis key for title: 'x'")
    b = _ebay("b", title="Dell OptiPlex 7080 Micro i5 10th Gen 8GB 256GB SSD",
              parse_ok=0, parse_notes="no chassis key for title: 'y'; "
                                      "no CPU found in title")
    text = report.build_report([a, b], _config())
    assert "ebay: 2 parse_ok=false, by reason" in text
    assert "2  no chassis key: Dell 7080" in text
    assert "1  no CPU found in title" in text
    assert "parse_ok=false: Dell OptiPlex 7080" not in text


def test_held_out_summary_names_the_cheapest_would_be_qualifier():
    listings = [_ebay("u1", ships_from="US", price=300.0),
                _ebay("u2", ships_from="US", price=200.0)]
    text = report.build_report(listings, _config())
    assert ("2  ships from US, import_adjustment TBD | 2 would qualify, "
            "cheapest $900.00 before that term") in text


# ── The nested_virt gate, end to end ─────────────────────────────────────────

def test_nested_virt_gate_survives_the_database(tmp_path):
    """The gate once passed everything: specs.parse() computed nested_virt, but
    poll.py never copied it into the row and listings had no column for it, so
    ranking read None -- and None is not 0. Each half was tested on its own and
    each was right; only a round trip through the database exercises the seam.

    A test-local CPU table, because no real CPU in cpus.yaml fails the gate."""
    import yaml
    import db
    import poll
    import specs

    config_dir = Path(__file__).resolve().parents[1] / "config"
    read = lambda n: yaml.safe_load((config_dir / n).read_text(encoding="utf-8"))
    cpus = {"i5-9500T": {"cores": 6, "threads": 6, "nested_virt": False}}
    title = "HP EliteDesk 800 G5 Mini i5-9500T 16GB 256GB NVMe SSD"
    parsed = specs.parse(title, "", read("chassis.yaml"), cpus,
                         read("chassis_aliases.yaml"))
    assert parsed["parse_ok"]

    conn = db.connect(tmp_path / "t.db")
    row = poll.listing_row(parsed, [], "etek", "https://x/1", title,
                           report.datetime.now(report.timezone.utc))
    listing_id = db.upsert_listing(conn, row)
    db.insert_observation(conn, listing_id, "2026-10-01T12:00:00+00:00",
                          400.0, None, True)
    [stored] = db.fetch_current(conn)

    ranked, _, excluded, _, _ = ranking.rank([stored], _config())
    assert ranked == []
    assert [(l["url"], why) for l, why in excluded] == \
        [("https://x/1", "no nested virtualisation")]


def test_unquoted_shipping_is_held_out_not_charged_zero():
    """eBay could not quote shipping to Canada (21 of 161 in the first live
    search). Ranking it at 0 would flatter it; it waits instead."""
    assert _set_aside([_ebay("s", shipping=None)])["s"] ==         ("held out", "shipping not quoted")


def test_refurbisher_without_shipping_still_ranks():
    """The Shopify sources state no shipping and are charged 0 (§2) -- the
    held-out rule is for marketplace listings only."""
    ranked = ranking.rank([_listing("r")], _config())[0]
    assert [r[1]["url"] for r in ranked] == ["r"]
