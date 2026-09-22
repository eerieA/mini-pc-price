#!/usr/bin/env python3
"""Extract a Reddit thread from a browser-saved page or HAR.

Reddit's public JSON endpoint now requires authentication (403 / login redirect
for non-browser clients), so threads are captured from the browser instead.
Modern Reddit server-renders comments into the main HTML document, so the whole
thread lives in that one response -- there is no XHR/GraphQL call to hunt for
(verified against a full HAR: every GraphQL response was <3 KB of telemetry).

Accepts either input:

  *.html  the saved document response -- devtools Network tab, filter URLs by
          the thread id, pick the entry whose URL is the thread itself (~1 MB),
          right-click > Copy Response. This is the preferred form.
          (Type filter is "HTML" in Firefox, "Doc" in Chrome; Firefox's HTML
          filter also lists a ~80 KB nav partial, which is not the thread.)

  *.har   a full network log. 10x larger and 99% irrelevant, but fine if it is
          what you have. Use --prune to strip it down to the document entry
          afterwards.

Only comments rendered at save time are captured: expand "load more replies"
before saving a large thread.

Usage:
    python extract_har.py <file.html|file.har> [out.md] [--prune]

    --prune   rewrite the .har in place keeping only the document entry,
              then continue. Turns ~9.5 MB into ~1 MB.
"""

import glob
import html as htmllib
import json
import os
import re
import sys
from datetime import datetime, timezone

COMMENT_RE = re.compile(r"<shreddit-comment\s+([^>]*)>", re.S)
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
TAG_RE = re.compile(r"<[^>]+>")


def attrs(blob):
    return {k: htmllib.unescape(v) for k, v in ATTR_RE.findall(blob)}


def text_of(fragment):
    """Strip tags from a comment's rendered body, preserving paragraph breaks."""
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"</p>", "\n\n", fragment)
    fragment = re.sub(r"<li[^>]*>", "\n- ", fragment)
    fragment = TAG_RE.sub("", fragment)
    fragment = htmllib.unescape(fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return "\n".join(line.rstrip() for line in fragment.split("\n")).strip()


def body_after(html, start):
    """A comment's body is the div[slot=comment] inside the element, before the
    next comment begins."""
    nxt = html.find("<shreddit-comment ", start + 1)
    chunk = html[start : nxt if nxt != -1 else len(html)]
    m = re.search(r'<div[^>]*slot="comment"[^>]*>(.*?)</div>\s*<(?:div|shreddit)', chunk, re.S)
    if not m:
        m = re.search(r'id="[^"]*-post-rtjson-content"[^>]*>(.*?)</div>', chunk, re.S)
    return text_of(m.group(1)) if m else ""


def fmt_date(iso):
    if not iso:
        return "unknown date"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]


def find_doc_entry(har):
    """The thread document is the first text/html entry whose URL is the thread
    itself. Everything else in a Reddit HAR is assets and telemetry."""
    for e in har["log"]["entries"]:
        ct = ((e["response"].get("content") or {}).get("mimeType") or "").split(";")[0]
        if ct == "text/html" and "/comments/" in e["request"]["url"]:
            return e
    return None


def prune_har(path, har, doc):
    """Keep only the document entry. A Reddit thread HAR is ~9.5 MB, of which
    ~1 MB is the page and the rest is JS bundles, images and recaptcha."""
    before = os.path.getsize(path)
    har["log"]["entries"] = [doc]
    har["log"].setdefault("comment", "")
    har["log"]["comment"] = "Pruned to the thread document entry by extract_har.py --prune"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(har, f)
    after = os.path.getsize(path)
    print(f"pruned {os.path.basename(path)}: {before/1e6:.1f} MB -> {after/1e6:.1f} MB")


