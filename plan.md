# Mini-PC Deal Tracker — Plan (v2)

> v1 of this plan is preserved as `plan-v1.md`. This revision keeps its good
> structural instincts (ChangeDetection.io for the messy web plumbing, SQLite,
> aggressive hardware normalization, phased rollout) and cuts the parts that are
> over-built for a single-user tool with one concrete buying decision behind it.

## The actual goal

Buy **one** cost-efficient mini PC capable of hosting an EVE-NG lab of 5–6 nodes.
Find it at a good price, within a few weeks, without babysitting retailer pages.

Everything below is subordinate to that. This is not a general-purpose price
intelligence platform; it's a filter that emails me once a day.

---

## 1. Architecture

ChangeDetection.io owns the messy web; a small Python package owns the domain
logic. Same boundary as v1 — but the integration is a **pull**, not a webhook.

```text
  ┌────────────────────────────────┐
  │      ChangeDetection.io        │   docker, one container
  │                                │
  │  fetch / JS render / cookies   │
  │  CSS / XPath / JSON / jq       │
  │  per-retailer selectors        │
  │  scheduled checks + history    │
  └───────────────┬────────────────┘
                  │
                  │  poll.py reads the REST API every 30 min
                  │  GET /api/v1/watch
                  │  GET /api/v1/watch/<uuid>/history
                  ▼
  ┌────────────────────────────────┐
  │      deal tracker (python)     │   two cron jobs, no service
  │                                │
  │  parse specs from snapshot     │
  │  canonical product key         │
  │  write observations → SQLite   │
  └───────────────┬────────────────┘
                  │
                  │  digest.py, daily 08:00
                  ▼
  ┌────────────────────────────────┐
  │  filter → rank → one email     │
  └────────────────────────────────┘
```

### Why pull instead of the webhook

v1 §4 made ChangeDetection POST to a FastAPI endpoint. Two problems:

1. **The webhook fires on *change*, not on *state*.** The observation record is
   supposed to say what each listing cost at each check, including the checks
   where nothing moved. Change events give a record with holes exactly where the
   price was stable — which is most of the time — and every price comparison in
   §2 reads from that record: the 30-day median most obviously, but also
   `compare_at_price` (a vendor can revise it without touching the price),
   `in_stock` transitions, and any later reconstruction of what a listing cost on
   a given day.
2. **The payload is unstructured text.** `diff` / `current_snapshot` are text
   blobs; extracting a price from them means owning the parsing anyway, which is
   the thing the boundary was supposed to avoid.

Polling the REST API gives every observation, structured, with no delivery,
retry, or ordering concerns, and it can be re-run against stored data while
debugging. Retail prices do not move fast enough for 30-minute latency to matter.

The webhook stays available as a later low-latency nudge if polling ever proves
too slow. It is not needed for v1.

### Why there is no web service

Once the flow is pull-based, the entire system is:

```text
every 30 min   poll.py     CD API → parse → SQLite
daily 08:00    digest.py   SQLite → filter → email
```

Two cron entries and a package. No FastAPI, no request handling, no webhook
endpoint, no raw-event table. A browsing UI can be added later if wanted; it is a
want, not a requirement.

---

## 2. The decision rule: a filter, not a score

v1 §9 proposed a five-term weighted formula with nested sub-weights. Cut it. With
a handful of listings there is no way to know whether `0.40` or `0.35` on
HardwareValue is correct — the number is unfalsifiable and un-tunable, and every
alert it produces is uninterpretable.

### One gate, then tunable requirements

An earlier draft made all four requirements hard exclusions. That was wrong in
both directions: it treated one genuinely binary constraint and three
priced preferences as the same kind of thing, and it left no way to adjust the
bar as real inventory turned out denser or thinner than expected.

Only one requirement is truly absolute:

| Gate | Why no price makes it acceptable |
| --- | --- |
| Nested virtualization (VT-x + EPT, or AMD-V + RVI) | EVE-NG runs VMs inside a VM. Without it the machine does not do the job at all. |

The rest are **preferences with a price attached**. A machine that caps at 32 GB
is not unusable for a 5–6 node lab — it is worse at it, and worth less. Saying
how much less is exactly what §2 already does for vendor risk, so the same
mechanism applies:

```yaml
# config/rules.yaml

gates:                        # fail → excluded at any price
  nested_virt: true

requirements:                 # fail → penalised, still ranked
  ram_max_gb:    {min: 64,   else_penalty: 120}
  cpu_cores:     {min: 6,    else_penalty: 80}
  storage_nvme:  {min: true, else_penalty: 40}

surface_near_misses: true     # digest section for single-requirement failures
```

**This is the dial.** If two weeks of data show far more qualifying machines than
expected, raise `min` — the bar moves and the digest gets shorter. If almost
nothing qualifies, lower it, or lower the `else_penalty` so near-misses compete
more readily. `min` changes what counts as good; `else_penalty` changes how much
the shortfall costs. Both are one-line edits in config, and neither touches code.

An aggressively discounted machine that caps at 32 GB now lands at `price + 120`
and competes on the same axis as everything else, instead of vanishing from a
digest that would never explain why.

> **`else_penalty` is the same species of number as `source_adjustment`** — a
> subjective dollar figure, not a derived one (see the note below).

### Then rank by effective cost

The single most valuable change from v1: **score post-upgrade cost, not the
listing as shipped.**

```text
effective_price = listing_price
                + cost_to_reach(64 GB RAM)      -- where the chassis allows it, §5
                + cost_to_reach(NVMe, if absent)
                + requirement_penalties         -- unmet requirements, above
                + fulfillment_adjustment        -- marketplace seller risk, §3
                + source_adjustment            -- vendor recourse risk, §3
```

`cost_to_reach` and `requirement_penalties` are mutually exclusive per
requirement: if the chassis can reach 64 GB, the listing pays the *real* cost of
the SODIMMs; if it cannot, it pays `else_penalty` instead. A machine is never
charged both for an upgrade and for failing to be upgradeable.

RAM is upgradeable and cheap. A 16 GB M920q at $300 plus $60 of DDR4 SODIMM beats
a 32 GB one at $400. v1 §19 poses exactly this question and the v1 scoring model
cannot express it, because it scores what the retailer listed. This does.

