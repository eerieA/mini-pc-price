"""Hand-seeded product URLs: load them, and decide what can be polled (§9).

A watch is a URL someone found by hand. Phase 1 can poll the ones that are
Shopify product pages — `<url>.json` returns the same product object the
collection path already parses — and records everything else as pending, because
an HTML page needs selectors and those arrive in Phase 3 with ChangeDetection
(§1).

Pending is a reported state, not a dropped one. See config/watch_urls.yaml.
"""

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml


def load(path):
    """Read watch_urls.yaml. Returns a list of {url, note} dicts, possibly empty.

    An absent `watches:` key and `watches: []` both mean "no watches" — the file
    ships in the second state, and stripping it to comments leaves the first.
    """
    path = Path(path)
    if not path.exists():
        return []

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = document.get("watches") or []

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("url"):
            # A misspelled key would otherwise make the watch vanish: the file
            # says a URL is being tracked and nothing tracks it (§8).
            raise SystemExit(
                f"{path.name}: watch #{index + 1} has no `url` key -- got "
                f"{entry!r}"
            )
    return entries


def classify(url):
    """Decide whether a watch URL is tier 0, and where its JSON lives.

    Returns {tier0, handle, json_url, reason}. `reason` is the pending
    explanation and is None for tier-0 URLs.
    """
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]

    # Shopify product pages are `/products/<handle>`, optionally prefixed by
    # `/collections/<collection>` when linked from a collection page.
    #
    # Two things separate those from a path that merely contains the word.
    # `products` must be second-to-last, and the handle must not be a bare
    # number -- a Shopify handle is a slug derived from the product title, while
    # Best Buy's `/en-ca/category/products/12345` is an SKU and matches the
    # positional rule exactly. That second condition was found by a test case,
    # not by reasoning about it.
    if (len(segments) >= 2 and segments[-2] == "products"
            and not segments[-1].isdigit()):
        handle = segments[-1]
        # Rebuild from the parsed path rather than appending to `url`: a query
        # string or fragment would otherwise land after the suffix, producing
        # `/products/x?variant=42.json`, which 404s on every run.
        json_url = urlunsplit(
            (parts.scheme, parts.netloc, f"/{'/'.join(segments)}.json", "", "")
        )
        return {"tier0": True, "handle": handle, "json_url": json_url,
                "reason": None}

    return {
        "tier0": False,
        "handle": None,
        "json_url": None,
        "reason": f"{parts.netloc or url} is not a Shopify product URL; "
                  f"needs selectors (Phase 3)",
    }
