"""eBay.ca through the Browse API -> listings (plan.md §3, §9 Phase 4).

Structured JSON for a search query, with seller reputation, shipping and origin
as fields -- no selectors to break silently, the same advantage that put the
Shopify sources on tier 0 (§5). What it does not give is clean titles, which is
why specs.py's form-factor guard and sources.yaml's keyword list exist.

The search PAGE is evidence, never a feed: fetching it on a schedule is the
marketplace scraping §3 rejected. Only the API is polled.

Credentials are an OAuth client-credentials pair from the eBay developer
account's production keyset, read from the environment and from nowhere else,
because config/*.yaml is committed. The Dev ID is not used by this grant.

**No eBay user's identity is stored, here or in data/raw/.** The production
keyset is enabled under eBay's Marketplace Account Deletion exemption, "I do
not persist eBay data", which is only true if a seller's username never reaches
disk. So the block list is applied as the item is read (seller()) and only its
outcome is kept, and the raw response is scrubbed before it is saved (scrub()).
Feedback figures and the ships-from country are kept: they identify no one.
"""

import base64
import os

import requests

CLIENT_ID = "MINIPC_EBAY_CLIENT_ID"
CLIENT_SECRET = "MINIPC_EBAY_CLIENT_SECRET"

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/{}"
SCOPE = "https://api.ebay.com/oauth/api_scope"
PAGE_SIZE = 200  # the API's maximum

# eBay's structured "For parts or not working". The condition TEXT is localised
# on eBay.ca -- "D'occasion", "Neuf" -- so the id is what identifies it.
PARTS_CONDITION_ID = "7000"


def credentials():
    """(client_id, client_secret) from the environment, or exit naming what is
    missing -- eBay answers an unset secret with a generic invalid_client."""
    pair = os.environ.get(CLIENT_ID), os.environ.get(CLIENT_SECRET)
    missing = [n for n, v in zip((CLIENT_ID, CLIENT_SECRET), pair) if not v]
    if missing:
        raise SystemExit(
            f"{' and '.join(missing)} not set. The eBay production App ID and "
            f"Cert ID are read from the environment (or .env) because "
            f"config/*.yaml is committed."
        )
    return pair


def access_token(client_id, client_secret):
    """An application token, valid two hours -- longer than one poll takes."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        TOKEN_URL, timeout=30,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": SCOPE},
    )
    if response.status_code == 401:
        # Never echo the secret. A production keyset stays disabled until the
        # account-deletion notification is answered, which looks the same.
        raise SystemExit(
            f"eBay rejected the client credentials (401). Check {CLIENT_ID} and "
            f"{CLIENT_SECRET} are the PRODUCTION App ID and Cert ID, and that "
            f"the keyset is enabled (Marketplace Account Deletion exemption)."
        )
    response.raise_for_status()
    return response.json()["access_token"]


def _headers(token, source):
    return {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": source["browse_api"],
        # Without a buyer location eBay omits shipping costs, and shipping is
        # an effective-price term (§2). Country only: a postal code would put
        # the buyer's location in a committed file.
        "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DCA",
    }


def _get(token, source, url, params=None):
    response = requests.get(url, timeout=30, headers=_headers(token, source),
                            params=params)
    response.raise_for_status()
    return response.json()


def search(token, source, query):
    """Every result for one query, all pages. Returns (items, pages).

    `pages` are the decoded responses, kept for save_raw: a parser bug found
    later is fixed against the bytes that produced it (§7).
    """
    items, pages, offset = [], [], 0
    while True:
        page = _get(token, source, SEARCH_URL, params={
            "q": query, "filter": source["filter"],
            "category_ids": source["category_ids"],
            "limit": PAGE_SIZE, "offset": offset,
        })
        pages.append(page)
        items.extend(page.get("itemSummaries", []))
        offset += PAGE_SIZE
        if offset >= page.get("total", 0):
            return items, pages


def returns_accepted(token, source, item):
    """Whether the seller takes returns -- only the full item carries it.

    One call per listing, which is most of a poll's running time. The response
    names the seller and their city; only this one boolean is kept (see the
    module docstring), and the response itself is not saved.
    """
    detail = _get(token, source, ITEM_URL.format(item["itemId"]))
    return bool((detail.get("returnTerms") or {}).get("returnsAccepted"))


def variation_group(token, source, item):
    """(lowest, highest price, returns_accepted) for a multi-variation listing.

    The search returns one variant's price for the whole group -- in the first
    live poll, $339.99 for a listing whose variants ran $149.99 to $489.99 --
    so the range needs the group itself (§6).
    """
    group = _get(token, source, item["itemGroupHref"])
    prices = [float(i["price"]["value"]) for i in group.get("items", [])]
    terms = (group.get("items") or [{}])[0].get("returnTerms") or {}
    return min(prices), max(prices), bool(terms.get("returnsAccepted"))


def offer(item):
    """The observation fields of one item summary (§4).

    Every value as listed. Currency is recorded and never converted (§2), and a
    missing shipping figure stays None rather than becoming 0 -- the ranking
    holds that listing out instead of flattering it.
    """
    # A CALCULATED option can arrive with no cost at all (21 of 161 in the
    # first live search, nearly all from the US): eBay could not quote the
    # destination. That stays None, and the ranking holds the listing out.
    shipping, estimated = None, None
    options = item.get("shippingOptions") or []
    if options:
        cheapest = min(options,
                       key=lambda o: float(o.get("shippingCost", {}).get("value", "inf")))
        if "shippingCost" in cheapest:
            shipping = float(cheapest["shippingCost"]["value"])
            estimated = cheapest.get("shippingCostType") == "CALCULATED"
    return {
        "price": float(item["price"]["value"]),
        "currency": item["price"]["currency"],
        "shipping": shipping,
        "shipping_estimated": estimated,
        "compare_at_price": None,
        "price_max": None,
        "in_stock": True,  # the search returns active listings only
    }


def seller(item, blocked_sellers):
    """The listings-row seller fields -- everything but who the seller is.

    The username is read once, here, to check sellers.yaml's block list, and
    only the entry's reason is kept (see the module docstring). `seller` stays
    None. Feedback arrives as a string.
    """
    info = item.get("seller") or {}
    username = (info.get("username") or "").lower()
    blocked = {name.lower(): why for name, why in blocked_sellers.items()}
    percent = info.get("feedbackPercentage")
    return {
        "seller": None,
        "seller_blocked": blocked.get(username),
        "seller_rating": float(percent) if percent is not None else None,
        "seller_reviews": info.get("feedbackScore"),
        "fulfillment": "seller_fulfilled",
        "ships_from": (item.get("itemLocation") or {}).get("country"),
        "condition": ("For parts or not working"
                      if item.get("conditionId") == PARTS_CONDITION_ID
                      else item.get("condition")),
    }


def scrub(item):
    """A copy of one item summary that is safe to write to data/raw/.

    Drops the seller's username and the item location below country level --
    for a private seller a postal code and city are about a person, not a
    business. Everything the parser or the ranking reads survives, so the raw
    file still reproduces a parse (§7).
    """
    clean = dict(item)
    clean["seller"] = {k: v for k, v in (item.get("seller") or {}).items()
                       if k != "username"}
    if "itemLocation" in item:
        clean["itemLocation"] = {"country": item["itemLocation"].get("country")}
    return clean


def url(item):
    """The listing's stable URL. itemWebUrl carries tracking parameters that
    vary between responses, and §4 keys listings on URL."""
    return f"https://www.ebay.ca/itm/{item['legacyItemId']}"