`fulfillment_adjustment` covers marketplace seller risk; `source_adjustment`
covers how much recourse a vendor actually offers if the unit is faulty. Both are
`0` for vendors with clean warranty and return paths, and both are defined in §3.

> **What the adjustments are, exactly.** They are **subjective dollar penalties —
> what I would pay to avoid dealing with that vendor's failure mode — not
> expected values.** Some are anchored to a published number (eTek's restocking
> fee), some are pure preference (`seller_fulfilled: 50`); once written down they
> are the same kind of thing and get added together as such. No claim is made
> that any of them equals probability × cost, and none has been calibrated
> against an outcome. Their job is to make "cheaper but riskier" and "dearer but
> safer" land on one axis so the digest can rank them, and to put the number
> somewhere I can argue with it. Treat a $15 gap between two effective prices as
> noise.

Upgrade part costs live in `config/parts.yaml` and get refreshed by hand
occasionally — they move slowly.

### Then flag: is this cheap for what it is?

Ranking by `effective_price` answers "which of these is cheapest." It does not
answer "is any of them actually a *deal*" — for that the price needs a baseline.

**30-day median of the listing's own history — the weakest, and v1 overrated it.**

v1 §10 called this "where this gets really powerful." It is the one idea from the
scoring section worth keeping, but it has two problems, and the second is worse
than the first:

1. *It rarely exists.* It needs 30 days of observations on a listing still in
   stock, and refurbisher inventory turns over in days to weeks. Many listings
   disappear before they have a median, and it becomes broadly available around
   the time the machine has probably already been bought.
2. *It is self-referential.* It measures a listing against **its own** recent
   past, so it cannot tell whether the price was ever good. A machine overpriced
   for a month that drops 10% looks like a deal; one that was correctly cheap the
   whole time looks like nothing at all.

Keep recording it — observations are free and cannot be backfilled — but it is a
tiebreaker, not the signal.

**`compare_at_price` — cheap, weak, and worth capturing from Phase 1.**

Shopify returns a vendor "was" price per variant, already present in the tier-0
JSON (§5) and currently unused. Store it and print it.

It is *marketing*, not a market price: often the MSRP of a long-discontinued SKU,
sometimes simply inflated to manufacture a discount. It gets no weight in the
ranking and never gates anything. The reason to capture it in Phase 1 anyway is
that it costs one nullable column and cannot be recovered later — an observation
not recorded is gone.

**Street price per `canonical_key` — the one actually wanted, and it arrives with
eBay.**

The real question is "does this configuration normally sell for $450?" That is a
property of the *market*, not of one listing or one vendor's marketing, and it is
what catches a genuinely underpriced machine on its first appearance — no history
required. eBay sold/completed listings are the honest source, so this lands in
Phase 4 (§9) with the eBay integration, keyed by `canonical_key`.

Until then, **cross-vendor comparison at a single moment** is the working
substitute: the same `canonical_key` priced by two vendors today (§9, Phase 3).
Thinner than a real street price — two vendors are not a market — but it needs no
history and it fires immediately.

Order of trust, then: street price (Phase 4) > cross-vendor (Phase 3) >
`compare_at_price` (Phase 1) > 30-day median. The digest shows whichever exist
and labels which is which, so a flag is never mistaken for more evidence than it
carries.

If this proves inadequate after real data exists, add scoring in v3 — with
evidence.

---

## 3. Sources

Start with **two or three sources**, not twelve.

Begin with **eTek Laptop** — a Shopify store whose Tiny/Mini/USFF collection is
entirely the target segment and is available as structured JSON at a stable URL
(§5, tier 0). Nothing to scrape, nothing to break silently. That makes it the
source least likely to fail for reasons unrelated to the pipeline itself, which
is the only thing Phase 1 is testing.

**Dell Canada Outlet** — manufacturer-refurbished, real warranty, unambiguous
recourse — is the better vendor to buy from and follows in Phase 3b alongside the
other direct retailers. It is not the easier one to build against.

The rest of the independent refurbishers follow in Phase 3. They carry the
cheaper inventory, but they are also where vendor reputation has to be judged
rather than assumed — see below.

```yaml
# config/sources.yaml
sources:
  - id: dell_outlet
    name: Dell Canada Outlet
    type: manufacturer_outlet
    discovery_urls: [...]
    selectors:
      card: ".product-item"
      title: ".product-title"
      price: ".price"
      availability: ".stock-status"

  - id: etek
    name: eTek Laptop (Etek Liquidators Inc., Saint-Laurent QC)
    type: refurbisher
    # Shopify: structured JSON, no selectors to maintain or silently break (§8).
    products_json: "https://www.eteklaptop.ca/collections/tiny-computer-and-mini-pcs-usff/products.json?limit=250"
    discovery_urls:
      - "https://www.eteklaptop.ca/collections/tiny-computer-and-mini-pcs-usff"
    # No `selectors:` key — the JSON path in §5 handles this source end to end.

  - id: itrefurbs
    name: ITRefurbs
    type: refurbisher
    discovery_urls: [...]
    selectors: {...}

# Excluded, with reasons — see below. Kept as comments so the decisions are
# visible to anyone reading the config rather than only the plan.
#   uniway    — legitimate, but consistently marked up
#   refurb_io — fulfillment and refund failures, 2022 onward
#   openbox   — good reputation, but sells zero desktops; consumer returns only
```

### Vendor reputation is a per-source input, not a hidden assumption

Two distinct problems get confused here, so keep them apart:

- **Is the vendor's pricing worth tracking at all?** A legitimate vendor that is
  reliably above market is a source that costs maintenance and returns nothing.
- **What is the risk of buying from them?** That is a cost, and §2 already prices
  costs in dollars.

Only the second is what `source_adjustment` is for. The first is a reason not to
add a source at all, and it has nothing to do with trust.

Every claim below cites a saved transcript in `research/` — see
`research/README.md` for how they were captured and how to read them.

#### Uniway — excluded, for markup, not trust

Uniway was originally picked as a convenient Phase 1 scraping target, not because
it had been vetted. When it was checked, the evidence said something other than
expected: **Uniway is legitimate.** Three separate commenters confirm it — real
physical stores, a real business, refurb office PCs as the main line.

The consistent complaint is **price**:

