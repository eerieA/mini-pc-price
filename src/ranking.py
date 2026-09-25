"""Filter, score and rank listings (plan.md §2).

Both consumers of the ranking import this: `report.py` renders it to a console
and `digest.py` emails it. §7 puts the shared filter-and-rank step in one place
once a second consumer exists, which is what Phase 2 created -- before that it
lived in report.py, and duplicating it here would have been the wrong move.

Nothing in this module renders anything. Effective price is defined once.
"""

from pathlib import Path

import yaml

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
        "sources": {s["id"]: s for s in read("sources.yaml")["sources"]},
        "watches": watches.load(CONFIG / "watch_urls.yaml"),
        "overrides": overrides.load(CONFIG / "listing_overrides.yaml"),
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

    return price, terms, misses


def gated(listing, rules):
    """The one requirement no price offsets. None means it passes."""
    if rules["gates"].get("nested_virt") and listing.get("nested_virt") == 0:
        return "no nested virtualisation"
    return None


def rank(listings, config):
    """Partition listings into ranked / unparsed / excluded, cheapest first.

    Returns (ranked, unparsed, excluded) where ranked is a list of
    (effective_price, listing, terms, misses) sorted by price. A listing that
    fails to parse is never silently dropped and a gated one carries its reason,
    because both have to appear in the output (§9, §8).
    """
    rules = config["rules"]
    ranked, unparsed, excluded = [], [], []

    for listing in listings:
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
        ranked.append((price, listing, terms, misses))

    ranked.sort(key=lambda r: r[0])
    return ranked, unparsed, excluded


def split(ranked):
    """(qualifiers, near_misses). Separate blocks, never one sorted list.

    A flat else_penalty against a real parts cost lets a capped machine undercut
    a capable one, so a single ranking reads as "buy the cheapest" and points at
    a box that cannot host the lab (§2, §6).
    """
    return [r for r in ranked if not r[3]], [r for r in ranked if r[3]]
