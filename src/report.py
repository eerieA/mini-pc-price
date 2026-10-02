"""SQLite -> filter -> rank -> console (plan.md §9).

Run by hand against whatever poll.py has collected:

    python src/report.py

Not a second digest implementation. It prints the same ranking §6 describes,
minus the email and the baselines that need history.
When digest.py arrives in Phase 2 the filter-and-rank step moves somewhere both
can call it -- but that extraction happens when the second consumer exists, not
in anticipation of it (§7).

ASCII only, and that is a constraint rather than a preference: a Windows console
is cp1252 and printing a single arrow or check mark raises UnicodeEncodeError,
killing the run.
"""

import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import db
import overrides
import ranking
import specs
import watches

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 78



# ITRefurbs opens every title with its condition grade. Left in, it fills the
# 44 characters and every line reads "Refurbished (Excellent) - HP EliteDesk
# 800..." with the model cut off. Display only: title_raw keeps it.
CONDITION_PREFIX_RE = re.compile(
    r"^(?:Refurbished\s*\([^)]*\)|Open Box|Brand New)\s*-\s*", re.I)


def short_title(title, limit=44):
    """Trim to the last whole word that fits. Vendor titles run to 90 characters
    of boilerplate ("Windows 11 Pro ... Refurbished") and the model is at the
    front, so the tail is what to lose."""
    title = CONDITION_PREFIX_RE.sub("", " ".join(title.split()))
    if len(title) <= limit:
        return title
    return title[:limit].rsplit(" ", 1)[0] + "..."


def format_listing(listing, price, terms, misses, rules):
    # The source is on the first line because two vendors now compete for the
    # same rank, and "+ 25 src" in the arithmetic does not say which one.
    lines = [f"  ${price:<8.2f} {listing['source_id']:<9} "
             f"{short_title(listing['title_raw'])}"]
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

    if listing.get("fulfillment"):
        # A marketplace listing is two decisions, the machine and the seller.
        # The seller's name is on the listing page, never stored (src/ebay.py).
        origin = f" | ships from {listing['ships_from']}" if listing.get("ships_from") else ""
        lines.append(f"           seller {listing['seller_rating']}% "
                     f"({listing['seller_reviews']}) | {listing['condition']}{origin}")
    if listing.get("shipping_estimated"):
        lines.append("           ! shipping is eBay's estimate, not a listed rate")
    if listing.get("overridden"):
        # A ranking that silently depends on an emailed answer is not checkable.
        lines.append("           ! vendor-confirmed, not from the listing page")
    if specs.NVME_FROM_DESCRIPTION in (listing.get("parse_notes") or ""):
        # Same reason as the line above: the title says only "SSD", so the
        # ranking rests on the description's word for it.
        lines.append("           ! NVMe per the description, not the title")
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

    # The same configuration at another vendor, folded here by
    # ranking.collapse_duplicates. Kept on its own line with its URL: it is
    # still a listing someone may prefer to buy from.
    for other_price, other in listing.get("also_at", []):
        lines.append(f"           also {other['source_id']} ${other_price:.2f} "
                     f"{other['url']}")
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
        ranked = ranking.rank(listings, config)
    ranked, unparsed, excluded, dismissed, set_aside = ranked
    qualifiers, near_misses = ranking.split(ranked)
    requirements = rules["requirements"]

    out.append(header_line(listings, dismissed))
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

    if dismissed:
        # One line each and no URL: these were looked at and set aside by hand,
        # so they are listed to stay visible (§9), not to be acted on. The URL
        # is in the file that dismissed them.
        out.append("")
        out.append(f"OUT OF SCOPE ({len(dismissed)}) - config/out_of_scope.yaml")
        out.append("-" * WIDTH)
        for listing, reason in dismissed:
            out.append(f"  {listing['source_id']:<9} "
                       f"{short_title(listing['title_raw'], 40):<43} {reason}")

    out.extend(multi_config_lines(set_aside))
    out.extend(held_out_lines(set_aside))
    out.extend(set_aside_lines(set_aside))

    out.append("=" * WIDTH)
    out.extend(summary_lines(qualifiers, near_misses, rules))
    out.extend(warning_lines(ranked, unparsed, config,
                             stale=_stale_sources(listings)))
    return "\n".join(out)


def multi_config_lines(set_aside):
    """One listing offering several configurations at one URL (§6). Unranked:
    a price range is not one machine, but hiding it hides live inventory."""
    rows = sorted((e["listing"] for e in set_aside if e["kind"] == "multi-config"),
                  key=lambda l: l["price"])
    if not rows:
        return []
    out = ["", f"MULTI-CONFIGURATION - not ranked, chassis can reach 64GB "
               f"({len(rows)})", "-" * WIDTH]
    for listing in rows:
        span = f"${listing['price']:.2f}-${listing['price_max']:.2f}"
        out.append(f"  {span:<18} {short_title(listing['title_raw'], 48)}")
        out.append(f"           {listing['url']}")
    return out


def held_out_lines(set_aside):
    """Scored listings waiting on a config value (§2), counted by reason.

    Counted rather than listed because one origin can hold out sixty listings.
    The cheapest qualifier among them is the number that says whether setting
    the value is worth doing today -- priced WITHOUT the missing term, and
    labelled so.
    """
    held = [e for e in set_aside if e["kind"] == "held out"]
    if not held:
        return []
    out = ["", f"HELD OUT - an input is missing, so not ranked ({len(held)})",
           "-" * WIDTH]
    for reason, count in Counter(e["detail"] for e in held).most_common():
        line = f"  {count:>3}  {reason}"
        qualifying = sorted(e["row"][0] for e in held
                            if e["detail"] == reason and not e["row"][3])
        if qualifying:
            line += (f" | {len(qualifying)} would qualify, cheapest "
                     f"${qualifying[0]:.2f} before that term")
        out.append(line)
    return out