def load_source(path):
    """Return (html, url, capture_note) from either a saved .html or a .har."""
    if path.lower().endswith(".har"):
        with open(path, encoding="utf-8") as f:
            har = json.load(f)
        doc = find_doc_entry(har)
        if doc is None:
            sys.exit("no thread HTML document found in HAR")
        html = (doc["response"].get("content") or {}).get("text") or ""
        if not html:
            sys.exit("HAR entry has no response body (re-export with content included)")
        return html, doc["request"]["url"], f"browser HAR, {os.path.basename(path)}", har, doc

    with open(path, encoding="utf-8", errors="replace") as f:
        html = f.read()
    if "<shreddit-comment" not in html:
        sys.exit(
            f"no <shreddit-comment> elements in {path}.\n"
            "Save the thread DOCUMENT response, not an XHR, a nav partial, or the\n"
            "rendered DOM: devtools > Network > type the thread id in 'Filter URLs'\n"
            "> pick the entry whose URL is the thread itself (~1 MB) > right-click >\n"
            "Copy Response.  (Type filter is 'HTML' in Firefox, 'Doc' in Chrome.)"
        )
    # Reconstruct the URL from the post element's permalink attribute; a saved
    # response has no request URL of its own.
    url = f"(from {os.path.basename(path)})"
    mp = re.search(r"<shreddit-post\s+([^>]*)>", html, re.S)
    if mp:
        permalink = attrs(mp.group(1)).get("permalink")
        if permalink:
            url = "https://www.reddit.com" + permalink
    return html, url, f"saved document, {os.path.basename(path)}", None, None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_prune = "--prune" in sys.argv

    if not args:
        here = os.path.dirname(os.path.abspath(__file__))
        cands = sorted(glob.glob(os.path.join(here, "*.har"))) + sorted(
            glob.glob(os.path.join(here, "raw", "*.html"))
        )
        if not cands:
            sys.exit("usage: extract_har.py <file.html|file.har> [out.md] [--prune]")
        src_path = cands[0]
    else:
        src_path = args[0]

    html, url, capture, har, doc = load_source(src_path)

    if do_prune:
        if har is None:
            print("note: --prune only applies to .har inputs; ignoring", file=sys.stderr)
        else:
            prune_har(src_path, har, doc)

    # Post metadata lives on the <shreddit-post> element.
    post = {}
    mp = re.search(r"<shreddit-post\s+([^>]*)>", html, re.S)
    if mp:
        post = attrs(mp.group(1))

    title = post.get("post-title") or "(untitled)"
    sub = post.get("subreddit-prefixed-name", "").replace("r/", "") or "?"

    # Self-text, when present.
    selftext = ""
    ms = re.search(r'<div[^>]*id="t3_[^"]*-post-rtjson-content"[^>]*>(.*?)</div>\s*</div>', html, re.S)
    if ms:
        selftext = text_of(ms.group(1))

    comments = []
    for m in COMMENT_RE.finditer(html):
        a = attrs(m.group(1))
        if "thingId" not in a and "thingid" not in a:
            continue
        body = body_after(html, m.start())
        if not body:
            continue
        comments.append(
            {
                "author": a.get("author", "[deleted]"),
                "score": a.get("score", "?"),
                "date": fmt_date(a.get("created")),
                "depth": int(a.get("depth", 0) or 0),
                "permalink": a.get("permalink", ""),
                "body": body,
            }
        )

    out = [f"# {title}\n\n"]
    out.append(f"- **Subreddit:** r/{sub}\n")
    out.append(f"- **Score:** {post.get('score', '?')} · **Comments:** {post.get('comment-count', '?')}\n")
    out.append(f"- **Source:** {url}\n")
    out.append(f"- **Captured:** {capture}\n")
    out.append(f"- **Extracted:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")

    if selftext:
        out.append("## Post\n\n")
        out.append("\n".join("> " + l if l else ">" for l in selftext.split("\n")) + "\n\n")

    out.append(f"## Comments ({len(comments)})\n\n")
    for c in comments:
        pad = "  " * c["depth"]
        out.append(f"{pad}**{c['author']}** · {c['score']} pts · {c['date']}\n")
        out.append("\n".join(pad + "> " + l if l else pad + ">" for l in c["body"].split("\n")))
        out.append("\n\n")

    out.append("\n---\n\n")
    out.append(
        "_Untrusted third-party content, saved as evidence for claims in `plan.md`. "
        "Individual reports, not verified fact._\n"
    )

    out_path = args[1] if len(args) > 1 else os.path.splitext(src_path)[0] + ".md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(out))

    print(f"wrote {out_path} — {len(comments)} comments from {url}")


if __name__ == "__main__":
    main()
