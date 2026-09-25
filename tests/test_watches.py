"""Watch-URL classification tests (plan.md §9).

The classifier decides whether a hand-seeded URL can be polled now or must be
recorded as pending. Getting it wrong in the permissive direction is the
expensive error: a URL misread as tier 0 fails at fetch time every run, and a
watch that errors on a schedule is the "quietly does nothing" failure §8 warns
about wearing a different hat.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import watches  # noqa: E402


TIER0 = [
    # Real, from sources.yaml's product_url_base.
    "https://www.eteklaptop.ca/products/dell-optiplex-3080-micro",
    # Trailing slash, query string and fragment: all still the same product.
    "https://www.eteklaptop.ca/products/dell-optiplex-3080-micro/",
    "https://www.eteklaptop.ca/products/lenovo-m73?variant=42",
    "https://www.eteklaptop.ca/products/lenovo-m73#specs",
    # A collection-scoped product URL, which is how Shopify links from a
    # collection page. The handle is still the last segment.
    "https://shop.example.ca/collections/mini-pcs/products/hp-800-g4",
    "http://shop.example.ca/products/hp-800-g4",
]

PENDING = [
    # Marketplaces: the case watch_urls.yaml exists to serve, and the case
    # Phase 1 cannot parse.
    "https://www.kijiji.ca/v-desktop-computers/city/dell-optiplex/1234567890",
    "https://www.ebay.ca/itm/123456789012",
    "https://www.amazon.ca/dp/B08XYZ1234",
    # A Shopify STORE, but not a product page.
    "https://www.eteklaptop.ca/collections/tiny-computer-and-mini-pcs-usff",
    "https://www.eteklaptop.ca/",
    # "products" present but not as the path segment introducing a handle.
    "https://www.bestbuy.ca/en-ca/category/products/12345",
    # Plausible-looking but empty handle.
    "https://www.eteklaptop.ca/products/",
]


@pytest.mark.parametrize("url", TIER0)
def test_shopify_product_urls_are_tier0(url):
    assert watches.classify(url)["tier0"] is True


@pytest.mark.parametrize("url", PENDING)
def test_everything_else_is_pending(url):
    assert watches.classify(url)["tier0"] is False


@pytest.mark.parametrize("url, handle", [
    ("https://www.eteklaptop.ca/products/dell-optiplex-3080-micro",
     "dell-optiplex-3080-micro"),
    ("https://www.eteklaptop.ca/products/lenovo-m73?variant=42", "lenovo-m73"),
    ("https://shop.example.ca/collections/mini-pcs/products/hp-800-g4",
     "hp-800-g4"),
])
def test_handle_is_extracted(url, handle):
    assert watches.classify(url)["handle"] == handle


def test_json_url_appends_suffix_to_the_clean_path():
    """The .json URL must drop the query string.

    `/products/x?variant=42.json` is not a URL Shopify answers, and the failure
    is a 404 on every run rather than anything obvious at config time.
    """
    result = watches.classify(
        "https://www.eteklaptop.ca/products/lenovo-m73?variant=42")
    assert result["json_url"] == (
        "https://www.eteklaptop.ca/products/lenovo-m73.json")


def test_pending_url_has_no_json_url():
    assert watches.classify("https://www.ebay.ca/itm/123")["json_url"] is None


def test_pending_reason_names_the_host():
    """The warning has to say which watch is idle, not that some watch is."""
    assert "kijiji.ca" in watches.classify(
        "https://www.kijiji.ca/v-desktop-computers/x/1234")["reason"]


def test_load_accepts_an_empty_file(tmp_path):
    """The committed watch_urls.yaml ships with `watches: []`.

    `yaml.safe_load` returns None for a file that is only comments, and an empty
    list for `watches: []`. Neither is an error, and neither should reach the
    caller as something it has to check.
    """
    empty = tmp_path / "empty.yaml"
    empty.write_text("# only a comment\n", encoding="utf-8")
    assert watches.load(empty) == []

    declared = tmp_path / "declared.yaml"
    declared.write_text("watches: []\n", encoding="utf-8")
    assert watches.load(declared) == []


def test_load_rejects_a_watch_without_a_url(tmp_path):
    """A typo'd key is a silent no-op otherwise, which is the §8 failure."""
    path = tmp_path / "bad.yaml"
    path.write_text("watches:\n  - urls: \"https://x.ca/products/y\"\n",
                    encoding="utf-8")
    with pytest.raises(SystemExit, match="url"):
        watches.load(path)


def test_load_carries_the_note_through(tmp_path):
    path = tmp_path / "noted.yaml"
    path.write_text(
        'watches:\n'
        '  - url: "https://www.ebay.ca/itm/1"\n'
        '    note: "seller says 64GB installed"\n',
        encoding="utf-8")
    watch = watches.load(path)[0]
    assert watch["note"] == "seller says 64GB installed"
