"""Filter, score and rank listings (plan.md §2).

Both consumers of the ranking import this: `report.py` renders it to a console
and `digest.py` emails it. §7 puts the shared filter-and-rank step in one place
once a second consumer exists, which is what Phase 2 created -- before that it
lived in report.py, and duplicating it here would have been the wrong move.

Nothing in this module renders anything. Effective price is defined once.
"""

import re
from numbers import Number
from pathlib import Path

import yaml

import out_of_scope
import overrides
import watches

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


def load_config():
    def read(name):
        return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))
    return {
        "rules": read("rules.yaml"),
        "parts": read("parts.yaml"),
        "chassis": read("chassis.yaml"),
        "sellers": read("sellers.yaml"),
        "sources": {s["id"]: s for s in read("sources.yaml")["sources"]},
        "watches": watches.load(CONFIG / "watch_urls.yaml"),
        "overrides": overrides.load(CONFIG / "listing_overrides.yaml"),
        "out_of_scope": out_of_scope.load(CONFIG / "out_of_scope.yaml"),
    }


def ram_upgrade_cost(listing, parts):
    """What it costs to get this machine to 64 GB, or None if it cannot get there.

    Two different parts, and pricing one with the other's number is wrong in an
    unknown direction (parts.yaml): a 2-slot Tiny/Micro takes SODIMMs, while a
    4-socket SFF takes full-size UDIMMs sold as kits.
    """
    shipped = listing["ram_gb"] or 0
    if listing["ram_slots"] == 4:
        # 4 x 16 GB = two 2x16GB kits. The SFF reaches 64 GB across four sockets,
        # so the SODIMM figure does not apply.
        return 2 * parts["ram"]["ddr4_udimm_32gb_kit"], "RAM (2 UDIMM kits)"
    modules = max(0, -(-(64 - shipped) // 32))  # 32 GB SODIMMs, rounded up
    return modules * parts["ram"]["ddr4_sodimm_32gb"], f"RAM ({modules}x SODIMM)"


def score(listing, config):
    """effective_price and what is wrong with the machine (§2).

    Returns (price, [arithmetic terms], [unmet requirements]). cost_to_reach and
    else_penalty are mutually exclusive per requirement: a machine is never
    charged both for an upgrade and for being unable to upgrade.
    """
    rules = config["rules"]
    requirements = rules["requirements"]
    parts = config["parts"]

    price = listing["price"]
    terms = [f"${price:.2f} list"]
    misses = []

    ceiling = listing["ram_max_gb"]
    if ceiling is not None and ceiling >= requirements["ram_max_gb"]["min"]:
        cost, label = ram_upgrade_cost(listing, parts)
        if cost:
            price += cost
            terms.append(f"+ {cost} {label}")
    else:
        penalty = requirements["ram_max_gb"]["else_penalty"]
        price += penalty
        terms.append(f"+ {penalty} pen")
        misses.append(f"ram_max {ceiling}GB<{requirements['ram_max_gb']['min']}")

    minimum_cores = requirements["cpu_cores"]["min"]
    if (listing["cpu_cores"] or 0) < minimum_cores:
        penalty = requirements["cpu_cores"]["else_penalty"]
        price += penalty
        terms.append(f"+ {penalty} pen")
        misses.append(f"cores {listing['cpu_cores']}<{minimum_cores}")

    if listing["storage_type"] != "nvme":
        penalty = requirements["storage_nvme"]["else_penalty"]
        price += penalty
        terms.append(f"+ {penalty} pen")
        # "unknown", not "no NVMe": this source never states an interface, so the
        # drive may well be NVMe -- the vendor simply does not say (§5).
        misses.append("nvme unknown")

    # A hand-seeded watch (§9) has no sources.yaml entry and so no
    # source_adjustment. Scoring it at 0 is the honest reading -- the URL may be
    # any retailer -- but it makes a watched listing look better than an eTek one
    # at the same price, which is why warning_lines() names them.
    source = config["sources"].get(listing["source_id"], {})
    adjustment = source.get("source_adjustment", 0)
    if adjustment:
        price += adjustment
        terms.append(f"+ {adjustment} src")

    # Marketplace terms (§2, §3). The Shopify sources state none of these and
    # pay nothing for them, which §2 records as flattering them on shipping.
    fulfillment = config["sellers"]["fulfillment_adjustment"].get(
        listing.get("fulfillment"), 0)
    if fulfillment:
        price += fulfillment
        terms.append(f"+ {fulfillment} seller")
    if listing.get("shipping"):
        price += listing["shipping"]
        # "~" when eBay shows only an estimate (§2): the sum rests on it.
        mark = "~" if listing.get("shipping_estimated") else ""
        terms.append(f"+ {mark}{listing['shipping']:.2f} ship")
    duty = import_adjustment(listing, rules)
    if isinstance(duty, Number) and duty:
        price += duty
        terms.append(f"+ {duty} import")

    return price, terms, misses


def import_adjustment(listing, rules):
    """Dollars for this listing's ships-from country, or the config's raw value.

    None for a source that states no origin (the domestic Shopify stores). A
    non-number -- `TBD` -- comes back as it is and holds the listing out of the
    ranking (held_out_reason), never as 0.
    """
    origin = listing.get("ships_from")
    if origin is None:
        return None
    table = rules["import_adjustment"]
    return table.get(origin, table["default"])


def held_out_reason(listing, rules):
    """Why a scored listing cannot be ranked yet, or None (§2).

    Both cases are missing inputs that would default to the flattering value,
    the same reason an unknown chassis is held out rather than assumed (§5).
    """
    currency = listing.get("currency")
    if currency and currency != "CAD":
        # Never converted: an unconverted US$300 beside C$300 is a ~$100 error.
        return f"priced in {currency}"
    if not isinstance(import_adjustment(listing, rules), (Number, type(None))):
        return f"ships from {listing['ships_from']}, import_adjustment TBD"
    if listing.get("fulfillment") and listing.get("shipping") is None:
        # eBay could not quote shipping to Canada. The Shopify sources state
        # no shipping either and are charged 0 (§2), but a marketplace listing
        # can cost anything to ship, so it waits rather than ranks at 0.
        return "shipping not quoted"
    return None


def set_aside_reason(listing, config):
    """(kind, detail) for a marketplace listing that is not a candidate, or None.

    Every one of these is COUNTED in the digest by kind and detail, never
    dropped (§3, §6): a keyword hiding thirty real listings a day has to be
    visible as a large count to be noticed at all.
    """
    source = config["sources"].get(listing["source_id"], {})
    for word in source.get("exclude_keywords", []):
        if re.search(rf"\b{re.escape(word)}s?\b", listing["title_raw"], re.I):
            return "keyword", word
    # eBay's condition is a structured field, so it needs no keyword.
    if (listing.get("condition") or "").lower().startswith("for parts"):
        return "for parts", listing["condition"]

    if listing.get("fulfillment") is None:
        return None  # not a marketplace listing; no seller to judge
    # Matched against the block list at poll time, since the username is never
    # stored (src/ebay.py) -- so a block-list edit applies from the next poll.
    if listing.get("seller_blocked"):
        return "blocked seller", listing["seller_blocked"]
    gates = config["sellers"]["gates"]
    if gates["returns_accepted"] and not listing.get("returns_accepted"):
        return "no returns", "seller accepts none"
    # A missing figure fails the gate rather than passing it: never default.
    if (listing.get("seller_rating") or 0) < gates["min_feedback_percent"]:
        return "seller gate", f"feedback {listing.get('seller_rating')}%"
    if (listing.get("seller_reviews") or 0) < gates["min_feedback_score"]:
        return "seller gate", f"{listing.get('seller_reviews')} reviews"
    return None


def gated(listing, rules):
    """The one requirement no price offsets. None means it passes."""
    if rules["gates"].get("nested_virt") and listing.get("nested_virt") == 0:
        return "no nested virtualisation"
    return None


def rank(listings, config):
    """Partition listings into ranked / unparsed / excluded / out of scope / set
    aside.

    Returns (ranked, unparsed, excluded, dismissed, set_aside) where ranked is a
    list of (effective_price, listing, terms, misses) sorted by price, with
    duplicate configurations folded under the cheapest (see
    collapse_duplicates). A listing that fails to parse is never silently
    dropped, and a gated or out-of-scope one carries its reason, because every
    listing has to appear in the output (§9, §8).

    `set_aside` is the marketplace's volume, which cannot be listed one by one:
    dicts of {listing, kind, detail}, plus `row` -- the scored tuple -- for a
    held-out listing. Kinds: those of set_aside_reason(), "multi-config" (shown
    in its own section, §6), "multi-config capped" and "held out".
    """
    rules = config["rules"]
    ranked, unparsed, excluded, dismissed, set_aside = [], [], [], [], []

    for listing in listings:
        # First, ahead of parse_ok: most out-of-scope listings do not parse, and
        # reporting them as parser failures is exactly what the list prevents.
        reason = out_of_scope.reason_for(listing, config["out_of_scope"])
        if reason:
            dismissed.append((listing, reason))
            continue
        # Also ahead of parse_ok, for the same reason: a drive caddy does not
        # parse either, and it is not parser breakage.
        reason = set_aside_reason(listing, config)
        if reason:
            set_aside.append({"listing": listing, "kind": reason[0],
                              "detail": reason[1]})
            continue
        if listing.get("price_max") is not None:
            # A price range across configurations is not one machine (§6). Shown
            # only where the chassis can reach the RAM target at some variant;
            # the title names the chassis even when it names no configuration.
            ceiling = listing["ram_max_gb"]
            capable = ceiling and ceiling >= rules["requirements"]["ram_max_gb"]["min"]
            set_aside.append({
                "listing": listing,
                "kind": "multi-config" if capable else "multi-config capped",
                "detail": listing["chassis_key"] or "unknown chassis"})
            continue
        if not listing["parse_ok"]:
            unparsed.append(listing)
            continue
        reason = gated(listing, rules)
        if reason:
            excluded.append((listing, reason))
            continue
        # Hand-confirmed facts land here: after parsing, before scoring. The
        # database keeps what the page said; the ranking uses what we know.
        listing["overridden"] = overrides.apply(listing, config["overrides"])
        price, terms, misses = score(listing, config)
        reason = held_out_reason(listing, rules)
        if reason:
            set_aside.append({"listing": listing, "kind": "held out",
                              "detail": reason,
                              "row": (price, listing, terms, misses)})
            continue
        ranked.append((price, listing, terms, misses))

    ranked.sort(key=lambda r: r[0])
    return (collapse_duplicates(ranked), unparsed, excluded, dismissed,
            set_aside)


def collapse_duplicates(ranked):
    """One line per configuration across vendors (§4 canonical_key, §9).

    `ranked` must already be sorted, so the first listing seen for a key is the
    cheapest effective price and leads; the rest go on its `also_at` as
    (effective_price, listing) and are rendered beneath it.

    Only within a block. The key is the configuration, but a hand override can
    make one unit qualify and its twin not, and folding a qualifier under a
    near-miss would hide it in the block a reader treats as "cannot do the job".
    """
    leads, collapsed = {}, []
    for row in ranked:
        price, listing, _, misses = row
        listing["also_at"] = []
        key = listing["canonical_key"]
        if key is None:
            collapsed.append(row)
            continue
        lead = leads.get((key, bool(misses)))
        if lead is None:
            leads[(key, bool(misses))] = listing
            collapsed.append(row)
        else:
            lead["also_at"].append((price, listing))
    return collapsed


def split(ranked):
    """(qualifiers, near_misses). Separate blocks, never one sorted list.

    A flat else_penalty against a real parts cost lets a capped machine undercut
    a capable one, so a single ranking reads as "buy the cheapest" and points at
    a box that cannot host the lab (§2, §6).
    """
    return [r for r in ranked if not r[3]], [r for r in ranked if r[3]]
