"""Fetch a source, persist it, parse it, store it (plan.md §1, §9).

Phase 1 is one source on parsing tier 0 — a Shopify store's own product JSON —
so there is no ChangeDetection container and no selectors. Run it from a cron
entry; run it by hand as often as you like.

    python src/poll.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

import db
import specs

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
RAW = ROOT / "data" / "raw"


def load_config():
    def read(name):
        return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))
    return {
        "sources": read("sources.yaml")["sources"],
        "chassis": read("chassis.yaml"),
        "cpus": read("cpus.yaml"),
        "aliases": read("chassis_aliases.yaml"),
    }


def fetch(url):
    """Fetch the products JSON. Returns (products, raw_bytes)."""
    response = requests.get(url, timeout=30,
                            headers={"User-Agent": "mini-pc-price/0.1"})
    response.raise_for_status()
    # Explicitly utf-8: listing titles carry typographic punctuation and the
    # descriptions carry emoji, both of which fail under the cp1252 default (§9).
    return json.loads(response.content.decode("utf-8")), response.content


def save_raw(source_id, payload, fetched_at):
    """Write the response verbatim before anything parses it.

    Nothing reads this in the normal path. It exists so a parser bug found on day
    10 can be fixed against day 1's bytes without re-fetching (§7) — and because
    a crash in parsing should still leave the evidence on disk.
    """
    directory = RAW / source_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{fetched_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_bytes(payload)
    return path


def listing_from_product(product, source, config, fetched_at):
    """One Shopify product -> a listings row plus its price observation."""
    variant = product["variants"][0]
    parsed = specs.parse(product["title"], product.get("body_html", ""),
                         config["chassis"], config["cpus"], config["aliases"])

    notes = [parsed["parse_notes"]] if parsed["parse_notes"] else []
    if len(product["variants"]) > 1:
        # Ten single-variant products today. A second variant is a different
        # configuration at a different price, which needs its own row and so its
        # own key — a Phase 3 problem, flagged rather than silently collapsed.
        notes.append(f"{len(product['variants'])} variants; only the first is tracked")

    listing = {
        "source_id": source["id"],
        "url": f"{source['product_url_base']}/{product['handle']}",
        "title_raw": product["title"],
        "first_seen": fetched_at.isoformat(timespec="seconds"),
        "last_seen": fetched_at.isoformat(timespec="seconds"),
        "parse_notes": "; ".join(notes) if notes else None,
    }
    for field in ("canonical_key", "brand", "model", "chassis_key", "cpu",
                  "cpu_cores", "cpu_threads", "ram_gb", "ram_type", "ram_slots",
                  "ram_max_gb", "storage_gb", "storage_type"):
        listing[field] = parsed[field]
    listing["parse_ok"] = int(parsed["parse_ok"])

    observation = {
        "price": float(variant["price"]),
        # Vendor "was" price: marketing, not a market price, and sometimes below
        # the asking price. Stored unaltered and given no weight (§2) — a
        # "sanity check" that nulled the odd ones would destroy the evidence
        # that this field is noise.
        "compare_at_price": (float(variant["compare_at_price"])
                             if variant.get("compare_at_price") else None),
        "in_stock": bool(variant.get("available")),
    }
    return listing, observation


def poll_source(conn, source, config, fetched_at):
    products, payload = fetch(source["products_json"])
    raw_path = save_raw(source["id"], payload, fetched_at)

    if not products.get("products"):
        # Shopify answers a wrong or retired collection handle with HTTP 200 and
        # an empty array, which is indistinguishable from a sold-out collection.
        # Tier 0 removes selector breakage but not this: the handle IS the
        # selector (§8). Fail loudly rather than record "zero listings today".
        raise SystemExit(
            f"{source['id']}: 0 products returned. The collection handle may be "
            f"retired or wrong -- check {source['products_json']}\n"
            f"Response saved to {raw_path}"
        )

    parsed_ok = 0
    for product in products["products"]:
        listing, observation = listing_from_product(product, source, config,
                                                    fetched_at)
        listing_id = db.upsert_listing(conn, listing)
        db.insert_observation(conn, listing_id,
                              fetched_at.isoformat(timespec="seconds"),
                              observation["price"],
                              observation["compare_at_price"],
                              observation["in_stock"])
        parsed_ok += listing["parse_ok"]

    conn.commit()
    return len(products["products"]), parsed_ok, raw_path


def main():
    config = load_config()
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
    conn = db.connect(ROOT / "data" / "tracker.db")

    for source in config["sources"]:
        if "products_json" not in source:
            continue  # tier 0 only in Phase 1; HTML sources arrive with CD (§9)
        total, parsed_ok, raw_path = poll_source(conn, source, config, fetched_at)
        print(f"{source['id']}: fetched {total}, parse_ok {parsed_ok}/{total}, "
              f"raw -> {raw_path.relative_to(ROOT)}")

    conn.close()


if __name__ == "__main__":
    sys.exit(main())
