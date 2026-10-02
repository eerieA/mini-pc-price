"""eBay item -> stored fields: the part that must never keep who the seller is.

The production keyset is enabled under eBay's "I do not persist eBay data"
exemption (src/ebay.py). These tests are what keeps that statement true: a
username must not reach the listings row or the raw file on disk.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ebay  # noqa: E402

ITEM = {
    "legacyItemId": "123456789012",
    "title": "Dell OptiPlex 3080 Micro i5-10500T 16GB 256GB NVMe",
    "price": {"value": "329.99", "currency": "CAD"},
    "condition": "Used",
    "seller": {"username": "Some_Seller", "feedbackPercentage": "99.4",
               "feedbackScore": 3612},
    "itemLocation": {"postalCode": "V6B***", "city": "Vancouver",
                     "country": "CA"},
}
BLOCKED = {"some_seller": "test reason"}


def test_seller_fields_never_carry_the_username():
    fields = ebay.seller(ITEM, {})
    assert fields["seller"] is None
    assert "some_seller" not in json.dumps(fields).lower()
    assert fields["seller_rating"] == 99.4 and fields["seller_reviews"] == 3612
    assert fields["seller_blocked"] is None


def test_block_list_matches_case_insensitively_and_keeps_only_the_reason():
    """eBay usernames are case-insensitive, and the config's spelling of one
    is whoever typed it."""
    fields = ebay.seller(ITEM, BLOCKED)
    assert fields["seller_blocked"] == "test reason"
    assert "some_seller" not in json.dumps(fields).lower()


def test_scrub_drops_username_and_location_below_country():
    clean = json.dumps(ebay.scrub(ITEM)).lower()
    assert "some_seller" not in clean
    assert "vancouver" not in clean and "v6b" not in clean
    scrubbed = ebay.scrub(ITEM)
    # What the parser and ranking read survives, so a raw file still
    # reproduces a parse (§7).
    assert scrubbed["itemLocation"] == {"country": "CA"}
    assert scrubbed["seller"]["feedbackPercentage"] == "99.4"
    assert scrubbed["title"] == ITEM["title"]
    assert ITEM["seller"]["username"] == "Some_Seller"  # input untouched


def test_parts_condition_is_read_from_the_id_not_the_localised_text():
    """eBay.ca serves French condition text to some listings ("D'occasion"),
    so "For parts" is identified by conditionId 7000."""
    item = dict(ITEM, condition="Pour pièces ou ne fonctionne pas",
                conditionId="7000")
    assert ebay.seller(item, {})["condition"] == "For parts or not working"
    assert ebay.seller(dict(ITEM, conditionId="3000"), {})["condition"] == "Used"


def test_calculated_shipping_without_a_cost_is_none_not_zero():
    item = dict(ITEM, shippingOptions=[{"shippingCostType": "CALCULATED"}])
    assert ebay.offer(item)["shipping"] is None


def test_cheapest_shipping_option_is_taken_and_estimate_marked():
    item = dict(ITEM, shippingOptions=[
        {"shippingCostType": "FIXED", "shippingCost": {"value": "40.00"}},
        {"shippingCostType": "CALCULATED", "shippingCost": {"value": "25.50"}}])
    observation = ebay.offer(item)
    assert observation["shipping"] == 25.5
    assert observation["shipping_estimated"] is True
