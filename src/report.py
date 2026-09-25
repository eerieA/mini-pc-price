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
from datetime import datetime, timezone
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

    # Deliberately NOT truncated and deliberately allowed to exceed WIDTH. Every
    # other field here is trimmed to fit -- short_title drops boilerplate, the
    # override warnings print a 64-character tail -- because a shortened title is
    # still a title. A shortened URL is a broken link, so this is the one line in
    # the report that sets its own width. On its own line and unwrapped is also
    # what mail clients autolink, which is what makes it clickable (digest.py).
    #
    # Expect handles that contradict their own listing: eTek copies products and
    # does not rename them, so the 9020 links to a "...3070...-copy" handle and
    # both 3080s to "3090". Those URLs are right -- the handle is a unique key,
    # never evidence about the hardware (config/chassis_aliases.yaml).
    lines.append(f"           {listing['url']}")
    return lines


def build_report(listings, config, ranked=None):
    """Render the ranking. Pass `ranked` to reuse a ranking already computed.

    ranking.rank() applies overrides by mutating the listing dicts, so calling
    it twice on the same objects makes the second call see a value the first
    wrote and report the override as redundant. Callers that need the ranking
    for their own purposes (digest.py, to decide whether anything changed) pass
    it in rather than ranking a second time.
    """
    rules = config["rules"]
    parts = config["parts"]
    out = []

    if ranked is None:
        ranked, unparsed, excluded = ranking.rank(listings, config)
    else:
        ranked, unparsed, excluded = ranked
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
            out.append(f"           {listing['url']}")

    out.append("=" * WIDTH)
    out.extend(summary_lines(qualifiers, near_misses, rules))
    out.extend(warning_lines(ranked, unparsed, config,
                             stale=_stale_hours(listings)))
    return "\n".join(out)


def _stale_hours(listings):
    """Hours since the newest observation, or None if that is recent.

    Scheduled, the digest sends whether or not the poll succeeded -- silence
    would be indistinguishable from a quiet day (scripts/run-daily.ps1). That
    choice is only honest if a digest built on yesterday's prices says so in
    words. The header prints `fetched <timestamp>`, but reading a stale date
    requires noticing it; this states the conclusion.

    36 hours, not 24: a daily run that drifts by an hour, or a DST shift, must
    not raise a warning that means nothing.
    """
    if not listings:
        return None
    newest = max(l["observed_at"] for l in listings)
    observed = datetime.fromisoformat(newest)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - observed).total_seconds() / 3600
    return hours if hours >= 36 else None


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


def warning_lines(ranked, unparsed, config, stale=None):
    """Not decoration (§9). These are conclusions a reader would otherwise have
    to reconstruct from the ranking."""
    warnings = []

    if stale is not None:
        # First, because it qualifies everything below it: on stale data the
        # prices, the ranking and the gap are all as old as the last poll.
        warnings.append(
            f"PRICES ARE {stale:.0f} HOURS OLD - the last poll did not run or "
            f"failed. Every price and ranking below is from that poll; check "
            f"logs/ for the failure before acting on it."
        )

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
