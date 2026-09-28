"""What moved since the last digest (plan.md §6).

Phase 2 sent every day. On a source with ten SKUs whose prices sit still for
weeks that is how a digest teaches its one reader to stop opening it, which §6
names as the thing to avoid. So the digest now sends only when something moved,
and says what moved.

"Moved" is deliberately defined on LIST price and qualification status, not on
effective price. Effective price also changes when `rules.yaml` or `parts.yaml`
is edited, and a digest that arrives because you tuned a penalty is a digest
about your own keystrokes. Tuning is a thing you do while looking at the report;
it does not need to be mailed back to you.

The state this needs -- what was last reported -- lives in one row of a
`digest_state` table rather than a file: it must move with the database, because
the database is what Actions commits and what you pull back (§7).
"""

import json

import report

SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    sent_at     TEXT NOT NULL,
    snapshot    TEXT NOT NULL
);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA)


def snapshot(listings, qualifying_urls):
    """The comparable state of one run: url -> (price, in_stock, qualifies).

    Keyed on URL because that is what §4 keys listings on, and because a listing
    id is local to one database -- this snapshot is written to a database that
    travels between a CI runner and a desktop.
    """
    return {
        listing["url"]: {
            "price": listing["price"],
            "in_stock": bool(listing["in_stock"]),
            "qualifies": listing["url"] in qualifying_urls,
            "title": listing["title_raw"],
        }
        for listing in listings
    }


def load_previous(conn):
    """The snapshot from the last digest that was actually sent, or None.

    None means "never sent" and is not the same as "nothing changed": the first
    run on a fresh database must send, or a new checkout would sit silent until
    a price happened to move.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT sent_at, snapshot FROM digest_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return {"sent_at": row["sent_at"], "listings": json.loads(row["snapshot"])}


def record_sent(conn, sent_at, current):
    """Mark this snapshot as reported. Called only after a send succeeds.

    Recording before the send would lose a day's changes to a transient SMTP
    failure: the next run would compare against a state that was never
    delivered, and the movement would never be reported at all.
    """
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO digest_state (id, sent_at, snapshot) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET sent_at=excluded.sent_at, "
        "snapshot=excluded.snapshot",
        (sent_at, json.dumps(current)),
    )


def diff(previous, current):
    """What changed between two snapshots.

    Returns a dict of lists. Empty everywhere means nothing to report, which is
    the case that suppresses the send.
    """
    if previous is None:
        return None  # never sent; the caller sends unconditionally

    before, after = previous["listings"], current
    changes = {"price": [], "appeared": [], "disappeared": [],
               "qualified": [], "unqualified": [], "stock": []}

    for url, now in after.items():
        was = before.get(url)
        if was is None:
            changes["appeared"].append({"url": url, "title": now["title"],
                                        "price": now["price"]})
            continue
        if now["price"] != was["price"]:
            changes["price"].append({
                "url": url, "title": now["title"],
                "was": was["price"], "now": now["price"],
                "delta": now["price"] - was["price"],
            })
        # A listing that newly qualifies is the single most important thing this
        # project can tell you, so it is tracked separately from its price even
        # though a price move is usually what caused it.
        if now["qualifies"] and not was["qualifies"]:
            changes["qualified"].append({"url": url, "title": now["title"],
                                         "price": now["price"]})
        elif was["qualifies"] and not now["qualifies"]:
            changes["unqualified"].append({"url": url, "title": now["title"]})
        if now["in_stock"] != was["in_stock"]:
            changes["stock"].append({"url": url, "title": now["title"],
                                     "in_stock": now["in_stock"]})

    for url, was in before.items():
        if url not in after:
            changes["disappeared"].append({"url": url, "title": was["title"],
                                           "price": was["price"]})

    return changes


def is_empty(changes):
    """True when a diff found nothing worth mailing."""
    return changes is not None and not any(changes.values())


def format_changes(changes, previous, width=78):
    """The 'what moved' block that opens a digest. Lines, not a string.

    Placed above the ranking because it is the reason this email exists today
    rather than yesterday -- the ranking itself is unchanged from the last one
    in most respects, and burying the delta under it inverts that.
    """
    if changes is None:
        return ["WHAT MOVED - first digest from this database", "-" * width,
                "  Everything below is new to this log.", ""]

    since = previous["sent_at"][:16].replace("T", " ")
    lines = [f"WHAT MOVED - since the last digest, {since}", "-" * width]

    for item in changes["qualified"]:
        lines.append(f"  + NOW QUALIFIES  ${item['price']:.2f}  "
                     f"{_short(item['title'])}")
    for item in changes["unqualified"]:
        lines.append(f"  - no longer qualifies  {_short(item['title'])}")
    for item in changes["appeared"]:
        lines.append(f"  + new listing    ${item['price']:.2f}  "
                     f"{_short(item['title'])}")
    for item in changes["disappeared"]:
        lines.append(f"  - gone           was ${item['price']:.2f}  "
                     f"{_short(item['title'])}")
    for item in sorted(changes["price"], key=lambda c: c["delta"]):
        lines.append(f"  {'v' if item['delta'] < 0 else '^'} ${item['was']:.2f} "
                     f"-> ${item['now']:.2f} ({item['delta']:+.2f})  "
                     f"{_short(item['title'])}")
    for item in changes["stock"]:
        state = "back in stock" if item["in_stock"] else "out of stock"
        lines.append(f"  ! {state}  {_short(item['title'])}")

    lines.append("")
    return lines


def _short(title, limit=42):
    return report.short_title(title, limit)