# The set-aside kinds summarised by set_aside_lines, in print order, with the
# words the digest uses for each.
_COUNTED_KINDS = {
    "keyword": "excluded by keyword",
    "for parts": "listed for parts or not working",
    "blocked seller": "from a blocked seller",
    "no returns": "from sellers accepting no returns",
    "seller gate": "seller below gate",
    "multi-config capped": "multi-configuration on a chassis capped below 64GB",
}


def set_aside_lines(set_aside):
    """One counted line per kind, its details tallied (§6):

        ebay: 14 excluded by keyword (caddy 5, bezel 4, motherboard 3, ...)

    Counted, never listed and never dropped. A word hiding thirty listings a day
    stands out in that line, which is the whole check on the keyword list.
    """
    out = []
    for kind, words in _COUNTED_KINDS.items():
        entries = [e for e in set_aside if e["kind"] == kind]
        for source in sorted({e["listing"]["source_id"] for e in entries}):
            tally = Counter(e["detail"] for e in entries
                            if e["listing"]["source_id"] == source)
            detail = ", ".join(f"{d} {n}" for d, n in tally.most_common(6))
            more = ", ..." if len(tally) > 6 else ""
            out.append(f"  {source}: {sum(tally.values())} {words} "
                       f"({detail}{more})")
    if out:
        out = ["", "SET ASIDE - counted, not candidates", "-" * WIDTH] + out
    return out


def header_line(listings, dismissed):
    """Per source: listings, how many parsed, how many were set aside by hand.

    Out-of-scope listings are counted apart from parse failures because they
    are not failures -- lumping them in would make a healthy source read as a
    broken one.
    """
    set_aside = {l["url"] for l, _ in dismissed}
    parts = []
    for source in sorted({l["source_id"] for l in listings}):
        rows = [l for l in listings if l["source_id"] == source]
        ok = sum(1 for l in rows if l["parse_ok"] and l["url"] not in set_aside)
        out = sum(1 for l in rows if l["url"] in set_aside)
        detail = f"{ok} ok" + (f", {out} out of scope" if out else "")
        parts.append(f"{source} {len(rows)} ({detail})")
    newest = max((l["observed_at"] for l in listings), default=None)
    fetched = newest[:16].replace("T", " ") if newest else "-"
    return f"{' | '.join(parts) or 'no listings'} | fetched {fetched}"


def _stale_sources(listings):
    """{source_id: hours since its newest observation}, stale sources only.

    Per source because poll.py polls each one independently: one vendor's
    endpoint failing leaves the other's prices fresh, and a single newest-
    observation check would read that as a healthy poll.
    """
    stale = {}
    for source in sorted({l["source_id"] for l in listings}):
        hours = _stale_hours([l for l in listings if l["source_id"] == source])
        if hours is not None:
            stale[source] = hours
    return stale


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

    for source, hours in (stale or {}).items():
        # First, because it qualifies everything below it: on stale data the
        # prices, the ranking and the gap are all as old as the last poll.
        warnings.append(
            f"{source.upper()} PRICES ARE {hours:.0f} HOURS OLD - its last poll "
            f"did not run or failed. Its prices and ranks below are from that "
            f"poll; check logs/ for the failure before acting on them."
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
            f"storage interface unstated - listings say only 'SSD'. "
            f"{unstated} of {len(ranked)} pay the nvme penalty.{detail}"
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

    marketplace = [l for l in unparsed if l.get("fulfillment")]
    for listing in unparsed:
        if listing.get("fulfillment"):
            continue  # counted below, not listed
        warnings.append(f"parse_ok=false: {listing['title_raw'][:56]}")
        if listing["parse_notes"]:
            warnings.append(f"  {listing['parse_notes'][:70]}")
    warnings.extend(unparsed_tally(marketplace))

    warnings.append(f"parts.yaml priced {config['parts']['priced_on']}; "
                    f"DDR4 is EOL and rising 10-20%/mo.")

    return ["", "warnings", "-" * WIDTH] + [f"  ! {w}" for w in warnings]


# parse_notes that record where a value came from, not why parsing failed.
_INFORMATIONAL_NOTE = re.compile(r"read from description|variants; only")


def unparsed_tally(listings):
    """Marketplace parse failures counted by reason, not listed one by one.

    eBay's volume would turn the two-lines-per-listing warning into a hundred
    lines a day, and a warning that long is one nobody reads (§8). The tally
    still names every reason, and an unknown chassis is named by brand and
    model number -- the counts say which chassis.yaml entries would pay off.
    A listing with two problems is counted under both.
    """
    if not listings:
        return []
    tally = Counter()
    for listing in listings:
        for note in (listing["parse_notes"] or "").split("; "):
            if not note or _INFORMATIONAL_NOTE.search(note):
                continue
            if note.startswith("no chassis key"):
                title = listing["title_raw"]
                note = (f"no chassis key: {specs._brand(title) or '?'} "
                        f"{specs._model_number(title) or '?'}")
            tally[note] += 1
    source = listings[0]["source_id"]
    lines = [f"{source}: {len(listings)} parse_ok=false, by reason "
             f"(a listing can have several):"]
    lines += [f"  {count:>3}  {note[:66]}" for note, count in tally.most_common()]
    return lines


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