> "Their pricing is not that great though." — *SadEyesHappyFaces, 2025-11-22*

> "Their Lenovo mini PC was 200 and I just bought one similar specs on eBay for
> 120 … From what I see they're prices are marked up a ton."
> — *FigNew2679, 2026-02-01*

That second one is the only comment in the thread about a **mini PC**
specifically, and it reports roughly a 60% markup against a comparable unit.

A deal-finding tool exists to surface cheap listings. A vendor that is reliably
expensive is not a bad *vendor* — it is a bad *source*, and adding it costs
selector maintenance (§8) for listings that will never rank. Excluded on those
grounds, which also means the reason expires cleanly: if Uniway's pricing becomes
competitive, add it back.

`research/bapccanada-1p39obx-uniway.md` — note the thread is mostly about gaming
prebuilds, so only part of it bears on this.

#### REFURB.io — excluded, as a gate

This is the one where the evidence changed the decision. Reputation here is not
static, and the dates matter more than the sentiment:

- **2017** — positive. Reputable, stands behind what it sells, one account of a
  faulty unit handled well ("great customer service … not wasting your time
  trying to troubleshoot on the phone").
- **2022** — missed a guaranteed 3–7 day window, shipped on day 6+, support
  responded only with tracking numbers.
- **2023** — an order that sat unfulfilled for weeks, was cancelled because the
  vendor was out of stock on a *charger*, and took two more weeks to refund:
  "They lie about what's in stock … Avoid this company at all costs." A separate
  account the same month describes the vendor alleging fraud against the customer
  over a charge that had already cleared.

The nine-year span is the finding. This is not a vendor with slow support; it is
one whose fulfillment appears to have degraded badly sometime after 2017. Earlier
descriptions of REFURB.io as "legit, just slow" were accurate — about the 2017
era.

**That is a gate, not an adjustment.** `source_adjustment` prices the risk that a
unit arrives faulty and the return is painful. It does not price "the order may be
cancelled after three weeks and the refund may take two more" — no plausible
dollar figure makes that worth chasing a discount for one purchase.

`research/thinkpad-6pm59n-refurbio.md`

#### eTek Laptop — kept, and the best-structured source in the plan

Etek Liquidators Inc., Saint-Laurent QC, incorporated 1997, with a physical
storefront at 1055 rue Bégin. Checked 2026-09-21.

Their Tiny/Mini/USFF collection **is** the target segment rather than merely
containing it — ten units, all CAD, six of them 8th-gen Intel or newer:

| Model | CPU | RAM | Price |
| --- | --- | --- | --- |
| Dell OptiPlex 5060 | i5-8500 | 16 GB | $299.99 |
| HP ProDesk 400 G5 Mini | i5-9500T | 16 GB | $324.99 |
| Dell OptiPlex 3070 Micro | i5-9500T | 16 GB | $349.99 |
| Dell OptiPlex 7070 Micro | i5-9500T | 16 GB | $349.99 |
| HP EliteDesk 800 G4 Mini | i7-8700T | 16 GB | $424.99 |
| Dell OptiPlex 3080 Ultra | i5-10500T | 16 GB | $429.99 |
| Dell OptiPlex 3080 Ultra | i5-10500T | 32 GB | $549.99 |

The 7070 Micro at $350 lands near $520 effective once it reaches 64 GB, which is
competitive with anything else in the plan.

**The reason to prioritise it is structural, not the prices.** It runs Shopify,
so `…/products.json?limit=250` returns title, price, `compare_at_price`, SKU and
an `available` boolean per variant. Verified returning valid JSON for all ten
products. That eliminates the single largest ongoing failure mode in §8 — silent
selector breakage — for this source entirely, and it reduces `specs.py` to title
parsing (§9, Phase 1).

**The return policy is the worst of any vendor kept:**

- 15-day return window
- **30% restocking fee** on non-defective returns
- Customer pays return shipping, insurance and fees
- Prior approval required, or the return is refused at the warehouse
- 60-day warranty (against Dell Outlet's manufacturer warranty)

30% of a $350 machine is **$105**. That is not a slow-shipping annoyance like
ITRefurbs — it is a quantified cost on the failure mode that matters most here,
because the binding requirements (nested virtualisation, the chassis RAM ceiling)
are only fully verifiable after the box is open. Hence `source_adjustment: 40`
below, which unlike ITRefurbs' $25 is anchored to a published number.

**Evidence caveat, and it cuts both ways.** eTek has *no Reddit presence* — no
thread turned up for either the storefront or the corporate name. The 4.7★ Google
rating is cited on their own marketing page and could not be independently
corroborated. So: no complaints found, but also no independent scrutiny found.
That is thinner evidence than ITRefurbs has, in the opposite direction, and it is
a weaker basis than a vendor with public discussion either way.

#### ITRefurbs — kept, with an adjustment and thin evidence

One substantive firsthand report (2026-04-15): three weeks after ordering, no
shipping or tracking, repeated "expect to ship today" estimates that did not
hold, with the most recent delay attributed to QC finding a fault and repairing
it. The buyer was notably charitable about it —

> "I'm happy their QC is repairing it before shipping, but so far the experience
> is not what I was expecting."

— and reported no resolution either way. No corroborating account.

**One data point is not a pattern**, and the failure described is slow shipping,
not a failure to deliver or refund. That is exactly what a dollar adjustment is
for. Kept, priced, and flagged for re-checking.

`research/Refurbishedguide-1sf2s9v-itrefurbs.md`

#### The resulting configuration

```yaml
# config/sellers.yaml
source_adjustment:            # dollars added to effective_price (§2)
  dell_outlet:       0        # manufacturer warranty, clear RMA path
  canada_computers:  0        # physical locations, easy returns
  memory_express:    0
  etek:             40        # 30% restocking fee on non-defective returns
  itrefurbs:        25        # one report of multi-week shipping delays
```

Per the note in §2, these are subjective penalties, not expected values. eTek's
$40 is anchored to a published number (a 30% restocking fee is ~$105 on a $350
unit); ITRefurbs' $25 is what a slow, uncertain RMA is worth avoiding. Neither is
derived and neither has been calibrated. They exist so a genuinely cheap listing
can still win — which a blanket exclusion would not allow — and so the penalty is
visible in the digest and editable in one line.

A vendor whose problems are bad enough to be a *gate* rather than a price simply
does not appear in `sources.yaml`. That is the REFURB.io case. A vendor that is
merely expensive does not appear either, for an unrelated reason. That is the
Uniway case.

A vendor that sells nothing in the target segment does not appear either, for a
third unrelated reason. That is the **openbox.ca** case: checked 2026-09-21 after
a positive 18-comment thread, and their entire Windows computer collection is 19
laptops and 2-in-1s with zero desktops. Their supply is consumer returns — TVs,
phones, laptops — not corporate lease returns, which is where mini PCs come from.
A fit exclusion is the cleanest kind, since it does not require weighing anecdotes
at all. A transcript exists (`research/PersonalFinanceCanada-1hqtt0h-openbox.md`)
but is not cited here — the reputation question never became load-bearing.

This leaves **two independent refurbishers**, eTek and ITRefurbs, both carrying a
non-zero `source_adjustment` — the pattern, not a coincidence. Two searches
produced exactly one new viable name, which is itself evidence about the size of
the pool.

> **The exclusions are not all equally solid, and the difference matters more
> than the sentiment does.** Two rest on facts anyone can re-verify at a URL:
> openbox.ca sells no desktops (fit), and Uniway is consistently above market
> (price). Those are durable. The other two — REFURB.io's gate, ITRefurbs' $25 —
> rest on three Reddit comments and one Reddit comment respectively, saved under
> `research/` so they can be re-read or challenged. That is not a pattern; it is
> an anecdote with a dollar sign attached, and it is held loosely. The eTek and
> openbox.ca claims rest on vendor-published pages plus third-party aggregators,
> checked 2026-09-21, not on transcripts: the inventory and policy facts are
> verifiable at the URLs cited, the reputation signals are not corroborated.
> Vendor standing shifts — re-check before a purchase decision rather than
> trusting a year-old assessment of either kind.
>
> **This line of research is closed.** Two searches produced exactly one new
> viable name, and the marginal thread is worth less than the marginal listing.
> Re-open only if a specific vendor becomes load-bearing for a specific decision.

URLs and selectors live in config, never in code (v1 §3 was right about this).

Direct retailers (Canada Computers, Memory Express, Staples, Lenovo/Dell outlet)
are a reasonable Phase 3 addition once the pipeline is proven.

### Marketplaces belong in, but discovery works differently

Marketplaces (eBay.ca, Amazon.ca, Walmart.ca, Best Buy Marketplace) stay — Phase
4. Refurbishers price against their own margins; marketplace sellers are often
liquidators clearing lots, which is where the cheap outliers actually are.
Cutting them loses the tail that makes this worth building.

**eBay is the correction to the thin-refurbisher finding above, and it was
missing from v1 entirely.** §3 concluded that two searches produced exactly one
new viable refurbisher and that the cheap-inventory thesis probably rests on
marketplaces — eBay is where that inventory actually is. Off-lease corporate
Tiny/Micro/Mini machines arrive in Canada mostly through liquidators selling on
eBay, which is precisely the segment this whole plan targets. The Uniway markup
evidence in §3 makes the point unintentionally: the $120 comparison that showed
Uniway marked up ~60% was *an eBay listing*.

It is also the easiest marketplace to integrate, which makes its absence from
both plans the worst kind of omission — most value, least effort:

- The **Browse API** returns structured JSON for a search query: title, price,
  condition, seller username, feedback score and percentage, shipping and
  returns. No scraping, no selectors, no anti-bot defenses — the same structural
  advantage that earned eTek the Phase 1 slot (§5, tier 0), on the source with
  the most inventory.
- Seller reputation arrives *as fields*, so the gates in `sellers.yaml` below
  apply directly with nothing to parse.
- It needs a free developer account and an OAuth client-credentials token. That
  is the entire cost, and it is the only source in the plan needing credentials.

The catches are real but bounded: listing titles are keyword-stuffed and
inconsistent, so tier-3 regex parsing (§5) does more work here than anywhere else
and `parse_ok = false` will be common at first; condition varies far more than
with a refurbisher; and auctions are a different purchase mode than the buy-now
assumption running through §2 — filter to fixed-price listings and treat auctions
as out of scope. None of that is a reason to defer it behind Amazon.

The real obstacle is **discovery, not value** — these are separate problems and
should not be conflated:

- Amazon search pages are heavily anti-bot: rotating DOM classes, CAPTCHA on
  datacenter IPs, session-varying layout. ChangeDetection's browser fetcher gets
  further than plain HTTP, but it is ongoing maintenance and the single source
  most likely to break silently (see §8).
- Walmart.ca is somewhat easier.
- Best Buy Marketplace is easiest — marketplace items sit on the same product-page
  structure as Best Buy direct, so it rides on the Phase 3 parser for free.

A **product page fetched by URL is far less defended than a search results page.**
So: don't out-engineer Amazon's bot defenses to find candidates. Let candidates
arrive by other means, and point the pipeline at product URLs, where it does the
part that is genuinely ours — spec parsing, upgrade math, cross-retailer dedup.

Two discovery mechanisms, neither requiring search-page scraping:

1. **Manual seeding (from Phase 1).** A `watch_urls:` list in config. Spot an
   interesting seller or listing, paste the URL, it gets tracked from then on.
   Zero discovery engineering, composes with everything else.
2. **Platform-native alerting as the discovery layer (Phase 4).** Amazon's own
   watchlist price-drop notifications, or camelcamelcamel, surface candidates;
   the URL lands in the watch list and the normal pipeline takes over.

Direct marketplace search scraping with a browser fetcher and proxying stays
available as a last resort. It is the option to avoid — highest maintenance, and
its failure mode is silent.

### Seller handling: gates and a dollar adjustment

v1 §12 modelled seller quality as a continuous multiplier feeding a weighted
score — `f(rating, review_count, return_policy, warranty, fulfillment,
known_seller)`, six inputs and no way to validate the shape. That has the same
unfalsifiable-weights problem as v1 §9.

For a **single** purchase, seller risk is not a gradient traded off against $20.
Either the seller is acceptable or they are not. So the need is real but the tool
was wrong:

```yaml
# config/sellers.yaml

gates:                          # fail any → excluded, regardless of price
  min_rating: 4.5
  min_reviews: 100
  returns_accepted: true

fulfillment_adjustment:         # dollars added to effective_price (§2)
  amazon_fulfilled:   0         # A-to-z guarantee covers the risk
  bestbuy_fulfilled:  0
  walmart_fulfilled:  0
  seller_fulfilled:  50         # risk premium for a harder return
  no_return_window: excluded    # a gate, not a price
```

Gates handle the bad-seller problem, which is genuinely binary. The adjustment
handles residual risk among sellers who clear the gates. No weights anywhere.

The `50` is a statement about **me**, not about the market: it is what a painful
third-party return is worth avoiding. A $379 seller-fulfilled listing lands at
$429 effective and can be compared directly against ITRefurbs at $399 — the
arithmetic is visible in the digest (§6) rather than buried in a coefficient.

**Planned evolution.** The adjustment is a flat per-fulfillment-type lookup for
now. The intent is to make it a function of seller reputation later:

```yaml
fulfillment_adjustment:
  seller_fulfilled:
    base: 50
    scale_by: seller_rating     # e.g. 4.9/2000 reviews → 25; 4.6/120 → 75
```

Because the output is dollars added to `effective_price`, that swap is local to
`sellers.yaml` and one lookup function — no change to the decision rule, the
schema, or the digest. This is the main reason to keep the adjustment in dollars
rather than as a score multiplier.

---

## 4. Schema

Two tables for v1, not seven.

```text
listings
────────────────────
id
source_id            -- from sources.yaml
url                  -- unique
cd_watch_uuid        -- ChangeDetection watch
title_raw
canonical_key        -- lenovo:m920q:i5-9500t:16gb:512gb
brand, model
cpu, cpu_cores, cpu_threads
ram_gb, ram_type, ram_slots, ram_max_gb
storage_gb, storage_type
seller               -- null for direct retailers/refurbishers
fulfillment          -- amazon_fulfilled | seller_fulfilled | ... | null
seller_rating        -- null until marketplaces land (Phase 4)
seller_reviews
first_seen, last_seen
parse_ok             -- false if the parser failed on this one

observations
────────────────────
id
listing_id
observed_at
price
compare_at_price     -- vendor "was" price; nullable, weak signal (§2)
in_stock
```

`compare_at_price` sits on `observations` rather than `listings` because vendors
change it — it is a property of the offer at a moment, like the price itself. It
is null for any source that does not publish one, which is most of them.

A `reference_prices` table (street price per `canonical_key`, §2) arrives with
eBay in Phase 4. It is deliberately not here: nothing before Phase 4 can populate
it honestly, and an empty table invites filling it with guesses.

Everything else from v1 §5 is deferred until it has a consumer:

- `products` — add with cross-retailer dedup (Phase 3, when there are multiple
  retailers to dedup *across*)
- `hardware_specs` — folded into `listings`; splitting it buys nothing at this
  size
- `scores`, `alerts` — no scoring, and the digest is stateless
- `sources` — it's a YAML file; it does not need a table

The seller columns are nullable and unused until Phase 4. They are in the v1
schema anyway because adding nullable columns later is a migration, and there is
no cost to carrying four nulls per row.

---

## 5. Parsing

Keep v1 §7's hierarchy — it was right. One tier is added above it, numbered 0
because it is not a fallback for the others but a check to run first:

0. **A vendor's own product JSON, where one exists.** Shopify stores expose
   `/collections/<handle>/products.json?limit=250`: title, price,
   `compare_at_price`, SKU and an `available` boolean per variant, with no
   scraping and nothing to break silently. eTek is on this path (§3), and it is
   worth checking for on *every* candidate source before writing a selector —
   Shopify is common among Canadian refurbishers, and it turns a source that
   would cost ongoing maintenance into one that costs almost nothing.
1. **JSON-LD / schema.org Product / embedded product JSON.** ChangeDetection can
   extract these directly with JSONPath/jq, so this tier often needs no custom
   code at all.
2. **Per-retailer CSS selectors** from `sources.yaml`.
3. **Regex fallback** over the title/description — `32GB DDR4 512GB NVMe` and its
   many spellings.
4. **LLM** — only on repeated failure, and not in v1.

Normalization target (unchanged from v1 §6):

```json
{
  "brand": "Lenovo",
  "family": "ThinkCentre",
  "model": "M920q",
  "cpu": "i5-9500T",
  "cpu_cores": 6,
  "cpu_threads": 6,
  "ram_gb": 32,
  "ram_type": "DDR4",
  "ram_slots": 2,
  "storage_gb": 512,
  "storage_type": "NVMe"
}
```

`ram_slots` and `ram_max_gb` are new, and load-bearing — the upgrade-cost model
in §2 cannot work without knowing whether the machine can take more memory. They
come from the **chassis** table below, not from the listing and not from the CPU.

### Chassis table — the one that actually gates the purchase

v1 had no equivalent of this, and it is more important than the CPU table.

The binding requirement in §2 is *64 GB after upgrade*. Whether a machine can get
there is a property of the **chassis and its platform**, not of the CPU and not
of what the retailer shipped: nearly every Tiny/Micro/Mini box is 2 × SODIMM, so
the ceiling is `2 × (largest SODIMM the platform will accept)`. On these machines
that is usually 2 × 32 GB = 64 GB from roughly 8th-gen Intel onward, but it is
2 × 16 GB = 32 GB on several older or lower-tier chassis.

Under the §2 rules that is not an exclusion — it is a `ram_max_gb` miss carrying
`else_penalty: 120`, so such a machine still ranks and still appears in the
near-miss section. What makes this table load-bearing is that **the shortfall is
invisible in the listing title**: nothing on the page says the chassis caps at
32 GB, so without this lookup the penalty is never applied and the machine ranks
as though it could reach 64 GB. That is a silent pricing error, not a loud one.

```yaml
# config/chassis.yaml
lenovo-m720q:      {ram_slots: 2, ram_max_gb: 64, m2_slots: 1, sata: true}
lenovo-m920q:      {ram_slots: 2, ram_max_gb: 64, m2_slots: 2, sata: true}
dell-optiplex-3070-micro: {ram_slots: 2, ram_max_gb: 64, m2_slots: 1, sata: true}
dell-optiplex-7070-micro: {ram_slots: 2, ram_max_gb: 64, m2_slots: 2, sata: true}
dell-optiplex-3080-ultra: {ram_slots: 2, ram_max_gb: 64, m2_slots: 1, sata: true}
hp-prodesk-400-g5-mini:   {ram_slots: 2, ram_max_gb: 64, m2_slots: 1, sata: true}
hp-elitedesk-800-g4-mini: {ram_slots: 2, ram_max_gb: 64, m2_slots: 2, sata: true}
# ...one entry per model that actually appears in the tracked inventory
```

Keyed by `brand:model` — the same thing `canonical_key` (§4) already derives, so
the lookup is free once the title parses. `m2_slots` feeds the NVMe hard
requirement and the `cost_to_reach(NVMe)` term in the same way.

Two rules keep it small and honest:

- **Populate it on demand.** One entry per model that shows up in tracked
  inventory — eTek's ten listings are about seven chassis. Do not pre-fill it.
- **A missing chassis is `parse_ok = false`, never a default.** Guessing
  `ram_max_gb: 64` for an unknown model would silently pass a machine through the
  one filter that cannot be undone after purchase. Unknown chassis surface in the
  digest (§6) as needing a lookup, and an unresolved one is excluded, not
  assumed.

Values come from the vendor's own spec sheet or PSREF/QuickSpecs, and each entry
is worth a one-line source comment — this is the table where being wrong costs
money rather than a bad ranking.

### CPU table

~15 entries, not 50–100. Only the T- and GE-suffix business chips that actually
appear in tiny PCs:

```yaml
# config/cpus.yaml
i5-8500T:  {cores: 6, threads: 6,  gen: 8,  tdp: 35, nested_virt: true}
i5-9500T:  {cores: 6, threads: 6,  gen: 9,  tdp: 35, nested_virt: true}
i7-9700T:  {cores: 8, threads: 8,  gen: 9,  tdp: 35, nested_virt: true}
i5-10500T: {cores: 6, threads: 12, gen: 10, tdp: 35, nested_virt: true}
ryzen-5-5650ge: {cores: 6, threads: 12, tdp: 35, nested_virt: true}
ryzen-7-5750ge: {cores: 8, threads: 16, tdp: 35, nested_virt: true}
# ...~15 total
```

`nested_virt` is the field v1 §8 omitted and the one the §2 **gate** actually
tests — the single requirement no price can offset. It is nearly always `true`
for Intel 8th-gen-and-later and Zen 2+ — which is itself the useful finding:
**the CPU almost never excludes anything, so this table mostly supplies the core
count and the chassis table above does the real gating.** Keep `nested_virt`
anyway to catch the exceptions (Celeron/Pentium/Atom-based tiny PCs).

Expand the table only when a listing appears with a CPU that isn't in it — the
parser logs that as `parse_ok = false` and it shows up in the digest.

---

## 6. Output: one email a day

v1 §13's three alert tiers are cut. The digest is the only mode.

```text
🖥️  Mini-PC Digest — Sep 21

  $450 eff.  Dell OptiPlex 7070      eTek         $350 + $60 RAM + $40 source
             i5-9500T / 16→64GB / 256GB NVMe
             60d warranty, 30% restocking fee on non-defective returns

  $464 eff.  Lenovo M920q            ITRefurbs    $379 + $60 RAM + $25 vendor
             i5-9500T / 16→64GB / 512GB NVMe
             ↓ below 30d median ($429)

  $469 eff.  Lenovo M720q            Amazon.ca    $359 + $60 RAM + $50 ship-by-seller
             i5-8500T / 16→64GB / 512GB NVMe
             seller: ABC Computers  4.7★ / 1832

  $479 eff.  Dell OptiPlex 7070      Dell Outlet  $479 + $0
             i7-9700T / 32GB / 512GB NVMe
             1yr warranty, standard returns

  ── near misses (1 requirement short) ──
  $420 eff.  Beelink SER5            eBay         $300 + $120 ram_max 32GB
             Ryzen 5 5560U / 32GB max / 500GB NVMe
             ✗ ram_max_gb 32 < 64      (lower the bar → ranks 1st)
             ~street $520 (eBay sold, n=14)

  ── excluded (12) ──────────────────────
  2 no nested virt (gate)
  2 seller below gate (rating / reviews)
  8 ranked above with penalties applied

  ⚠  itrefurbs: title selector returned nothing, 3 checks running
  ⚠  chassis unknown, excluded until looked up: hp-elitedesk-805-g6-mini
```

Ranked by effective price, with the delta shown so the arithmetic is visible.

**The near-miss section is what makes the §2 dial usable.** A count of exclusions
tells you nothing about whether the bar is set right; seeing the $300 Beelink
that failed *only* on RAM ceiling, with its effective price and what it would
rank if the bar moved, is the information needed to decide. Note that it would
rank first at $420 — the line shows exactly what the current setting is costing,
so tuning is a judgement about a real machine rather than a guess at a threshold.

Note also the `~street` line: a reference price from eBay sold listings, with
`n=14` shown so a thin sample is visible as such. That is the strongest of the
four baselines in §2 and is labelled by source, as is the `↓ below 30d median`
flag on line 2 — the digest never presents a flag without saying where it came
from, because they differ by an order of magnitude in how much they mean.

The first two lines are the point of the whole §2 model: eTek's listing is $29
cheaper than ITRefurbs' but carries a $40 adjustment against ITRefurbs' $25, and
it *still* wins by $14 — while the reader can see exactly why and disagree by
editing one number in `sellers.yaml`.

Lines 1 and 4 are both OptiPlex 7070s but in different configurations, so they
are different canonical keys and correctly appear as separate entries — dedup
(§9, Phase 3) collapses same-key listings across vendors, not same-model ones.
Instant alerts can be added later if a genuinely time-sensitive deal is ever
missed.

**The selector-health warning at the bottom is not decoration.** See §8.

---

## 7. Layout

```text
mini-pc-price/
├── docker-compose.yml      # changedetection only
├── config/
│   ├── sources.yaml        # per source: urls + selectors, or a products.json
│   ├── watch_urls.yaml     # manually seeded product URLs (any retailer)
│   ├── chassis.yaml        # RAM ceiling + M.2 slots per model — the real gate
│   ├── cpus.yaml           # ~15 CPUs
│   ├── parts.yaml          # RAM/NVMe upgrade costs
│   ├── sellers.yaml        # gates + fulfillment adjustment
│   └── rules.yaml          # the gate, tunable requirements + penalties (§2)
├── src/
│   ├── poll.py             # CD API → parse → SQLite
│   ├── digest.py           # SQLite → filter → rank → email
│   ├── specs.py            # title/JSON-LD → normalized specs
│   ├── cd_client.py        # thin ChangeDetection REST wrapper
│   └── db.py               # two tables
├── tests/
│   └── test_specs.py       # the parser is what needs tests
└── README.md
```

Five source files. `docker-compose` runs one container (ChangeDetection); the
tracker is cron + Python.

**Run it on the desktop, not on Proxmox.** v1 §19 pictures the finished system
running on the mini PC — but the mini PC hasn't been bought yet; finding it is the
entire point. Deployment is a post-purchase problem.

---

## 8. Risk the v1 plan didn't name

**Selector breakage is the main ongoing cost, not the domain logic.**

Retailers restructure pages without warning. A selector that silently returns
nothing looks exactly like "no new products" — the system goes quiet and appears
to be working. Several of these sites will also rate-limit or block automated
access outright.

Mitigations, all cheap:

- Check hourly at most. There is no deal that requires 5-minute polling.
- Review each retailer's terms before adding it.
- **Track per-selector health.** If a selector yields nothing for 3 consecutive
  checks, surface it in the digest (§6). This one feature is worth more than the
  whole of v1 §9.
- Record `parse_ok` per listing so parser gaps are visible rather than silent.
- **Never default an unknown chassis** (§5). A missing `chassis.yaml` entry means
  the `ram_max_gb` penalty cannot be computed; assuming 64 GB silently ranks a
  machine as better than it is, and unlike a bad ranking that error is only
  discovered after the box is open. Unknown chassis are held out of the ranking
  and listed in the digest for a manual lookup — the one place the plan prefers a
  gap in the output to a confident guess.

---

## 9. Phasing

### Phase 1 — prove the pipeline (one retailer)

```text
eTek products.json → ChangeDetection → poll.py → SQLite → console
```

No email. The bar for done: a source yields listings whose specs parse correctly
into the schema, and prices accumulate across checks.

**Phase 1 is meant to stand alone, and stopping here is a legitimate outcome.**
eTek's collection is ten listings; once they are parsed into the schema with
`effective_price` computable by hand from the same numbers, the buying decision
may simply be answerable by reading the table. Everything from Phase 2 on exists
to widen the search and to remove the manual step, not to make the decision
possible. Build Phase 1, look at the data, then decide whether Phase 2 is still
worth it.

Build `config/chassis.yaml` (§5) here rather than deferring it — the ten eTek
listings are about seven chassis, the RAM ceiling is the requirement that
actually excludes machines, and it is the one table where a wrong entry costs
money. A handful of PSREF/QuickSpecs lookups is most of Phase 1's manual work,
and it is the part that pays off immediately even if nothing else gets built.

Two more things belong in Phase 1 for the same reason — they are nearly free
*now* and expensive or impossible later:

- **`config/rules.yaml` (§2)** — the gate, the tunable requirements and their
  penalties. Reading thresholds from a file instead of hardcoding them is an
  afternoon; retrofitting it means editing a filter that already works. The dial
  is also most useful early, when there is no intuition yet for how dense real
  inventory is.
- **`compare_at_price` (§2, §4)** — one nullable column, populated straight from
  the tier-0 JSON. Weak signal, near-zero cost, and **unbackfillable**: an
  observation not recorded when the page was fetched is gone for good. That
  asymmetry is the whole argument for capturing it now.

**eTek is the Phase 1 target because it is the least likely to fail for reasons
that have nothing to do with the pipeline.** Its Shopify `products.json` (§3, §5
tier 0) is structured data at a stable URL: no selectors to guess at, no page
restructure to absorb, no anti-bot defenses, and a collection that is entirely
mini PCs rather than one that must be filtered down to them. When something
breaks in Phase 1, it should be the code — not the source.

The cost is that eTek is not the simplest vendor to *reason* about: it carries
`source_adjustment: 40` and the weakest independent reputation evidence of any
source kept (§3). That is fine here, because Phase 1 ends at the console and
buys nothing. The adjustment first matters in Phase 2, where ranking begins.

**Dell Outlet moves to Phase 3b**, where it fits naturally with the other
well-structured, zero-adjustment vendors. It remains the better source to
actually buy from — manufacturer warranty, clean RMA — and the better exercise of
the JSON-LD tier. It is simply the harder thing to build first, and nothing is
learned in Phase 1 that requires it.

Also wire `config/watch_urls.yaml` here — manually seeded product URLs from any
retailer, including marketplaces. It is a few lines (a URL is a watch), it makes
marketplace listings trackable immediately without any discovery work, and it is
the fallback whenever discovery breaks on a source.

### Phase 2 — the digest

`digest.py`, the §2 filter (gate, then requirements with penalties),
effective-price ranking, the near-miss section, available baseline flags, Gmail
via app password. Now it is useful.

**The near-miss section (§6) is part of Phase 2, not a later polish.** It is what
makes `rules.yaml` tunable in practice: without seeing the machines that just
missed — and what they would rank if the bar moved — adjusting a threshold is
guesswork. It is also a few lines, since those listings are already scored.

### Phase 3 — refurbishers + dedup

Add ITRefurbs — the second of the two independent refurbishers that survived the
§3 vendor review, eTek being already in from Phase 1. This is the phase where
cross-vendor comparison starts to matter, so `source_adjustment` needs to be
applied consistently before these listings compete with each other.

Cross-retailer dedup via `canonical_key` (v1 §11 — the genuinely valuable custom
piece, kept in full) becomes meaningful with multiple sources:

```text
              dell:optiplex7070:i5-9500t:16gb:256gb
                          │
                ┌─────────┴─────────┐
              eTek               ITRefurbs
              $350                 $399
```

One line in the digest, not two.

### Phase 3b — direct retailers

**Dell Canada Outlet**, Canada Computers, Memory Express, Staples, Best Buy
direct, Lenovo Canada Outlet. Same shape as Phase 3: config entries plus
selectors, no new machinery. Dell Outlet is worth doing first here — it is the
best vendor in the plan to actually buy from, and its JSON-LD should make it the
easiest of the group.

These all have `source_adjustment: 0` — physical locations or manufacturer
warranties mean returns are tractable. The best recourse is concentrated in
*this* phase and the cheapest inventory in Phases 3 and 4 (§3).

### Phase 4 — marketplaces

In rough order of value-per-unit-effort:

1. **eBay.ca** — first, and arguably the highest-value phase in the plan after
   Phase 2. Browse API: structured JSON, seller reputation as fields, no
   scraping. Fixed-price listings only. This is where the cheap off-lease
   inventory actually lives (§3), and it is the source the refurbisher tier was
   a weak substitute for. Add the seller gates and `fulfillment_adjustment` here.

   **This phase also delivers the reference price (§2)** — the baseline the plan
   has wanted since Phase 1 and could not honestly produce. Sold/completed
   listings give what a `canonical_key` actually transacts at, which is the only
   signal that identifies an underpriced machine on its *first* appearance, with
   no history and no second vendor needed. Add `reference_prices` (§4) here,
   keyed by `canonical_key`, storing the median, the sample size and the window;
   the digest shows `n` so a thin sample is visibly thin. Two features, one
   integration — which is most of why eBay leads this phase.
2. **Best Buy Marketplace** — nearly free once Best Buy direct is parsed in
   Phase 3b; marketplace items share the product-page structure.
3. **Walmart.ca** — moderate.
4. **Amazon.ca** — via platform-native alerting (watchlist / camelcamelcamel)
   feeding `watch_urls.yaml`, *not* by scraping search pages.

If only one item in this phase ever gets built, build eBay. If the schedule
slips, eBay is the one worth pulling *forward* — ahead of Phase 3b's direct
retailers, whose inventory is well-priced but rarely cheap.

Realistically, the machine may well have been bought before all of this lands.
That is a successful outcome, not an abandoned project — and manual seeding
(Phase 1) means marketplace deals are reachable well before Phase 4 arrives.

---

## 10. What changed from v1, and why

| v1 | v2 | Reason |
| --- | --- | --- |
| Webhook push (§4) | REST poll | Webhooks fire on change; a gap-free observation log is the substrate for every §2 comparison, and cannot be backfilled |
| FastAPI service (§16) | Two cron scripts | Nothing left to serve once pull-based |
| Weighted deal score (§9) | Hard filter + effective price | Unfalsifiable weights vs. explainable rules |
| Score as-listed | Score post-upgrade cost | RAM is cheap and upgradeable; this is the real comparison |
| 12 sources at once (§3) | eTek first, rest phased | Prove the pipeline before breadth; start with the source least able to fail for non-pipeline reasons |
| Uniway as Phase 1 target | Excluded entirely | Legitimate but consistently marked up — a bad *source*, not a bad vendor |
| REFURB.io as a Phase 3 source | Excluded (gate) | Fulfillment and refund failures 2022+; the positive reports are from 2017 |
| — | eTek added (Phase 1) | Shopify `products.json`; collection is entirely mini PCs |
| — | eBay.ca added (Phase 4, first) | Absent from v1; Browse API is structured, and it holds the off-lease inventory the refurbisher tier was a thin substitute for |
| — | `chassis.yaml` (§5) | The 64 GB ceiling is a chassis property and the one requirement that is invisible in a listing; v1 had nowhere to put it |
| — | One gate + tunable requirements (§2) | Only nested virt is truly binary; the rest are preferences with a price, and the bar needs to move as real inventory density becomes known |
| — | Near-miss section in the digest (§6) | Exclusion *counts* cannot tell you whether the bar is set right; the machine that just missed can |
| — | `compare_at_price` captured (§2, §4) | Weak signal, one column, and unbackfillable — an unrecorded observation is gone |
| — | Street price from eBay sold listings (§2, Phase 4) | The only baseline that identifies an underpriced machine on first sighting |
| Historical median as the key signal (§10) | Demoted to a tiebreaker | Not just sparse — self-referential: it cannot tell whether a price was ever good |
| — | Parser tier 0: vendor product JSON | Removes selector breakage entirely where it applies |
| Refurbisher trust as a constant (§3) | `source_adjustment` in dollars | "Legit but slow support" is a price, not a gate |
| Marketplace search scraping (§3) | Manual seeding + platform alerts | Value was never the issue; search-page scraping is |
| Seller scoring function (§12) | Gates + dollar adjustment | Binary risk for a single purchase; dollars compare directly |
| 7 tables (§5) | 2 tables | Add tables when they have a consumer |
| 50–100 CPUs (§8) | ~15, + `nested_virt` | Hand-entry cost; the omitted field was the load-bearing one |
| 3 alert tiers (§13) | Daily digest only | A digest that gets read beats alerts that get ignored |
| Deploy on Proxmox (§19) | Run on desktop | The mini PC doesn't exist yet |
| — | Selector health tracking | Silent breakage is the real failure mode |
| Phase 1 as a stepping stone (§17) | Phase 1 as a standalone deliverable | Ten parsed listings may answer the buying question on their own |
| Vendor research open-ended (§3) | Closed | Two searches produced one new viable name; durable exclusions separated from anecdotal ones |

### Kept from v1, unchanged

The CD/custom-code boundary (§1), SQLite over Postgres (§5), hardware
normalization (§6), the parser hierarchy (§7, with a tier added above it),
canonical product keys
and dedup (§11), digest-over-alerts (§15), config-not-code for retailer
specifics (§3), start-with-one-retailer phasing (§17), and no search engine
(§18).

v1 §10's historical median is kept but **demoted** — recorded from Phase 1, shown
when it exists, and no longer the primary "is this cheap" signal. v1's underlying
question was the right one; it just picked the weakest of the four available
answers (§2).

v1 §12's *premise* is kept too — marketplaces contain good sellers worth
monitoring, and questionable sellers need explicit handling rather than blanket
exclusion. Only the mechanism changed, from a scoring function to gates plus a
dollar adjustment.
