"""SQLite storage: two tables, per plan.md §4.

`listings` is the current state of a product page, keyed on its URL.
`observations` is an append-only log of what it cost each time we looked.

The split matters: an observation cannot be backfilled. A price we failed to
record when the page was fetched is gone for good, which is the whole reason
this project polls on a schedule instead of reacting to change notifications
(§1). So every run writes one observation per listing, including runs where
nothing moved.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB = Path("data") / "tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    cd_watch_uuid   TEXT,
    title_raw       TEXT NOT NULL,
    canonical_key   TEXT,
    brand           TEXT,
    model           TEXT,
    chassis_key     TEXT,
    cpu             TEXT,
    cpu_cores       INTEGER,
    cpu_threads     INTEGER,
    nested_virt     INTEGER,
    ram_gb          INTEGER,
    ram_type        TEXT,
    ram_slots       INTEGER,
    ram_max_gb      INTEGER,
    storage_gb      INTEGER,
    storage_type    TEXT,
    seller          TEXT,
    seller_blocked  TEXT,
    fulfillment     TEXT,
    seller_rating   REAL,
    seller_reviews  INTEGER,
    returns_accepted INTEGER,
    ships_from      TEXT,
    condition       TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    parse_ok        INTEGER NOT NULL,
    parse_notes     TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY,
    listing_id        INTEGER NOT NULL REFERENCES listings(id),
    observed_at       TEXT NOT NULL,
    price             REAL NOT NULL,
    compare_at_price  REAL,
    price_max         REAL,
    currency          TEXT,
    shipping          REAL,
    shipping_estimated INTEGER,
    in_stock          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS observations_listing
    ON observations(listing_id, observed_at);
"""

# Written on insert, then never touched again — a listing's first sighting is not
# something a later run gets to revise.
_INSERT_ONLY = {"url", "source_id", "first_seen"}

_SPEC_COLUMNS = [
    "cd_watch_uuid", "title_raw", "canonical_key", "brand", "model",
    "chassis_key", "cpu", "cpu_cores", "cpu_threads", "nested_virt", "ram_gb",
    "ram_type", "ram_slots", "ram_max_gb", "storage_gb", "storage_type", "seller",
    "fulfillment", "seller_blocked", "seller_rating", "seller_reviews",
    "returns_accepted", "ships_from",
    "condition", "last_seen", "parse_ok", "parse_notes",
]

# Columns added after data/tracker.db already held history. CREATE TABLE IF NOT
# EXISTS never alters an existing table, and the database cannot be recreated
# -- its observations are the one thing here that cannot be re-fetched (§4) --
# so they are added in place. Nullable, so every earlier row reads as "not
# stated", which is what it was.
_ADDED_COLUMNS = {
    "listings": {"nested_virt": "INTEGER", "seller_blocked": "TEXT",
                 "returns_accepted": "INTEGER", "ships_from": "TEXT",
                 "condition": "TEXT"},
    "observations": {"price_max": "REAL", "currency": "TEXT",
                     "shipping": "REAL", "shipping_estimated": "INTEGER"},
}


def connect(path=DEFAULT_DB):
    """Open the database, creating it and its schema if absent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    return conn


def _add_missing_columns(conn):
    for table, columns in _ADDED_COLUMNS.items():
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def upsert_listing(conn, listing):
    """Insert a listing, or refresh the specs of one already seen. Returns its id.

    Everything except `first_seen` is overwritten on conflict: a vendor may
    correct a title or we may improve the parser, and the listing row is meant to
    describe the page as it is now. History lives in `observations`.
    """
    columns = list(_INSERT_ONLY) + _SPEC_COLUMNS
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c}=excluded.{c}" for c in _SPEC_COLUMNS)

    conn.execute(
        f"INSERT INTO listings ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(url) DO UPDATE SET {updates}",
        [listing.get(c) for c in columns],
    )
    row = conn.execute(
        "SELECT id FROM listings WHERE url = ?", (listing["url"],)
    ).fetchone()
    return row["id"]


def insert_observation(conn, listing_id, observed_at, price,
                       compare_at_price, in_stock, price_max=None, currency=None,
                       shipping=None, shipping_estimated=None):
    """Append one price observation. Never deduplicated, never conditional.

    Called for every listing on every run even when the price is unchanged — an
    unbroken series is what makes a 30-day median meaningful, and a run that
    skipped "boring" rows would leave a hole nothing can fill later.

    The keyword arguments are eBay's (§2, §4). None means the source does not
    state it: the Shopify sources publish no currency and no shipping. Ranking
    charges their shipping as 0, which §2 records as flattering them.
    """
    conn.execute(
        "INSERT INTO observations "
        "(listing_id, observed_at, price, compare_at_price, in_stock, "
        "price_max, currency, shipping, shipping_estimated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (listing_id, observed_at, price, compare_at_price, int(in_stock),
         price_max, currency, shipping,
         None if shipping_estimated is None else int(shipping_estimated)),
    )


def fetch_current(conn):
    """Every listing with its most recent observation, for report.py.

    Includes listings with parse_ok = 0: §9 requires every listing to appear in
    the output, and one silently dropped for failing to parse is indistinguishable
    from one the fetch never saw.
    """
    rows = conn.execute("""
        SELECT l.*, o.price, o.compare_at_price, o.in_stock, o.observed_at,
               o.price_max, o.currency, o.shipping, o.shipping_estimated
        FROM listings l
        JOIN observations o ON o.id = (
            SELECT id FROM observations
            WHERE listing_id = l.id
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
        )
        ORDER BY l.id
    """).fetchall()
    return [dict(r) for r in rows]
