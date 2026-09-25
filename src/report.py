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

import db
import overrides
import ranking
import watches

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 78



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

    if listing.get("overridden"):
        # A ranking that silently depends on an emailed answer is not checkable.
        lines.append("           ! vendor-confirmed, not from the listing page")
    if misses:
        lines.append(f"           x {', '.join(misses)}")
    return lines


def build_report(listings, config):
    rules = config["rules"]
    parts = config["parts"]
    out = []

    ranked, unparsed, excluded = ranking.rank(listings, config)
    qualifiers, near_misses = ranking.split(ranked)

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

    unstated = sum(1 for r in ranked if "nvme unknown" in r[3])
    if unstated:
        confirmed = sum(1 for r in ranked if r[1].get("overridden"))
        detail = (f" {confirmed} confirmed NVMe by hand (see overrides below)."
                  if confirmed else "")
        warnings.append(
            f"storage interface unstated by this vendor - listings say only "
            f"'SSD'. {unstated} of {len(ranked)} pay the nvme penalty.{detail}"
        )
        if config["parts"]["storage"]["nvme_512gb"] is not None:
            warnings.append(
                "parts.yaml now prices NVMe, so the flat penalty understates a "
                "real cost. Add the cost_to_reach(NVMe) term (plan.md §2)."
            )

    for entry in config["overrides"]:
        provenance = overrides.describe([entry], entry["url"])
        warnings.append(f"override in effect: {provenance}")
        # Tail, not head: these URLs share a 60-character prefix and differ only
        # in the handle's last few characters.
        warnings.append(f"  ...{entry['url'][-64:]}")

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
    print(build_report(listings, ranking.load_config()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
