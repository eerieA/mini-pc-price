"""SQLite -> filter -> rank -> console (plan.md §9).

Run by hand against whatever poll.py has collected:

    python src/report.py

Not a second digest implementation. It prints the same ranking §6 describes,
minus the email, the baselines that need history and the multi-source sections.
When digest.py arrives in Phase 2 the filter-and-rank step moves somewhere both
can call it -- but that extraction happens when the second consumer exists, not
in anticipation of it (§7).

ASCII only, and that is a constraint rather than a preference: a Windows console
is cp1252 and printing a single arrow or check mark raises UnicodeEncodeError,
killing the run.
"""

import sys
from pathlib import Path

import yaml

import db
import watches

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
WIDTH = 78


def load_config():
    def read(name):
        return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))
    return {
        "rules": read("rules.yaml"),
        "parts": read("parts.yaml"),
        "chassis": read("chassis.yaml"),
        "sources": {s["id"]: s for s in read("sources.yaml")["sources"]},
        "watches": watches.load(CONFIG / "watch_urls.yaml"),
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


def short_title(title, limit=44):
    """Trim to the last whole word that fits. Vendor titles run to 90 characters
    of boilerplate ("Windows 11 Pro ... Refurbished") and the model is at the
    front, so the tail is what to lose."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    return title[:limit].rsplit(" ", 1)[0] + "..."


def format_listing(listing, price, terms, misses, rules):
    lines = [f"  ${price:<8.2f} {short_title(listing['title_raw'])}"]
    lines.append(f"           {' '.join(terms)}")

    ceiling = listing["ram_max_gb"]
    ram = f"{listing['ram_gb']}GB"
    if ceiling and ceiling >= rules["requirements"]["ram_max_gb"]["min"]:
        ram = f"{listing['ram_gb']}->{ceiling}GB"
    shipped_marker = ""
    if (listing["ram_gb"] or 0) >= rules["prefer_shipped_ram_gb"]:
        shipped_marker = " *ships 32GB"
    lines.append(f"           {listing['cpu']} / {listing['cpu_cores']}c / {ram} / "
                 f"{listing['storage_gb']}GB {listing['storage_type'].upper()}"
                 f"{shipped_marker}")

    if misses:
        lines.append(f"           x {', '.join(misses)}")
    return lines


def build_report(listings, config):
    rules = config["rules"]
    parts = config["parts"]
    out = []

    ranked, unparsed, excluded = [], [], []
    for listing in listings:
        if not listing["parse_ok"]:
            unparsed.append(listing)
            continue
        reason = gated(listing, rules)
        if reason:
            excluded.append((listing, reason))
            continue
        price, terms, misses = score(listing, config)
        ranked.append((price, listing, terms, misses))
    ranked.sort(key=lambda r: r[0])

    qualifiers = [r for r in ranked if not r[3]]
    near_misses = [r for r in ranked if r[3]]

    source = listings[0]["source_id"] if listings else "none"
    fetched = listings[0]["observed_at"][:16].replace("T", " ") if listings else "-"
    parsed_ok = sum(1 for l in listings if l["parse_ok"])
    requirements = rules["requirements"]

    out.append(f"{source} | {len(listings)} listings | fetched {fetched} | "
               f"parse_ok {parsed_ok}/{len(listings)}")
    out.append(
        f"rules.yaml: ram>={requirements['ram_max_gb']['min']}GB"
        f"({requirements['ram_max_gb']['else_penalty']}) "
        f"cores>={requirements['cpu_cores']['min']}"
        f"({requirements['cpu_cores']['else_penalty']}) "
        f"nvme({requirements['storage_nvme']['else_penalty']}) | "
        f"parts: SODIMM32 ${parts['ram']['ddr4_sodimm_32gb']}"
    )
    out.append("=" * WIDTH)

    out.append(f"QUALIFIES - meets every requirement ({len(qualifiers)})")
    out.append("-" * WIDTH)
    if qualifiers:
        for price, listing, terms, misses in qualifiers:
            out.extend(format_listing(listing, price, terms, misses, rules))
    else:
        out.append("  (none)")
    out.append("")

    out.append(f"NEAR MISSES - 1+ requirement short ({len(near_misses)})")
    out.append("-" * WIDTH)
    for price, listing, terms, misses in near_misses:
        out.extend(format_listing(listing, price, terms, misses, rules))

    if excluded:
        out.append("")
        out.append(f"EXCLUDED - fails a gate ({len(excluded)})")
        out.append("-" * WIDTH)
        for listing, reason in excluded:
            out.append(f"  {short_title(listing['title_raw'], 50)}")
            out.append(f"           x {reason}")

    out.append("=" * WIDTH)
    out.extend(summary_lines(qualifiers, near_misses, rules))
    out.extend(warning_lines(ranked, unparsed, config))
    return "\n".join(out)


def summary_lines(qualifiers, near_misses, rules):
    """The trade, stated rather than left to be inferred from the ordering.

    At else_penalty 325 against ~$650 of real memory a capped machine can undercut
    a capable one, so a reader scanning for the smallest number is being led
    somewhere the plan does not want them to go (§2).
    """
    cheapest_qualifier = f"${qualifiers[0][0]:.2f}" if qualifiers else "none"
    lines = [f"cheapest qualifier: {cheapest_qualifier}"]
    if near_misses:
        lines[0] += f" | cheapest near-miss ${near_misses[0][0]:.2f}"
        if qualifiers:
            lines[0] += f" | gap ${near_misses[0][0] - qualifiers[0][0]:+.2f}"
        else:
            # Nothing qualifies: the useful number is which machine comes
            # closest, not which is cheapest.
            closest = min(near_misses, key=lambda r: (len(r[3]), r[0]))
            lines.append(f"closest to qualifying: ${closest[0]:.2f} "
                         f"{closest[1]['chassis_key']} - misses only on "
                         f"{', '.join(closest[3])}")
    lines.append(f"* = ships >={rules['prefer_shipped_ram_gb']}GB "
                 f"(prefer_shipped_ram_gb) - no EOL DDR4 to source")
    return lines


def warning_lines(ranked, unparsed, config):
    """Not decoration (§9). These are conclusions a reader would otherwise have
    to reconstruct from the ranking."""
    warnings = []

    if ranked and not [r for r in ranked if not r[3]]:
        shared = set.intersection(*(set(r[3]) for r in ranked))
        if shared:
            warnings.append(
                f"no listing meets every requirement, and all {len(ranked)} miss "
                f"on the same field: {', '.join(sorted(shared))}."
            )
        else:
            warnings.append("no listing meets every requirement.")

    if any("nvme unknown" in r[3] for r in ranked):
        warnings.append(
            "storage interface unstated by this vendor - listings say only "
            "'SSD'. Every one pays nvme penalty; none is confirmed NVMe."
        )
        if config["parts"]["storage"]["nvme_512gb"] is not None:
            warnings.append(
                "parts.yaml now prices NVMe, so the flat penalty understates a "
                "real cost. Add the cost_to_reach(NVMe) term (plan.md §2)."
            )

    for watch in config["watches"]:
        classified = watches.classify(watch["url"])
        if not classified["tier0"]:
            note = f" ({watch['note']})" if watch.get("note") else ""
            warnings.append(f"watch pending: {classified['reason']}{note}")
            warnings.append(f"  {watch['url'][:70]}")

    if any(r[1]["source_id"] == "watch" for r in ranked):
        warnings.append(
            "watched URLs score with source_adjustment 0 - no vendor recourse "
            "risk is priced in. Compare them to sources.yaml rows with care."
        )

    for listing in unparsed:
        warnings.append(f"parse_ok=false: {listing['title_raw'][:56]}")
        if listing["parse_notes"]:
            warnings.append(f"  {listing['parse_notes'][:70]}")

    warnings.append(f"parts.yaml priced {config['parts']['priced_on']}; "
                    f"DDR4 is EOL and rising 10-20%/mo.")

    return ["", "warnings", "-" * WIDTH] + [f"  ! {w}" for w in warnings]


def main():
    # Belt and braces on the ASCII rule: if a non-ASCII character ever reaches
    # the console from a vendor title, degrade it to '?' rather than die.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="ascii", errors="replace")

    conn = db.connect(ROOT / "data" / "tracker.db")
    listings = db.fetch_current(conn)
    conn.close()

    if not listings:
        print("No listings in the database. Run poll.py first.")
        return 1
    print(build_report(listings, load_config()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
