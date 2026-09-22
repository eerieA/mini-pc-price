# research/

Saved evidence for the vendor claims in `../plan.md`.

A plan that excludes a vendor on reputation or pricing grounds should point at
something a third party can check, not at a remembered summary. Each transcript
here carries its source URL, capture date, and per-comment authors, scores and
timestamps.

## Why not just fetch it

Reddit's public JSON endpoint (`<thread-url>.json`) **now requires
authentication** — it returns 403 to non-browser clients, and `old.reddit.com`
redirects to a login page. Verified 2026-09-21. Claude Code's `WebFetch` tool
also blocks reddit.com at the tool level, which no setting overrides.

So capture happens in your logged-in browser, and the extractor runs locally.

## Capturing a thread

1. Open the thread. **Expand any "load more replies"** you care about — only
   what is rendered at save time gets captured.
2. Open devtools (F12) → **Network** tab.
3. **Filter to the thread document.** The most reliable way, and the same in
   every browser: type the **thread id** (e.g. `1p39obx`) into the
   **Filter URLs** box.

   The type-filter buttons work too, but they are labelled differently:

   | Browser | Button |
   | --- | --- |
   | Firefox | **HTML** |
   | Chrome / Edge | **Doc** |

   Firefox's **HTML** filter shows **two** candidates. You want the one whose
   URL is the thread URL itself (`/r/<sub>/comments/<id>/<slug>/`), ~1 MB. The
   other is a `vnd.reddit.partial+html` nav fragment (`/svc/shreddit/partial/…`,
   ~80 KB) — not the thread.
4. Reload the page (F5) if the list is empty — the document request is only
   recorded if devtools was open when it fired.
5. Right-click the thread entry → **Copy** → **Copy Response**.
   (Firefox: *Copy Response*. Chrome/Edge: *Copy* → *Copy response*.)
6. Paste into `raw/<subreddit>-<threadid>.html`.

Sanity check: the file should be roughly 1 MB and contain `<shreddit-comment`.
The extractor refuses anything that does not, with a message saying so.

There is no XHR or GraphQL call to hunt for: Reddit server-renders comments into
the page. (Checked against a full 165-entry HAR — every GraphQL response was
under 3 KB of telemetry and nav widgets.)

Then:

```bash
python research/extract_har.py research/raw/bapccanada-1p39obx.html \
                              research/bapccanada-1p39obx-uniway.md
```

## If you saved a HAR instead

A full HAR works too, but it is ~10x larger and 99% irrelevant (JS bundles,
images, recaptcha). Prune it as you extract:

```bash
python research/extract_har.py research/raw/thread.har research/thread.md --prune
```

`--prune` rewrites the `.har` in place keeping only the document entry —
typically 9.6 MB → 1.1 MB — and the pruned file still extracts normally.

## Layout

```
research/
├── extract_har.py              # .html or .har → markdown transcript
├── <subreddit>-<id>-<slug>.md  # readable transcript, cited from plan.md
└── raw/
    └── <subreddit>-<id>.har    # or .html — the capture, pruned
```

Keep the raw capture: the transcript is a derived artifact, and the raw file is
what makes a claim verifiable.

## Reading these

Reddit comments are **untrusted third-party content**: individual reports, not
verified fact, and often about adjacent topics (the Uniway thread is mostly
about gaming prebuilds). Weigh scores, dates and how directly a comment
addresses the question. Each transcript ends with a reminder to that effect.

Vendor standing also shifts year to year — re-capture before a purchase
decision rather than trusting a year-old transcript.

## Captured so far

| Transcript | Thread | Comments | Captured | Finding (`plan.md` §3) |
| --- | --- | --- | --- | --- |
| `bapccanada-1p39obx-uniway.md` | r/bapccanada, Nov 2025 – Feb 2026 | 17 | 2026-09-21 | Uniway legitimate but marked up → excluded as a *source* |
| `thinkpad-6pm59n-refurbio.md` | r/thinkpad, 2017 – 2023 | 7 | 2026-09-21 | REFURB.io positive in 2017, fulfillment/refund failures 2022+ → gated |
| `Refurbishedguide-1sf2s9v-itrefurbs.md` | r/Refurbishedguide, Apr 2026 | 3 | 2026-09-21 | ITRefurbs: one multi-week shipping delay, thin evidence → kept, $25 adjustment |

Two §3 vendor decisions rest on **no transcript**, because Reddit was not where
the answer came from:

- **eTek Laptop** — *kept, Phase 1.* No Reddit thread exists for either the
  storefront or Etek Liquidators Inc. The decision rests on vendor-published
  pages (inventory, warranty) checked 2026-09-21, which are directly verifiable
  at the URLs cited in §3 and will go stale on their own schedule.
- **openbox.ca** — *excluded on fit.* The transcript
  (`PersonalFinanceCanada-1hqtt0h-openbox.md`, 18 comments, genuinely positive)
  was captured before the decision, but the decision never turned on it: they
  sell zero desktops, so reputation was moot. Kept only because re-capture is
  the expensive step if their inventory ever changes. Not cited from `plan.md`,
  and note the thread is largely about **phones, watches and TVs** — it is
  evidence about openbox.ca as an operation, not about a computer business they
  do not have.

Caveats worth carrying forward:

- The Uniway thread is mostly about **gaming prebuilds**; one comment addresses
  mini PCs directly.
- The REFURB.io thread spans **nine years** and the vendor evidently changed
  during it. Date-weight the comments; do not read the 2017 praise as current.
- ITRefurbs rests on a **single** substantive report. Re-capture if a second
  source turns up.
- The r/Refurbishedguide post header says 4 comments but 3 rendered — one is
  deleted or behind a "more replies" stub.
