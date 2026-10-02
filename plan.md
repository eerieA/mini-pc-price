# Mini-PC Deal Tracker — Plan (v2)

> v1 of this plan was a separate document, since deleted; §10 records what it
> said and why each part changed. This revision keeps its good
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
  ┌────────────────────────────────┐   ┌────────────────────────────────┐
  │      ChangeDetection.io        │   │   vendor product JSON (tier 0) │
  │            docker              │   │                                │
  │  fetch / JS render / cookies   │   │  requests.get(products.json)   │
  │  CSS / XPath / JSON / jq       │   │  no container, no selectors    │
  │  per-retailer selectors        │   │                                │
  │  scheduled checks + history    │   │  eTek (Phase 1)                │
  └───────────────┬────────────────┘   └───────────────┬────────────────┘
                  │                                    │
                  │  GET /api/v1/watch                 │  raw bytes → data/raw/
                  │  GET /api/v1/watch/<uuid>/history  │
                  └─────────────────┬──────────────────┘
                                    ▼
  ┌────────────────────────────────────────────────────┐
  │              deal tracker (python)                 │  two cron jobs, no service
  │                                                    │
  │  parse specs from snapshot                         │
  │  canonical product key                             │
  │  write observations → SQLite                       │
  └───────────────────────┬────────────────────────────┘
                          │
                          │  digest.py, daily
                          ▼
  ┌────────────────────────────────┐
  │  filter → rank → one email     │  only when something moved
  └────────────────────────────────┘
```

Two fetch paths, one parser. Which one a source uses is a property of the source,
not a second architecture — see below.

### Where it runs: CI, not the desktop

A months-long run cannot live on the desktop, because of §1's own premise: **an
observation cannot be backfilled.** A desktop asleep at the scheduled hour does
not poll late, it loses the day, and the price history is precisely what a long
run accumulates.

So the poll and the digest run in GitHub Actions
(`.github/workflows/daily.yml`), which commits `data/tracker.db` back to the
repository. The repo stays the single copy of the history: `git pull`, and the
local report, digest and chart read the same database they always read. No sync
protocol, no second store, no server.

This does not reopen the FastAPI question §1 settled. There is still no service,
nothing listening, and no request handling — it is the same two scripts on a
different clock. What changed is only which machine owns the clock, and the
reason is the one §1 already cared most about.

The costs, stated plainly: the Gmail app password now lives in GitHub's secret
store rather than on one desktop; the database is a binary file in git, so each
daily commit stores a fresh copy rather than a delta (28 KB today, and the fix
if it ever matters is to commit observations as CSV and rebuild); and GitHub
disables scheduled workflows after 60 days of repository inactivity. The last of
those is why the digest reports poll coverage — see §8.

### Why pull instead of the webhook

v1 made ChangeDetection POST to a FastAPI endpoint. Two problems:

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

### Why tier-0 sources skip ChangeDetection entirely

A vendor's own product JSON (§5, tier 0) is an API, not a page to watch. Routing
it through a change-detector buys nothing and costs the second argument above:
CD would fetch the URL on a timer and hand back a text blob that `poll.py` must
`json.loads` anyway. The messy web that §1's boundary exists to contain is not
present in a Shopify `products.json`.

So `poll.py` fetches those sources directly. CD arrives with the first source
that genuinely needs it — an HTML page requiring a selector, a JS render, or
cookies. **No such source has been found yet.** ITRefurbs was named here as that
source until 2026-09-25, when its storefront turned out to be Shopify serving
`products.json` like eTek's (§9). CD is therefore not scheduled for any phase;
it is a capability waiting for a source that needs it.

This was a deliberate trade, and the thing given up was real: CD would keep
checking on a schedule of its own even if `poll.py` were broken or the desktop
were off, whereas a direct fetch that does not run is an observation that is gone
for good (§2 — observations cannot be backfilled). Moving the schedule into CI
(above) bought most of that back from a different direction: the poll no longer
depends on one machine being awake, and the coverage line reports the days it
missed (§8). What made the trade worth it
is that Phase 1 is testing the *parser*, and every piece of CD infrastructure —
container health, API token, watch UUID, CD's text-diff semantics over JSON — is
a way for Phase 1 to fail for reasons that have nothing to do with the code being
proved (§9). Against a source whose whole appeal is that it cannot break, adding
four ways for the harness to break is the wrong direction.

Two properties are worth borrowing from the CD path anyway, and both are cheap:

- **Persist the raw response before parsing it.** `poll.py` writes each fetch to
  `data/raw/<source>/<timestamp>.json` and parses from there. That recovers CD's
  most useful property for about five lines — the parser can be re-run against
  stored bytes while debugging, without re-hitting the vendor — and it means a
  parser bug discovered on day 10 can still be fixed against day 1's data.
- **Write an observation every run, unconditionally.** Not only when something
  changed. This is the same requirement that ruled out the webhook, and on the
  direct path it is simply what the code does rather than a property of CD's
  history endpoint that has to be verified.

Nothing here is wasted if CD lands later. `specs.py`, `db.py`, the schema and the
chassis table are all source-agnostic; only where `poll.py` gets its bytes
changes, and that is one function.

### Why there is no web service

Once the flow is pull-based, the entire system is:

```text
every 30 min   poll.py     fetch (direct or CD API) → parse → SQLite
daily 08:00    digest.py   SQLite → filter → email
```

Two cron entries and a package. No FastAPI, no request handling, no webhook
endpoint, no raw-event table. A browsing UI can be added later if wanted; it is a
want, not a requirement.

---

## 2. The decision rule: a filter, not a score

v1 proposed a five-term weighted formula with nested sub-weights. Cut it. With
a handful of listings there is no way to know whether `0.40` or `0.35` on
HardwareValue is correct — the number is unfalsifiable and un-tunable, and every
alert it produces is uninterpretable.

### One gate, then tunable requirements

Making all four requirements hard exclusions would be wrong in both directions:
it treats one genuinely binary constraint and three priced preferences as the
same kind of thing, and leaves no way to adjust the bar as real inventory turns
out denser or thinner than expected.

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
  ram_max_gb:    {min: 64,   else_penalty: 325}
  cpu_cores:     {min: 6,    else_penalty: 80}
  storage_nvme:  {min: true, else_penalty: 40}

prefer_shipped_ram_gb: 32     # see "Shipped RAM is worth more than upgradeable RAM"

surface_near_misses: true     # digest section for single-requirement failures
```

325 is the price of the 32 GB the capped machine cannot install. A lower figure
charges a 32 GB-capped chassis less for being permanently short of the target
than a 64 GB-capable one pays to actually reach it, which inverts the comparison
the penalty exists to make. It is still a preference, not a derived figure.

> **A known distortion at this setting.** `else_penalty` is a flat charge, while
> `cost_to_reach` is a real and now very large one. At 325 against ~$650–700 of
> actual memory, every capped machine ranks *above* every machine that reaches
> 64 GB — on the 2026-09 eTek inventory the entire top of the list is
> 32 GB-capped, and the cheapest qualifying machine sits 7th. That is the model
> working as specified: it is genuinely cheaper to buy a machine that cannot do
> the job than one that can. It is also a trap if the list is read as a
> recommendation, because the gate the project exists to satisfy is 64 GB.
>
> The digest's near-miss section (§6) is what keeps this honest — capped
> machines belong there, not interleaved with qualifiers — and the one number
> worth watching is the gap between the cheapest near-miss and the cheapest
> qualifier. Do not "fix" this by inflating `else_penalty` until the ordering
> looks right: that would be fitting the dial to a desired answer, which is
> precisely what §2 rejected weighted scoring for.

This is the dial. If two weeks of data show far more qualifying machines than
expected, raise `min` — the bar moves and the digest gets shorter. If almost
nothing qualifies, lower it, or lower the `else_penalty` so near-misses compete
more readily. `min` changes what counts as good; `else_penalty` changes how much
the shortfall costs. Both are one-line edits in config, and neither touches code.

> `else_penalty` is the same species of number as `source_adjustment` — a
> subjective dollar figure, not a derived one (see the note below).

### Then rank by effective cost

Score post-upgrade cost, not the listing as shipped.

```text
effective_price = listing_price
                + cost_to_reach(64 GB RAM)      -- where the chassis allows it, §5
                + cost_to_reach(NVMe, if absent)
                + requirement_penalties         -- unmet requirements, above
                + fulfillment_adjustment        -- marketplace seller risk, §3
                + source_adjustment            -- vendor recourse risk, §3
                + shipping                      -- as listed, see below
                + import_adjustment             -- cross-border cost, see below
```

`cost_to_reach` and `requirement_penalties` are mutually exclusive per
requirement: if the chassis can reach 64 GB, the listing pays the *real* cost of
the SODIMMs; if it cannot, it pays `else_penalty` instead. A machine is never
charged both for an upgrade and for failing to be upgradeable.

RAM is upgradeable, but no longer cheap: at ~$325 per 32 GB DDR4 module (a
DDR5 chassis pays ~$560, `parts.yaml`), a 16 GB machine at $300 lands at $625
and loses to a 32 GB one at $400. Scoring the post-upgrade cost is what decides
that, whichever way it goes.

### Shipped RAM is worth more than upgradeable RAM

The formula above only models upgrading *up*: it adds what it costs to reach the
target. That silently assumes the upgrade is a neutral transaction — pay the
money, get the capacity. Under a DDR4 shortage it is not, for two reasons the
effective price does not capture:

1. **The part may not be there.** DDR4 is end-of-life: no new supply is coming
   (§parts.yaml). Today's cheapest in-stock 32 GB module is one SKU at one
   retailer, and two of the four modules surveyed were already unavailable. A
   machine needing two of them is exposed to that in a way a machine that ships
   with the memory is not.
2. **The price is moving one way.** Suppliers are guiding 10–20% per month
   through end of 2026. `cost_to_reach` prices the upgrade as of the last hand
   refresh of `parts.yaml`, so it is systematically *low*, and low in the
   direction that flatters under-specified machines.

So `prefer_shipped_ram_gb: 32` in `rules.yaml` above: a listing already carrying
32 GB or more is marked in the digest, and where two machines are within the
noise floor of each other on effective price, the one that ships with the memory
wins. Deliberately a tiebreaker and a label rather than another dollar term —
the uncertainty here is about *availability*, which is not a price, and inventing
a second subjective penalty to sit beside `else_penalty` would double-count the
same shortage the RAM price already reflects.

`fulfillment_adjustment` covers marketplace seller risk; `source_adjustment`
covers how much recourse a vendor actually offers if the unit is faulty. Both are
`0` for vendors with clean warranty and return paths, and both are defined in §3.

> **What the adjustments are, exactly.** They are subjective dollar penalties —
> what I would pay to avoid dealing with that vendor's failure mode — not
> expected values. Some are anchored to a published number (eTek's restocking
> fee), some are pure preference (`seller_fulfilled: 50`); once written down they
> are the same kind of thing and get added together as such. No claim is made
> that any of them equals probability × cost, and none has been calibrated
> against an outcome. Their job is to make "cheaper but riskier" and "dearer but
> safer" land on one axis so the digest can rank them, and to put the number
> somewhere I can argue with it. Treat a $15 gap between two effective prices as
> noise.

Upgrade part costs live in `config/parts.yaml`, refreshed by hand. They were
expected to move slowly; during the 2026 DRAM shortage they are the fastest-moving
input in the project, and a figure more than a month old is likely low. The file
carries its own read-this-first note and the date it was priced.

### Shipping, imports and currency

The formula had no shipping term until eBay, because neither refurbisher's
tier-0 JSON carries one: eTek and ITRefurbs are charged `0`. That flatters them
by whatever they charge at checkout, which is unmeasured. It was tolerable while
every source shipped domestically. It is not on eBay, where the 2026-09-30
capture of one search (`OptiPlex 3080 Micro`, Buy It Now) ran from free to
$1,131, and 66 of 110 listings shipped from the US, Australia or the UK.
`shipping` is the listed amount; a listing that shows only an *estimate* carries
a mark in the digest, as `NVMe per the description` does.

`import_adjustment` is a flat dollar figure per ships-from country, not a
computed duty:

```yaml
# config/rules.yaml
import_adjustment:            # dollars, by ships-from country
  CA: 0
  US: TBD                     # placeholder -- set by hand before eBay ranks
  default: TBD                # every other origin
```

A computed rate was the alternative and it prices the wrong thing. Computers
(HS 8471) enter Canada duty-free as far as this plan has checked, so the real
cross-border cost is courier brokerage and a return that has to cross the border
back. That is a judgement, the same species as `source_adjustment` (see "What
the adjustments are, exactly" above), and a hand-set number is also where any
change in Canada–US tariff policy lands. Sales tax is deliberately excluded:
the domestic sources' prices exclude it too, so charging it to imports alone
would penalise them for something every purchase pays.

A listing from an origin whose value is still `TBD` is **held out of the
ranking and surfaced**, not ranked at `0`, for the same reason an unknown
chassis is (§5, §8): a missing input that defaults to the flattering value ranks
a machine better than it is.

The same holds for a marketplace listing with **no shipping quote**. eBay could
not quote shipping to Canada for 21 of 161 results in the first live search,
nearly all from the US, and a missing figure is not a free one. Those are held
out too. The refurbishers keep their `0`: they also state no shipping, but
their checkout cost is bounded and domestic, so the flattery is small and known.

**Currency is recorded, never converted.** The ranking adds prices across
sources, so an unconverted US$300 would sit beside C$300 as an equal — an error
of roughly $100, larger than the `storage_nvme` penalty. Every price in the
eBay.ca capture was already in C$, including the listings shipping from abroad,
and so were all 441 in the first live API poll (2026-10-02), so conversion is
expected never to be needed. If a non-CAD price does arrive,
the listing is held out and surfaced rather than ranked.

### Then flag: is this cheap for what it is?

Ranking by `effective_price` answers "which of these is cheapest." It does not
answer "is any of them actually a *deal*" — for that the price needs a baseline.

30-day median of the listing's own history — the weakest, and v1 overrated it.

v1 called this "where this gets really powerful." It is the one idea from the
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

`compare_at_price` — cheap, weak, and worth capturing from Phase 1.

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
    # Also tier-0 Shopify, confirmed 2026-09-25 -- no `selectors:` key here
    # either. See §9 for what that finding cost Phase 3's premise.
    products_json: "https://itrefurbs.ca/collections/refurbished-desktops/products.json?limit=250"
    product_url_base: "https://itrefurbs.ca/products"
    source_adjustment: 25

# Excluded, with reasons — see below. Kept as comments so the decisions are
# visible to anyone reading the config rather than only the plan.
#   uniway    — legitimate, but consistently marked up
#   refurb_io — fulfillment and refund failures, 2022 onward
#   openbox   — good reputation, but sells zero desktops; consumer returns only
```

### Vendor reputation is a per-source input, not a hidden assumption

Two distinct problems get confused here, so keep them apart:

- Is the vendor's pricing worth tracking at all? A legitimate vendor that is
  reliably above market is a source that costs maintenance and returns nothing.
- What is the risk of buying from them? That is a cost, and §2 already prices
  costs in dollars.

Only the second is what `source_adjustment` is for. The first is a reason not to
add a source at all, and it has nothing to do with trust.

Every claim below cites a saved transcript in `research/` — see
`research/README.md` for how they were captured and how to read them.

#### Uniway — excluded, for markup, not trust

Uniway was originally picked as a convenient Phase 1 scraping target, not because
it had been vetted. When it was checked, the evidence said something other than
expected: Uniway is legitimate. Three separate commenters confirm it — real
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

That is a gate, not an adjustment. `source_adjustment` prices the risk that a
unit arrives faulty and the return is painful. It does not price "the order may be
cancelled after three weeks and the refund may take two more" — no plausible
dollar figure makes that worth chasing a discount for one purchase.

`research/thinkpad-6pm59n-refurbio.md`

#### eTek Laptop — kept

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
| Dell OptiPlex 3080 "Ultra" * | i5-10500T | 16 GB | $429.99 |
| Dell OptiPlex 3080 "Ultra" * | i5-10500T | 32 GB | $549.99 |

\* eTek's title, and it is wrong: both listings describe an OptiPlex 3080
**Micro** throughout their product descriptions, and their URLs say `3090`. The
Micro reaches 64 GB on two SODIMMs (§5). The titles are reproduced here as the
vendor writes them because that is what the parser receives.

That table omits three older chassis in the same collection — the Lenovo M73
Tiny, the OptiPlex 9020 Tiny and the ProDesk 600 G3 SFF — which is where the
16 GB ceiling bites hardest. The 600 G3 is the interesting one: its chassis is
the only 4-socket machine here and reaches 64 GB, but the unit eTek is selling
has a 4-core i5-6500T, so it misses on cores and ranks last (§9). Chassis
capability and listing configuration are different things, and this is the
listing that separates them.

**Form factor: SFF is in scope.** The collection mixes USFF/Micro machines with
the occasional SFF one, and the 600 G3 SFF above is the case in point. SFF is a
larger box than a Micro but not a tower, and for a lab machine that sits
somewhere and runs, the size difference does not change the job. Since SFF
chassis take full-size DIMMs and often four of them, they are also
disproportionately the ones that reach 64 GB — excluding them on form factor
would exclude the cheapest path to the binding requirement. Towers stay out:
that is where the line sits, and it is a `rules.yaml` question if it ever needs
to move.

The reason to prioritise it is structural, not the prices. It runs Shopify,
so `…/products.json?limit=250` returns title, price, `compare_at_price`, SKU and
an `available` boolean per variant. Verified returning valid JSON for all ten
products. That eliminates the single largest ongoing failure mode in §8 — silent
selector breakage — for this source entirely, and it reduces `specs.py` to title
parsing (§9, Phase 1).

The return policy is the worst of any vendor kept:

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

Evidence caveat, and it cuts both ways. eTek has *no Reddit presence* — no
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

One data point is not a pattern, and the failure described is slow shipping,
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

These are subjective penalties, per the note in §2. They exist so a genuinely
cheap listing can still win, which a blanket exclusion would not allow.

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

#### Refurbish Canada — excluded, as a gate

Found while looking for a second tier-0 source, and excluded on the same failure
mode as REFURB.io. It is the worked example of re-opening this research for a
specific decision, and the evidence is stronger than anything else in this
section.

The technical fit was excellent — Shopify `products.json`, 67 desktop mini/SFF
listings, and **41 of them state NVMe or M.2 explicitly**, which is the field
eTek never provides. Ten chassis were researched and entered in `chassis.yaml`
on the strength of it.

Then the vendor was checked, and:

- **Trustpilot: 1.9/5 across 55 reviews, 77% one-star** (checked 2026-09-25).
  Recurring complaints are shipping far beyond the stated 7–14 business days,
  support ignoring email and phone, and refunds refused.
- One first-hand Reddit report matching that sequence exactly: no tracking,
  "label created by shipper" for a month, contact ignored, delivery only after a
  PayPal dispute was *escalated*
  (`research/AskACanadian-1mmkv71-refurbishcanada.md`).
- Registered since 2017, so this is not fraud. It is fulfillment failure.

**A 55-review aggregate is not the thin evidence the caveat below describes.**
It is re-checkable at a URL and not self-selected, which puts it in the durable
column with the Uniway markup finding. The vendor's own Judge.me widget is more
favourable and is also the weaker source — a merchant installs and configures it
on their own store, while Trustpilot is open to anyone.

This is a gate, not an adjustment. §2 prices recourse risk in dollars precisely
so a cheap listing can still win, but that logic assumes recourse eventually
works. Where the reported failure is *support not responding until a payment
processor intervenes*, there is no number that makes the listing worth buying,
and the whole point of this project is to buy one machine.

The chassis entries stay. They cost an afternoon, they are vendor-documented
facts about hardware rather than about a vendor, and the models recur across
every refurbisher — ProDesk 600 G6, EliteDesk 800 G5/G6, ThinkCentre M70q/M80q
will appear again in Phase 3 and 4.

Worth recording: their cheapest *qualifying* machine scored **$1,049.99** against
eTek's $914.99, so this exclusion costs nothing on today's inventory. That is
luck, not vindication — the decision would be the same if they had been cheaper.

This leaves **two independent refurbishers**, eTek and ITRefurbs, both carrying a
non-zero `source_adjustment` — the pattern, not a coincidence. Two searches
produced exactly one new viable name, which is itself evidence about the size of
the pool. A third search (2026-09-24) found four more Shopify storefronts and
promoted none: two were already excluded, one carries almost no mini desktops,
and this one gated.

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
> This line of research is closed to *breadth*: the marginal thread is worth
> less than the marginal listing.
>
> It is not closed to *depth*. Re-open it whenever a specific vendor becomes
> load-bearing for a specific decision — which is the normal case when a source
> is being added, not an exception. Refurbish Canada (above) is the worked
> example: the question "should this become a source" is precisely the trigger,
> and the answer changed the decision.

URLs and selectors live in config, never in code (v1 was right about this).

Direct retailers (Canada Computers, Memory Express, Staples, Lenovo/Dell outlet)
are a reasonable Phase 3 addition once the pipeline is proven.

### Marketplaces belong in, but discovery works differently

Marketplaces (eBay.ca, Amazon.ca, Walmart.ca, Best Buy Marketplace) stay — Phase
4. Refurbishers price against their own margins; marketplace sellers are often
liquidators clearing lots, which is where the cheap outliers actually are.
Cutting them loses the tail that makes this worth building.

**eBay is the correction to the thin-refurbisher finding above, and it was
missing from v1 entirely.** Off-lease corporate
Tiny/Micro/Mini machines arrive in Canada mostly through liquidators selling on
eBay, which is precisely the segment this whole plan targets. The Uniway markup
evidence in §3 makes the point unintentionally: the $120 comparison that showed
Uniway marked up ~60% was *an eBay listing*.

It is also the easiest marketplace to integrate:

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

A product page fetched by URL is far less defended than a search results page.
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

v1 modelled seller quality as a continuous multiplier feeding a weighted
score — `f(rating, review_count, return_policy, warranty, fulfillment,
known_seller)`, six inputs and no way to validate the shape. That has the same
unfalsifiable-weights problem as v1's deal score (§2).

For a **single** purchase, seller risk is not a gradient traded off against $20.
Either the seller is acceptable or they are not. So the need is real but the tool
was wrong:

```yaml
# config/sellers.yaml

gates:                          # fail any → set aside and counted, at any price
  min_feedback_percent: 98.0    # eBay's scale; see below
  min_feedback_score: 100
  returns_accepted: true        # no return window: a gate, not a price

fulfillment_adjustment:         # dollars added to effective_price (§2)
  amazon_fulfilled:   0         # A-to-z guarantee covers the risk
  bestbuy_fulfilled:  0
  walmart_fulfilled:  0
  seller_fulfilled:  50         # risk premium for a harder return

blocked_sellers:                # marketplace seller IDs, excluded by name
  refurbio: REFURB.io, excluded as a source above
```

Gates handle the bad-seller problem, which is genuinely binary. The adjustment
handles residual risk among sellers who clear the gates. No weights anywhere.

The gates are on eBay's scale, the only marketplace so far: positive-feedback
percentage and feedback count. The first draft's `min_rating: 4.5` was a
five-star figure, and its literal translation, 90%, gates almost nobody, since
established eBay sellers sit at 98–100%. `seller_fulfilled: 50` applies to every
eBay listing. eBay's Money Back Guarantee covers non-delivery, but a return of a
working-but-wrong machine, possibly across a border, stays the buyer's problem,
which is why eBay is not priced like an Amazon-fulfilled order.

`returns_accepted` is the expensive gate. Return terms are only on the full
item, so the poll makes one call per listing for them, which is most of its
running time, and in the first live poll it set aside 160 of 441 listings. It
stays a gate because a machine whose RAM ceiling or nested virtualisation turns
out wrong is only discovered after the box is open (§5), and with no returns
that discovery is final. The count prints in every digest, so what the gate
costs stays visible.

`blocked_sellers` exists because a vendor excluded above can also sell on a
marketplace, and the numeric gates will not catch it. The 2026-09-30 eBay
capture (§2) had REFURB.io listing two 3080 Micros as `refurbio`, at 99%
positive from 3.6K reviews with an eBay Refurbished badge — clear of every
threshold. The §3 exclusions are about the business, not the channel, so they
follow the vendor onto eBay by name. Each entry carries its reason, as
`out_of_scope.yaml` entries do, and a blocked listing is counted in the digest
like a keyword exclusion (§6) rather than dropped silently.

**No eBay username is ever stored.** The production API keyset is enabled under
eBay's Marketplace Account Deletion exemption, "I do not persist eBay data". The
alternative was a public endpoint receiving account-deletion events, which is
a service, and §1 rules services out. The exemption is only honest if no
seller's identity reaches disk, and `tracker.db` is committed to a public repo.
So `blocked_sellers` is matched at poll time and only its reason is stored
(`seller_blocked`), raw responses are saved with the username and sub-country
location stripped, and the digest prints feedback figures without a name. The
gates still apply at rank time, since feedback figures identify no one; a
block-list edit takes effect from the next poll.

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
    scale_by: seller_rating     # e.g. 100%/2000 reviews → 25; 98.2%/120 → 75
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
nested_virt          -- from cpus.yaml at parse time; the §2 gate reads it
ram_gb, ram_type, ram_slots, ram_max_gb
storage_gb, storage_type
seller               -- null for direct retailers/refurbishers, and for eBay (§3)
seller_blocked       -- the blocked_sellers reason matched at poll time; else null
fulfillment          -- amazon_fulfilled | seller_fulfilled | ... | null
seller_rating        -- null until marketplaces land (Phase 4)
seller_reviews
returns_accepted     -- marketplace only; the §3 gate reads it
ships_from           -- country code; null for domestic-only sources (§2)
condition            -- marketplace's own grade; "For parts" is set aside (§6)
first_seen, last_seen
parse_ok             -- false if the parser failed on this one

observations
────────────────────
id
listing_id
observed_at
price
compare_at_price     -- vendor "was" price; nullable, weak signal (§2)
price_max            -- top of a multi-configuration listing's range (§6); else null
currency             -- as listed; non-CAD is held out, never converted (§2)
shipping             -- as listed; null where the source does not state it (§2)
shipping_estimated   -- true when eBay shows a calculated estimate, not a rate
in_stock
```

`compare_at_price` sits on `observations` rather than `listings` because vendors
change it — it is a property of the offer at a moment, like the price itself. It
is null for any source that does not publish one, which is most of them.
`shipping`, `currency` and `price_max` sit there for the same reason: an eBay
seller can change any of them without the listing changing.

`observations` is where price history lives, and comparing two dates is a query
rather than a feature:

```sql
SELECT o.observed_at, o.price, o.compare_at_price
FROM observations o JOIN listings l ON l.id = o.listing_id
WHERE l.url = ?
ORDER BY o.observed_at;
```

That is the whole mechanism. Nothing else needs building to answer "what did this
cost last Monday" — which is the point of writing a row on every run rather than
only when something changes. The file holding this is committed for the same
reason (§7).

A `reference_prices` table (street price per `canonical_key`, §2) arrives with
eBay in Phase 4. It is deliberately not here: nothing before Phase 4 can populate
it honestly, and an empty table invites filling it with guesses.

Everything else from v1's seven-table schema is deferred until it has a consumer:

- `products` — add with cross-retailer dedup (Phase 3, when there are multiple
  retailers to dedup *across*)
- `hardware_specs` — folded into `listings`; splitting it buys nothing at this
  size
- `scores`, `alerts` — no scoring, and the digest is stateless
- `sources` — it's a YAML file; it does not need a table

---

## 5. Parsing

Keep v1's parser hierarchy — it was right. One tier is added above it, numbered 0
because it is not a fallback for the others but a check to run first:

0. **A vendor's own product JSON, where one exists.** Shopify stores expose
   `/collections/<handle>/products.json?limit=250`: title, price,
   `compare_at_price`, SKU and an `available` boolean per variant, with no
   scraping and nothing to break silently. eTek is on this path (§3), and it is
   worth checking for on *every* candidate source before writing a selector —
   Shopify is common among Canadian refurbishers, and it turns a source that
   would cost ongoing maintenance into one that costs almost nothing. Sources on
   this tier are fetched directly by `poll.py` and do not go through
   ChangeDetection at all (§1).
1. **JSON-LD / schema.org Product / embedded product JSON.** ChangeDetection can
   extract these directly with JSONPath/jq, so this tier often needs no custom
   code at all.
2. **Per-retailer CSS selectors** from `sources.yaml`.
3. **Regex fallback** over the title/description — `32GB DDR4 512GB NVMe` and its
   many spellings.
4. **LLM** — only on repeated failure, and not in v1.

**A tier-0 source is structured, not complete.** eTek's JSON is clean and stable,
and it still does not contain a storage interface: across all ten listings,
`NVMe`, `M.2`, `SATA` and `PCIe` appear zero times in the titles *and* zero times
in the descriptions. Every machine is "120GB SSD" or "256 SSD" — often without
the `GB`. So `storage_type` is parsed as `ssd` and the `storage_nvme` requirement
is **not met**, which costs `else_penalty: 40` and prints as `nvme unknown`.

It is tempting to recover the missing field from `chassis.yaml` — if the chassis
has an NVMe slot, assume the drive is NVMe. Don't. That asserts a fact about the
unit for sale from a fact about the model, which is the same conflation that once
had this plan calling a 4-core machine with no NVMe "the cheapest route to 64 GB"
(§3). The chassis says what the box *could* take; only the listing says what is
in it, and here the listing declines to say.

Note this is a requirement miss, not a parse failure: `parse_ok` stays true. A
listing held out of the ranking is invisible, and holding out every listing
because the vendor is vague would print an empty report rather than an honest one.

**eBay sellers are vague in two more ways, and both get the same treatment.**
- **"M.2" is read as `ssd`.** It names a connector, not an interface, and
  `chassis.yaml` already records M.2 sockets that are SATA-only (the 9020 Micro)
  and ones that take either. Only `NVMe` in the title makes it `nvme`.
- **A bare capacity is a drive of unstated type.** In "16GB 512GB Win 11 Pro"
  the 512 is the drive, but nothing says whether it is an SSD, and a bare
  "500GB" on a 6th-gen machine is often a hard disk. So `storage_type` stays
  null, with a note, and the listing pays the same nvme penalty. Which figure
  is the drive is decided by size alone: every RAM figure in these titles is
  64 GB or less and no drive is under 120 GB, so an unlabelled capacity of
  120 GB or more is the disk. The RAM parser reads the same threshold. Without
  it, a title whose only capacity was "256GB" had that drive read as 256 GB of
  RAM, which was in the first poll's data, masked only because those listings
  also failed on storage. "256/512GB" is not read: it names a choice of
  configurations, not this machine's drive.

A listing that says "No HDD" ships without a drive and stays `parse_ok = false`.
Ranking it needs the price of a drive as a `cost_to_reach` term, a §2 decision
not yet made.

Normalization target (unchanged from v1):

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

### Chassis table

v1 had no equivalent of this, and it is more important than the CPU table.

The binding requirement in §2 is *64 GB after upgrade*. Whether a machine can get
there is a property of the **chassis and its platform**, not of the CPU and not
of what the retailer shipped: nearly every Tiny/Micro/Mini box is 2 × SODIMM, so
the ceiling is `2 × (largest SODIMM the platform will accept)`, and that is
16 GB per slot far more often than expected — see the populated table below.

Under the §2 rules that is not an exclusion — it is a `ram_max_gb` miss carrying
`else_penalty: 325`, so such a machine still ranks and still appears in the
near-miss section. What makes this table load-bearing is that **the shortfall is
invisible in the listing title**: nothing on the page says the chassis caps at
32 GB, so without this lookup the penalty is never applied and the machine ranks
as though it could reach 64 GB. That is a silent pricing error, not a loud one.

```yaml
# config/chassis.yaml — shape only; the real file carries a source comment per entry
dell-optiplex-3070-micro: {ram_slots: 2, ram_max_gb: 32, m2_nvme_slots: 1, sata: true}
dell-optiplex-7070-micro: {ram_slots: 2, ram_max_gb: 32, m2_nvme_slots: 1, sata: true}
dell-optiplex-3080-micro: {ram_slots: 2, ram_max_gb: 64, m2_nvme_slots: 1, sata: true}
hp-elitedesk-800-g4-mini: {ram_slots: 2, ram_max_gb: 32, m2_nvme_slots: 2, sata: true}
lenovo-m73-tiny:          {ram_slots: 2, ram_max_gb: 16, m2_nvme_slots: 0, sata: true}
# ...one entry per model that actually appears in the tracked inventory
```

Keyed by `brand-model-formfactor`, and built independently of `canonical_key`
(§4): `dell:optiplex3070:i5-9500t:16gb:256gb` has no form-factor segment
(`-micro`), and the listing title cannot be trusted to supply one — see
"Resolving a title to a chassis key" below. `m2_nvme_slots` feeds the NVMe hard
requirement and the `cost_to_reach(NVMe)` term in the same way. It counts M.2
sockets that will actually take an NVMe drive, which is not the same as counting
M.2 connectors: a B-key socket may be SATA-only, and the 2230 Wi-Fi socket never
counts. Both distinctions cost real entries during the first population.
`ram_type` (DDR3/4/5) picks which `parts.yaml` module prices the RAM upgrade;
it is a chassis fact because one model number can span two (below).

The values above are the verified ones. 64 GB is not the norm from 8th gen
onward: of the nine chassis in eTek's inventory, **two** reach 64 GB officially — the 600 G3 SFF on four full-size DIMM sockets, and the 3080
Micro on two 32 GB SODIMMs. Dell and HP specify the 8th/9th-gen Micro/Mini
machines at
32 GB — 16 GB per slot — and several are reported running 2 × 32 GB anyway, but
an unofficial ceiling is by construction the one figure that cannot be verified
from vendor documentation, so it stays in a comment rather than a field. The
consequence for §2 is that the `else_penalty: 325` path is the common case, not
the exception: most of the tracked inventory is a near-miss.

Two rules keep it small and honest:

- **Populate it on demand.** One entry per model that shows up in tracked
  inventory — eTek's ten listings are nine chassis (two share one). Do not
  pre-fill it.
- **A missing chassis is `parse_ok = false`, never a default.** Guessing
  `ram_max_gb: 64` for an unknown model would silently pass a machine through the
  one filter that cannot be undone after purchase. Unknown chassis surface in the
  digest (§6) as needing a lookup, and an unresolved one is excluded, not
  assumed.

Values come from the vendor's own spec sheet or PSREF/QuickSpecs, and each entry
is worth a one-line source comment — this is the table where being wrong costs
money rather than a bad ranking.

### Resolving a title to a chassis key

The table above is only useful if a listing can be mapped onto it, and the first
attempt at that — read the brand, the model and the form factor out of the title
— does not survive real data.

**The form factor cannot be parsed from this vendor's text.** Checked against
eTek's ten listings: the 9020's description contains `tiny`, `micro`, `sff`,
`usff`, `ultra` *and* `small form factor`, all six, for one machine. The 3070's
title says `Mini` where its chassis key says `-micro`, and its body says `tiny`.
The words are marketing boilerplate pasted between listings, not a specification.

**Brand plus model number is clean.** The same ten listings yield `m73`, `9020`,
`600 g3`, `5060`, `400 g5`, `3070`, `7070`, `800 g4`, `3080` — nine values, nine
chassis, no collisions. So resolution is a small alias table in
`config/chassis_aliases.yaml` mapping `(brand, model_number)` to a chassis key,
with the form-factor segment supplied by whoever adds the entry, from vendor
documentation, rather than parsed from prose:

```yaml
# config/chassis_aliases.yaml — this vendor's naming, not hardware facts
dell:
  "3070": dell-optiplex-3070-micro
  "3080": dell-optiplex-3080-micro
```

It is a separate file from `chassis.yaml` deliberately: that file records
hardware with a vendor citation per entry, this one records how one retailer
spells things. Mixing them would put an eTek quirk inside a Dell fact.

Two details the real data forced:

- **Strip the CPU token before matching the model number**, or the OptiPlex 5060
  listing's `i5-8500` reads as a second model number.
- **Never read the Shopify `handle`.** Both 3080 listings have handles saying
  `3090`, and the 9020's handle says `dell-optiplex-3070-...-copy` — it was
  duplicated from another product and never renamed. The handle is still the
  right unique key for a listing; it is not evidence about the hardware.

This approach resolves 10/10 and is unaffected by eTek spelling the product
`Optiflex`, because neither the brand nor the model number depends on the product
name being spelled correctly.

A `(brand, model_number)` pair absent from the alias table is `parse_ok = false`
and is held out of the ranking — the same rule as a missing chassis entry, for
the same reason.

**On eBay a form-factor word may veto an alias, never resolve one.** One model
number arrives as Micro, SFF and Tower in the same results: the 2026-09-30
capture's "OptiPlex 3080 Micro" search held seven 3080 SFFs and a 3080 Tower
among the Micros, and the alias table would have priced every one as a Micro.
So a title naming a form factor that contradicts its alias — `SFF`, `Tower` or
`MT` on a Micro/Mini/Tiny key; `Micro`, `Tiny`, `USFF` or `MFF` on an SFF key —
is `parse_ok = false` with a note. The finding above still holds, which is why
the word is trusted only to reject: a title with no form-factor word resolves as
before, and every eTek and ITRefurbs title is consistent with its alias. `Mini`
is in no list, because "Mini PC" is generic marketing on SFF listings too. eBay's
own Form Factor field is no better than the refurbishers' prose: one 3080 Micro
listing gave it as "Micro Tower".

**A Lenovo generation is part of the model number.** The M70q is DDR4 through
Gen 4 and DDR5 from Gen 5, with storage changing too (PSREF), and the M80q and
M70s split the same way. So "M70q Gen 5", "M70q G5" and "M70q Tiny Gen 5" read
as `m70q gen5`, as HP's "600 G3" already reads as `600g3`, and the alias table
maps generations to chassis. A title that names no generation is held out, even
though Lenovo's own name for Gen 1 is plain "M70q": guessing Gen 1 prices a
Gen 5 with the wrong memory, and enough titles state the generation that the
held-out ones are not missed.

### CPU table

One entry per CPU that appears in tracked inventory, not v1's 50–100 chips
entered in advance:

```yaml
# config/cpus.yaml — shape only; the real file carries an Intel ARK link per entry
i5-8500T:  {cores: 6, threads: 6,  gen: 8,  tdp: 35, nested_virt: true}
i5-9500T:  {cores: 6, threads: 6,  gen: 9,  tdp: 35, nested_virt: true}
i7-9700T:  {cores: 8, threads: 8,  gen: 9,  tdp: 35, nested_virt: true}
i5-10500T: {cores: 6, threads: 12, gen: 10, tdp: 35, nested_virt: true}
```

`nested_virt` is the field v1's CPU table omitted and the one the §2 **gate** actually
tests — the single requirement no price can offset. It is nearly always `true`
for Intel 8th-gen-and-later and Zen 2+ — which is itself the useful finding:
**the CPU almost never excludes anything, so this table mostly supplies the core
count and the chassis table above does the real gating.** Keep `nested_virt`
anyway to catch the exceptions (Celeron/Pentium/Atom-based tiny PCs).

Expand the table only when a listing appears with a CPU that isn't in it — the
parser logs that as `parse_ok = false` and it shows up in the digest.

---

## 6. Output: one email, when something moved

v1's three alert tiers are cut. The digest is the only mode.

**The digest sends on change, not on a schedule.** Daily mail
about ten SKUs whose prices sit still for weeks is the same failure this section
already names for empty digests — it trains its one reader to stop opening it.
So the poll stays daily, because history cannot be backfilled, and the *send* is
gated on a listing's price moving, appearing, vanishing, changing stock, or
crossing the qualification line (`src/changes.py`).

"Moved" is defined on **list price and qualification, never effective price.**
Effective price also moves when `rules.yaml` or `parts.yaml` is edited, and a
digest arriving because you tuned a penalty is a digest about your own
keystrokes. Tuning is done while looking at the report.

Every digest now opens with what changed since the last one, above the ranking,
because that delta is why this email exists today rather than yesterday. The
state it compares against lives in the database rather than a file, since the
database is what CI commits and what you pull back (§1).

The one thing that must not be gated this way is failure — see §8.

```text
🖥️  Mini-PC Digest — Sep 21

  (no listing meets every requirement)

  ── near misses ────────────────────────
  $625 eff.  Lenovo M73 Tiny         eTek         $140 + $325 ram_max + $80 + $40
             i5-4570T / 16GB max / 120GB SSD
             ✗ ram_max_gb 16 < 64   ✗ cores 4 < 6   ✗ nvme unstated

  $705 eff.  Dell OptiPlex 5060      eTek         $300 + $325 ram_max 32GB + $40
             i5-8500 / 32GB max / 256GB SSD
             ✗ ram_max_gb 32 < 64      (lower the bar → ranks 1st)
             ✗ nvme unstated
             ~street $340 (eBay sold, n=14)

  $955 eff.  Dell OptiPlex 3080 Micro eTek        $550 + $325 RAM + $40 + $40
             i5-10500T / 32→64GB / 256GB SSD
             ▸ ships with 32GB — one SODIMM to source, not two
             ✗ nvme unstated      ← the only miss
             60d warranty, 30% restocking fee on non-defective returns

  $1085 eff. HP ProDesk 600 G3 SFF   eTek         $225 + $700 RAM + $80 + $40
             i5-6500T / 16→64GB / 120GB SSD   (4 × DIMM, 2 kits)
             ✗ cores 4 < 6   ✗ nvme unstated

  ── excluded (12) ──────────────────────
  2 no nested virt (gate)
  2 seller below gate (rating / reviews)
  8 ranked above with penalties applied

  ⚠  <html source>: title selector returned nothing, 3 checks running
  ⚠  chassis unknown, excluded until looked up: hp-elitedesk-805-g6-mini
```

Ranked by effective price, with the delta shown so the arithmetic is visible.

The near-miss section is what makes the §2 dial usable. A count of exclusions
tells you nothing about whether the bar is set right; seeing the $300 OptiPlex
5060 that failed *only* on RAM ceiling, with its effective price and what it
would rank if the bar moved, is the information needed to decide. The
`(lower the bar → ranks 1st)` annotation shows exactly what the current setting
is costing, so tuning is a judgement about a real machine rather than a guess at
a threshold.

Note also the `~street` line: a reference price from eBay sold listings, with
`n=14` shown so a thin sample is visible as such. That is the strongest of the
four baselines in §2 and is labelled by source, as is the `↓ below 30d median`
flag — the digest never presents a flag without saying where it came from,
because they differ by an order of magnitude in how much they mean.

The `▸ ships with 32GB` marker is `prefer_shipped_ram_gb` (§2). It carries no
dollars and does not move the ranking; here it marks the one machine in the
collection whose memory is already installed rather than waiting to be sourced
from an end-of-life supply.

The ranking is substantially a ranking of *RAM requirements*: a $225 computer
carrying $700 of memory places last.

Qualifiers and near-misses are separate blocks because of §2's flat-`else_penalty`
distortion. Read as one sorted list, this mock says "buy the $140 M73", a 4-core,
16 GB-max machine from 2013.

Instant alerts can be added later if a genuinely time-sensitive deal is ever
missed.

For the selector-health warning at the bottom, see §8.

### eBay additions: multi-configuration listings and keyword exclusions

Two blocks the refurbishers never needed, both decided against the 2026-09-30
eBay capture described in §2's shipping note.

**Multi-configuration listings get their own section, unranked.** An eBay
listing can offer several configurations at one URL — `C $149.99 to C $439.99`,
"i7/i5, up to 32GB" — which is not one machine and cannot be scored as one. They
print in a section beside the ranking, each as its price range and link:

```text
  -- multi-configuration (not ranked) --
  $149.99-$439.99  Dell OptiPlex Tiny Micro i7/i5 up to 32GB
                   <url>
```

The two alternatives both lost. Skipping them hides live inventory for no
stated reason, the failure §9's "every listing appears" rule exists to prevent.
Expanding each variant into its own row may be possible from the API, but that
is unverified, and the section works whatever the API turns out to expose, so
it is the cheaper thing to replace later.

The range comes from the listing's variation group, one extra call each. The
search returns a single variant's price for the whole group — $339.99 for a
listing whose variants ran $149.99 to $489.99 — so it cannot supply the range
itself. The group's variants do carry their own CPU and price, which is what
expanding them would build on.

Two limits keep it small. Only listings whose chassis reaches 64 GB appear, since
a capped chassis cannot qualify at any variant and the title names the chassis
even when it does not name the configuration — about 5 of 69 results were ranges
in one search, and that count scales with every chassis searched. And they are
left out of the what-moved comparison, as out-of-scope listings are: a range
moves whenever one variant sells out, and mail about that is noise.

**Keyword exclusions are counted, not dropped.** eBay results mix in parts —
drive caddies, bezels, motherboards, barebones units. A title containing a
listed word is excluded, and so is eBay's "For parts or not working" condition,
which is a structured field. False positives are accepted, so the digest makes
them findable:

```text
  eBay: 14 excluded by keyword (caddy 5, bezel 4, motherboard 3, barebone 2)
```

A word hiding thirty listings a day stands out in that line. The rule for the
list: **a word must name a part, never a machine type or an accessory.** `mount`
fails it — "VESA mount included" is common in genuine mini-PC titles — and so
would `workstation`, for the reason §9 Phase 3 gives. The list lives in config.
Blocked sellers, sellers below a gate and sellers accepting no returns are
counted in the same block, one line per kind. All of them are also left out of
the what-moved comparison, for the multi-configuration reason above: a caddy
appearing is not news. Held-out listings (§2) stay in it, since they are
candidates waiting on a number.

**Held-out listings are counted by reason, with what they would cost.** One
origin can hold out dozens, so each reason prints once, with how many of its
listings would qualify and the cheapest of those priced *without* the missing
term. That is the number that says whether setting `import_adjustment` is worth
doing today.

**eBay's parse failures are tallied, not listed.** The refurbishers print two
warning lines per unparsed listing; at eBay's volume that is a hundred lines a
day, which is a warning nobody reads (§8). The tally names every reason, and an
unknown chassis by brand and model number, so it doubles as a list of the
`cpus.yaml` and `chassis.yaml` entries that would pay off:

```text
  ebay: 203 parse_ok=false, by reason (a listing can have several):
     104  no storage found in title
      72  no CPU found in title
      65  cpu not in cpus.yaml: i5-10400T
```

---

## 7. Layout

```text
mini-pc-price/
├── docker-compose.yml      # changedetection - unscheduled, see §1
├── config/
│   ├── sources.yaml        # per source: urls + selectors, or a products.json
│   ├── watch_urls.yaml     # manually seeded product URLs (any retailer)
│   ├── chassis.yaml        # RAM ceiling + M.2 slots per model — the real gate
│   ├── cpus.yaml           # cores + nested_virt, per CPU seen in inventory
│   ├── parts.yaml          # RAM/NVMe upgrade costs
│   ├── sellers.yaml        # gates + fulfillment adjustment
│   ├── out_of_scope.yaml   # listings dismissed by hand, by URL (§9, Phase 3)
│   └── rules.yaml          # the gate, tunable requirements + penalties (§2)
├── data/
│   └── raw/                # persisted fetch responses, by source + timestamp (§1)
├── src/
│   ├── poll.py             # fetch → parse → SQLite
│   ├── ranking.py          # the shared filter-and-rank step (§2)
│   ├── out_of_scope.py     # hand-dismissed listings, ahead of parsing (§9)
│   ├── dotenv_lite.py      # reads a git-ignored .env; real env wins (§6)
│   ├── report.py           # ranking → console (Phase 1, §9)
│   ├── digest.py           # ranking → email (Phase 2)
│   ├── changes.py          # what moved since the last digest (§6)
│   ├── coverage.py         # did the poll actually run (§8)
│   ├── chart.py            # price history → self-contained chart.html (§6)
│   ├── specs.py            # title/JSON-LD → normalized specs
│   ├── cd_client.py        # thin ChangeDetection REST wrapper — unscheduled
│   └── db.py               # two tables, plus digest_state (§6)
├── scripts/                # local Windows scheduling; CI is the normal path (§1)
├── .github/workflows/
│   └── daily.yml           # poll → digest → commit the history back (§1)
├── tests/
│   ├── test_specs.py       # the parser is what needs tests
│   └── test_ranking.py     # out-of-scope and dedup partitions (Phase 3)
└── README.md
```

A handful of source files, none of them a framework. `docker-compose` would run
one container (ChangeDetection); the tracker is a scheduler plus Python. Neither
the container nor `cd_client.py` exists, and neither is scheduled — every source
tracked or reviewed so far serves tier-0 JSON, so CD waits for a source that
needs it (§1, §9).

`report.py` and `digest.py` share the filter-and-rank step in `ranking.py`, which
leaves `report.py` with rendering only. It was extracted when `digest.py`, the
second consumer, arrived, not in anticipation of it.

`digest.py` does not re-render the ranking. Its body *is* `report.build_report`'s
output, so there is one rendering with two destinations rather than two
renderings that drift until the unread one is wrong.

`data/raw/` (§1) is not a cache and nothing reads it in the normal path. It is
git-ignored: at ~50 KB per fetch it is the bulky part, and losing it costs a
debugging convenience rather than data.

**`data/tracker.db` is committed, and it is the exception to the usual rule
against versioning build output.** It is not build output: it is the observation
log, and it is the only file in the project that cannot be reconstructed. Re-
running `poll.py` produces today's prices, never last month's — the asymmetry
§1 is built around. Committing it is the backup, and it costs little (~20 KB per
10 listings; roughly 7 MB after a year of daily polls).

Two consequences worth stating, because a committed binary is unusual:

- `git diff` says nothing readable about it. That is fine — the file is queried
  with SQL, and git is carrying durability and a record of when each poll ran,
  not reviewability.
- A poll run makes the working tree dirty. Committing the result is part of
  polling, not a separate chore; a run whose observations are never committed is
  a run whose history exists only on one disk.

**Not on Proxmox.** v1 pictured the finished system running on the mini PC, but
the mini PC hasn't been bought yet; finding it is the entire point. It runs in CI
(§1).

---

## 8. Risk the v1 plan didn't name

Selector breakage is the main ongoing cost, not the domain logic.

**A change-gated digest sharpens this considerably.** Once
mail only arrives when something moved (§6), a tracker that has stopped working
produces exactly what a quiet market produces: nothing. The two are
indistinguishable from the inbox, and the failure is invisible for as long as
you are willing to believe prices are flat.

So every digest carries a poll-coverage line — `polled 26 of the last 30 days` —
and a poll silent for three days sends the digest *regardless* of whether
anything changed. That AND is the load-bearing part: suppressing on "nothing
changed" alone would go quiet exactly when nothing is being fetched, because
nothing that is not fetched can change.

Coverage is computed from the observation log, never from a success flag written
by the poller. A poller that dies before writing has no opinion about whether it
ran; the observations either exist or they do not.

Running in CI (§1) adds two failure modes this catches: GitHub disables
scheduled workflows after 60 days of repository inactivity, and its cron is
explicitly best-effort — late under load, occasionally skipped. Neither announces
itself. Both appear as gaps in the coverage line.

Retailers restructure pages without warning. A selector that silently returns
nothing looks exactly like "no new products" — the system goes quiet and appears
to be working. Several of these sites will also rate-limit or block automated
access outright.

Mitigations, all cheap:

- Check hourly at most. There is no deal that requires 5-minute polling.
- Review each retailer's terms before adding it.
- **Track per-selector health.** If a selector yields nothing for 3 consecutive
  checks, surface it in the digest (§6).
- Record `parse_ok` per listing so parser gaps are visible rather than silent.
- **Treat an empty tier-0 response as a failure, not as zero results.** Shopify
  answers a wrong or retired collection handle with HTTP 200 and
  `{"products": []}` — well-formed, successful, and empty, which is exactly what
  a genuinely sold-out collection returns. Tier 0 removes selector breakage but
  not this: the handle is the selector. `poll.py` should treat zero products from
  a source that has previously returned some as a health alert rather than a
  quiet day. (Found by fetching a guessed handle and briefly concluding eTek's
  collection had disappeared; the real handle, in `sources.yaml` above, was
  fine.)
- **Never default an unknown chassis** (§5): it is held out and listed in the
  digest for a manual lookup.

---

## 9. Phasing

### Phase 1 — prove the pipeline (one retailer)

```text
eTek products.json → poll.py → data/raw/ → parse → SQLite → console
```

No email, and **no ChangeDetection** — eTek is a tier-0 JSON source, so `poll.py`
fetches it directly and the container is deferred to Phase 3 (§1). The bar for
done: a source yields listings whose specs parse correctly into the schema, and
prices accumulate across checks.

Phase 1 is meant to stand alone, and stopping here is a legitimate outcome.
eTek's collection is ten listings; once they are parsed into the schema with
`effective_price` computable by hand from the same numbers, the buying decision
may simply be answerable by reading the table. Everything from Phase 2 on exists
to widen the search and to remove the manual step, not to make the decision
possible. Build Phase 1, look at the data, then decide whether Phase 2 is still
worth it.

Phase 1 also built three things that are nearly free early and expensive or
impossible later: `config/chassis.yaml` (§5), `config/rules.yaml` (§2), and
`compare_at_price` capture (§2, §4). eTek was the target because it is the
source least likely to fail for reasons unrelated to the pipeline (§3).

#### What the console prints

`report.py`, run by hand against the SQLite written by `poll.py`. Not a second
digest implementation: it prints the same ranking §6 describes, minus the email,
the baselines that need history, and the multi-source sections.

```text
eTek | 10 listings | fetched 2026-09-22 14:05 | parse_ok 10/10
rules.yaml: ram>=64GB(325) cores>=6(80) nvme(40) | parts: SODIMM32 $325
==============================================================================
QUALIFIES - meets every requirement (0)
------------------------------------------------------------------------------
  (none)

NEAR MISSES - 1+ requirement short (10)
------------------------------------------------------------------------------
  $624.99  Lenovo M73 Tiny            $139.99 list  + 325 pen + 80 pen
           i5-4570T / 4c / 8GB / 120GB SSD       + 40 pen + 40 src
           x ram_max 16GB<64, cores 4<6, nvme unknown
  $704.99  Dell OptiPlex 5060 Micro   $299.99 list  + 325 pen + 40 pen + 40 src
           i5-8500 / 6c / 16GB / 256GB SSD
           x ram_max 32GB<64, nvme unknown
  ...
  $954.99  Dell OptiPlex 3080 Micro   $549.99 list  + 325 RAM + 40 pen + 40 src
           i5-10500T / 6c / 32->64GB / 256GB SSD *ships 32GB
           x nvme unknown
  $1084.99 HP ProDesk 600 G3 SFF      $224.99 list  + 700 RAM + 80 pen + 40 src
           i5-6500T / 4c / 16->64GB / 120GB SSD  (4 x DIMM, 2 kits)
           x cores 4<6, nvme unknown
  $1159.99 Dell OptiPlex 3080 Micro   $429.99 list  + 650 RAM + 40 pen + 40 src
           i5-10500T / 6c / 16->64GB / 256GB SSD
           x nvme unknown
==============================================================================
cheapest qualifier: none | cheapest near-miss $624.99
closest to qualifying: $954.99 3080 Micro - misses only on nvme unknown
* = ships >=32GB (prefer_shipped_ram_gb) - no EOL DDR4 to source

warnings
------------------------------------------------------------------------------
  ! no listing meets every requirement, and all 10 miss on the same field:
    storage interface is unstated across every title and description -
    vendor says only "SSD". Every listing pays nvme(40); none is confirmed
    NVMe. Two listings miss on nothing else.
  ! parts.yaml priced 2026-09-22; DDR4 is EOL and rising 10-20%/mo.
```

That is the first run's real output (2026-09-22), before eTek confirmed by email
that the 3080 Micros carry NVMe (`listing_overrides.yaml`).

**An empty qualifier section is a normal state, not an error**, and the format
has to hold up in it. Hence `(none)` rather than a missing heading, and
`cheapest qualifier: none` rather than a `gap` line with nothing to subtract.

Five properties, each with a reason:

- **Two sections, never one list.** Qualifiers and near-misses are separated
  because at `else_penalty: 325` every near-miss undercuts every qualifier (§2),
  so a single sorted list reads as "buy the cheapest" and recommends a machine
  that cannot host the lab. The `cheapest qualifier` line states that trade
  explicitly rather than leaving it to be inferred from the ordering.
- **The arithmetic is shown, not the result.** `$224.99 list + 700 RAM + 80 pen`
  is the whole argument for a number that would otherwise be unfalsifiable. Every
  figure in it traces to a line in `rules.yaml` or `parts.yaml`, and the header
  echoes those settings so the output is self-contained — a printout from last
  week can be read without guessing which thresholds produced it.
- **Misses are named individually.** `x cores 4<6, nvme unknown` rather than a total.
  The §2 dial is tuned by seeing *which* requirement is doing the excluding, and
  a machine short on three counts is a different proposition from one short on
  RAM ceiling alone.
- **Every listing appears.** Ten in, ten printed. Phase 1's bar is that specs
  parse correctly, and a listing silently dropped for failing a filter is
  indistinguishable from one the parser lost. `parse_ok n/10` in the header is
  the check; anything with `parse_ok = false` prints in the warnings block with
  its raw title, since that is the failure Phase 1 exists to surface.
- **Warnings are not decoration.** Unknown chassis, unparseable fields and stale
  `parts.yaml` dates print at the bottom every run. Each is a conclusion a reader
  would otherwise have to reconstruct from the ranking, and belongs on screen
  rather than in a file nobody opens before paying.

**ASCII only, and this is a constraint rather than a preference.** Windows
consoles default to cp1252, where printing `✗`, `▸`, `↓` or `⚠` raises
`UnicodeEncodeError` and kills the run — §6's mock digest, written for email,
would crash outright. Use `x`, `*`, `!` and `->`. The same applies to reading the
vendor JSON: open it with an explicit `encoding='utf-8'` or listing titles with
typographic punctuation will fail on the way in.

**Dell Outlet moves to Phase 3b**, where it fits naturally with the other
well-structured, zero-adjustment vendors. It remains the better source to
actually buy from — manufacturer warranty, clean RMA — and the better exercise of
the JSON-LD tier. It is simply the harder thing to build first, and nothing is
learned in Phase 1 that requires it.

Also wire `config/watch_urls.yaml` here — manually seeded product URLs from any
retailer, including marketplaces. It is a few lines (a URL is a watch), it makes
marketplace listings trackable immediately without any discovery work, and it is
the fallback whenever discovery breaks on a source.

**A seeded URL is only as parseable as the page behind it**, so "any retailer"
overstates it. A Shopify product URL is tier 0 and needs nothing new — the
same `products.json` path already in use, one product instead of a collection.
Anything else is an HTML page needing selectors, which is precisely what §1
defers to Phase 3 along with ChangeDetection.

Recognising a tier-0 URL is positional — `/products/<handle>`, optionally behind
`/collections/<collection>` — plus one exception found by a test rather than by
reasoning: the handle must not be a bare number. Best Buy's
`/en-ca/category/products/12345` matches the positional rule exactly and is an
SKU path on a non-Shopify store, so the permissive reading would have produced a
watch that 404s on every run.

So the Phase 1 version of this file covers tier-0 URLs and records the rest as
pending rather than silently failing on them. That is narrower than "any
retailer" suggests, and it is still worth having: it is the mechanism by which a
listing found by hand enters the observation log, and an observation not recorded
today cannot be recovered later (§1). A seeded URL that cannot yet be parsed
should surface as such, for the same reason an unknown chassis does — a watch
that quietly does nothing is indistinguishable from one that found nothing (§8).

### Phase 2 — the digest

`digest.py`, the §2 filter (gate, then requirements with penalties),
effective-price ranking, the near-miss section, available baseline flags, Gmail
via app password. Now it is useful.

As built, this phase went further than the paragraph above in two ways, both
argued where they belong rather than here: the send is gated on movement (§6),
and the schedule moved off the desktop into CI so the history has no holes in it
(§1). `chart.py` was added alongside, answering "is this price unusual for this
machine" on demand rather than in the mail.

The near-miss section (§6) is part of Phase 2, not a later polish. It is what
makes `rules.yaml` tunable in practice: without seeing the machines that just
missed — and what they would rank if the bar moved — adjusting a threshold is
guesswork. It is also a few lines, since those listings are already scored.

**What §6's mock shows that Phase 2 does not send.** Three features in that mock
need data no phase before them produces, and a digest with placeholder rows is
worse than one without them:

- `~street $340 (eBay sold, n=14)` — the reference price, Phase 4.
- `↓ below 30d median` — needs an observation history that does not exist on a
  database a few days old.
- the selector-health warning — with the first HTML source, which Phase 3
  turned out not to have (see below).

The `(lower the bar → ranks 1st)` annotation is the one that needs no new data,
only re-scoring with a requirement relaxed. It is deferred anyway: the bar is
answering the question it was meant to answer, and the annotation earns its
place when tuning is actually in question.

**Scheduling is part of Phase 2, not an afterthought.** "One ranked digest a day"
is the project's premise, and a script someone remembers to type is not that.
`scripts/install-task.ps1` registers the Task Scheduler entry;
`scripts/run-daily.ps1` is what it runs, polling and then sending, appending both
to a monthly log.

Two choices there follow from §8 rather than from convenience. The digest sends
**even when the poll fails**, with the staleness stated in words at the top of
the warnings — skipping the send produces silence, and silence is exactly what a
working day with no new deals looks like, which is the silent-breakage failure
mode §8 names as the main ongoing cost. And the task is `StartWhenAvailable` but
not `WakeToRun`: a missed poll is an observation that cannot be backfilled (§1),
so a desktop that was off should catch up at the next wake — but waking a machine
to check refurbisher prices is a worse trade than polling an hour late.

`digest.py` handles SMTP failure by exiting with a line naming which step failed,
for the same reason. An unhandled traceback in a log nobody opens is
indistinguishable from a quiet morning.

The secret handling is worth stating once: the Gmail app password is read from
`MINIPC_SMTP_PASS` in the environment, never from `config/*.yaml`, because those
files are committed. A git-ignored `.env` is loaded for local testing, with
`.env.example` as the committed template — and the real environment always wins
over it, because a stale `.env` silently beating a scheduled task's variables is
an invisible failure (the digest sends from the wrong account, or not at all,
with nothing pointing at the file). `digest.py` exits naming the unset variable rather than
letting Gmail answer with a generic authentication failure, and `--dry-run`
deliberately resolves the recipient but *not* the credentials — the machine
someone debugs the output on is the machine least likely to have the password
set.

### Phase 3 — refurbishers + dedup

Add ITRefurbs — the second of the two independent refurbishers that survived the
§3 vendor review, eTek being already in from Phase 1. This is the phase where
cross-vendor comparison starts to matter, so `source_adjustment` needs to be
applied consistently before these listings compete with each other.

ChangeDetection does not land here: `itrefurbs.ca` turned out to be tier-0
Shopify too (§1), so adding it is a `sources.yaml` entry.

What the phase costs instead is parsing. `refurbished-desktops` is mixed: of 11
products, 5 are small-form desktops and the rest are gaming towers, a
workstation and an Asus mini *tower*. eTek's collection was entirely mini PCs,
so nothing had ever had to distinguish "could not read this" from "read it fine,
wrong kind of machine". Both landed in `parse_ok = false`, and would have been
reported as parse failures daily, forever.

**As built (2026-09-27):**

- **"Not a candidate" is a hand-kept list of URLs, not a rule**
  (`config/out_of_scope.yaml`). A dismissed listing is taken out ahead of the
  parse check and printed as one line in an OUT OF SCOPE section, with its
  reason. A title-keyword rule was the alternative and lost on a real listing:
  "Lenovo ThinkStation P340 *Workstation*" is a P340 **Tiny** — the description
  gives PSREF's dimensions — so excluding on "workstation" would have hidden a
  candidate without anyone deciding to. The cost is that each new tower shows
  up once as `parse_ok=false` until someone adds it. That is the §8 trade taken
  the usual way round: a visible chore instead of an invisible error. Six towers
  are listed today; dismissed listings are also left out of the digest's
  what-moved comparison, so a gaming rig's price move does not send mail.
- **The 800 G3 SFF's gap was TB, not the description.** It says "2 TB HDD", and
  `parse_storage` only read GB. Storage now falls back to the description too,
  as RAM does, though no current listing needs it.
- **The description is trusted for the storage interface, narrowly.**
  ITRefurbs' titles say "256 GB SSD" and their Key Features say "256GB NVMe
  SSD". The description upgrades `ssd` to `nvme` only when it names the *same
  capacity*, which separates a statement about this drive from boilerplate
  about the product line, and the report marks such listings `NVMe per the
  description`. This is not the chassis inference §5 forbids: the listing
  states it. It removes the flat `storage_nvme` penalty for three listings.
- **Two new entries, not three:** `lenovo-p340-tiny` (PSREF) and `i3-10300T`
  (ARK). The 800 G4 alias carries the one-model-several-chassis hazard in
  `chassis_aliases.yaml`, and ITRefurbs tested it: one 800 G4 is titled plain
  "Desktop". Its stated dimensions are the Desktop Mini's, so the alias held,
  after being checked rather than assumed.
- **Multi-source seams.** The report header counted `listings[0]`'s source and
  staleness was a single newest-observation check. Both are per source now, and
  every listing line names its vendor. `poll.py` isolates sources, so one
  vendor's endpoint failing no longer costs the other its observation (§1), and
  the run still exits non-zero.

Result, 2026-09-27: 5 of 11 rank, 6 are out of scope, none fail to parse. No
ITRefurbs listing qualifies — the two 800 G4 Minis are 32 GB-capped near-misses
at $586.99 and $599.99, and the P340 Tiny reaches 64 GB but has four cores —
so the $914.99 eTek 3080 Micro is still the cheapest qualifier.

Cross-retailer dedup via `canonical_key` (kept in full from v1) becomes
meaningful with multiple sources:

```text
              dell:optiplex7070:i5-9500t:16gb:256gb
                          │
                ┌─────────┴─────────┐
              eTek               ITRefurbs
              $350                 $399
```

One line in the digest, not two. The cheaper effective price leads and the other
prints beneath it as `also <vendor> $<price> <url>` (`ranking.collapse_duplicates`).
Folding happens only within a block, never across it: a hand override can make
one unit qualify and its twin not, and a qualifier folded under a near-miss would
be hidden in the block a reader skips.

Worth tempering the expectation: both vendors stock HP EliteDesk 800 G4 Minis,
but eTek's is an i7-8700T/16GB and ITRefurbs' is a different configuration, so
they do **not** share a `canonical_key` and dedup does not fire. Off-lease
inventory is heterogeneous — same chassis, different CPU, RAM and disk — so exact
config collisions between two small refurbishers will be rarer than the diagram
suggests. The comparison that actually matters here is chassis-level and
effective-price-level, which the ranking already does. Dedup earns its keep in
Phase 4, where eBay carries many sellers listing identical machines.

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

1. **eBay.ca** — first; the case for it is in §3. Browse API: structured JSON,
   seller reputation as fields, no scraping. Fixed-price listings only. Add the
   seller gates and `fulfillment_adjustment` here.

   This phase also delivers the reference price (§2) — the baseline the plan
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

**eBay was built ahead of Phase 3b (decided 2026-09-30).** Phase 3
added only near-misses, and eBay is the one source likely to change the answer.
Only eBay moves: the rest of this phase keeps its place, because Best Buy
Marketplace depends on Phase 3b's Best Buy direct. The phase numbers group
sources by kind, so they stay.

Decided against a manual capture of one eBay.ca search (2026-09-30) before any
API access, and argued in the sections that use them:

- **Shipping and import costs** enter the effective price, and currency is
  recorded but never converted (§2).
- **Multi-configuration listings** get an unranked section of their own (§6).
- **A keyword exclusion list** takes parts out of eBay results, with a count
  in the digest (§6). This is not a reversal of Phase 3's per-URL list, which
  stays for the refurbishers. That list is a chore per listing, fine for a
  ten-product collection and unworkable at eBay's volume and daily turnover. The
  case that sank a keyword rule there, P340 "Workstation", was a word naming a
  *machine type*, which §6's rule for the list excludes.

Two findings from the same capture that the build has to absorb:

- **The search page is evidence, not a feed.** It shows which fields exist, but
  fetching it on a schedule is the marketplace search scraping §3 rejected. The
  feed is the Browse API.
- **Titles alone parse 21 of 109.** The largest failure is a CPU missing from
  `cpus.yaml` (config), and the next is titles like "i5 10th Gen" that name no
  CPU at all, which no title parser can fix.

Three more from the first live poll (2026-10-02: 441 listings, 95 parsed, three
eBay qualifiers), which changed the build:

- **The API searches every category; the website does not.** The same
  "OptiPlex 3080 Micro" query returned 161 items, 34 of them machines and the
  rest motherboards, power supplies and RAM. Searches are restricted to
  category 179 (PC Desktops & All-In-Ones), a structured field, so this is a
  filter rather than a guess. The keyword list still catches what sellers
  miscategorise.
- **The per-item spec fields do not close the CPU gap.** They were expected to.
  A title saying "i5 10th Gen" has a Processor field saying "Intel Core i5 10th
  Gen." too. RAM and SSD sizes are there, but they were rarely what failed, so
  the fields are not read. The remaining fix is `cpus.yaml` entries.
- **No eBay username is stored** (§3), because of how the production key was
  enabled.

**The reference price is unconfirmed.** The Browse API returns active listings.
Sold prices appear to need the Marketplace Insights API, which is limited-release
and needs eBay's approval. Check that before building `reference_prices`; if
access is refused, the street-price baseline needs a different source or does
not happen.

Realistically, the machine may well have been bought before all of this lands.
That is a successful outcome, not an abandoned project.

---

## 10. What changed from v1, and why

v1 was a separate document, `plan-v1.md`. It was deleted once v2 had been
carrying the project for long enough to prove it, so this table is now the only
record of what v1 said. Section numbers pointing into v1 were removed with it --
the rows below name each decision in words instead, because a citation nobody can
follow is worse than none. The file remains recoverable from git history if a row
here ever proves too terse.

| v1 | v2 | Reason |
| --- | --- | --- |
| Webhook push | REST poll | Webhooks fire on change; a gap-free observation log is the substrate for every §2 comparison, and cannot be backfilled |
| FastAPI service | Two cron scripts | Nothing left to serve once pull-based |
| Weighted deal score | Hard filter + effective price | Unfalsifiable weights vs. explainable rules |
| Score as-listed | Score post-upgrade cost | RAM is upgradeable, so the real comparison is the machine after its upgrade |
| 12 sources at once | eTek first, rest phased | Prove the pipeline before breadth; start with the source least able to fail for non-pipeline reasons |
| Uniway as Phase 1 target | Excluded entirely | Legitimate but consistently marked up — a bad *source*, not a bad vendor |
| REFURB.io as a Phase 3 source | Excluded (gate) | Fulfillment and refund failures 2022+; the positive reports are from 2017 |
| — | eTek added (Phase 1) | Shopify `products.json`; collection is entirely mini PCs |
| — | eBay.ca added (Phase 4, first) | Absent from v1; Browse API is structured, and it holds the off-lease inventory the refurbisher tier was a thin substitute for |
| — | `chassis.yaml` (§5) | The 64 GB ceiling is a chassis property and the one requirement that is invisible in a listing; v1 had nowhere to put it |
| — | One gate + tunable requirements (§2) | Only nested virt is truly binary; the rest are preferences with a price, and the bar needs to move as real inventory density becomes known |
| — | Near-miss section in the digest (§6) | Exclusion *counts* cannot tell you whether the bar is set right; the machine that just missed can |
| — | `compare_at_price` captured (§2, §4) | Weak signal, one column, and unbackfillable — an unrecorded observation is gone |
| — | Street price from eBay sold listings (§2, Phase 4) | The only baseline that identifies an underpriced machine on first sighting |
| Historical median as the key signal | Demoted to a tiebreaker | Not just sparse — self-referential: it cannot tell whether a price was ever good |
| — | Parser tier 0: vendor product JSON | Removes selector breakage entirely where it applies |
| — | `listing_overrides.yaml` (§5) | A vendor's emailed answer is real evidence with nowhere to live: the page never changes, so the next poll would overwrite it. Applied after parsing, before scoring, with provenance required |
| Refurbisher trust as a constant | `source_adjustment` in dollars | "Legit but slow support" is a price, not a gate |
| Marketplace search scraping | Manual seeding + platform alerts | Value was never the issue; search-page scraping is |
| Seller scoring function | Gates + dollar adjustment | Binary risk for a single purchase; dollars compare directly |
| 7 tables | 2 tables | Add tables when they have a consumer |
| 50–100 CPUs | One per CPU seen in inventory, + `nested_virt` | Hand-entry cost; the omitted field was the load-bearing one |
| 3 alert tiers | Daily digest only | A digest that gets read beats alerts that get ignored |
| Deploy on Proxmox | Run in CI (§1) | The mini PC doesn't exist yet |
| — | Selector health tracking | Silent breakage is the real failure mode |
| Phase 1 as a stepping stone | Phase 1 as a standalone deliverable | Ten parsed listings may answer the buying question on their own |
| Vendor research open-ended | Closed to breadth, open to depth | Sweeping for more vendors returns little; checking one vendor that a decision depends on is the normal case, and Refurbish Canada is the worked example — the check reversed an add-this-source decision |

### Kept from v1, unchanged

The CD/custom-code boundary, SQLite over Postgres, hardware normalization, the
parser hierarchy (with a tier added above it), canonical product keys and dedup,
digest-over-alerts, config-not-code for retailer specifics,
start-with-one-retailer phasing, and no search engine.

v1's historical median is kept but **demoted** — recorded from Phase 1, shown when
it exists, and no longer the primary "is this cheap" signal. v1's underlying
question was the right one; it just picked the weakest of the four available
answers (§2).

v1's seller-scoring *premise* is kept too — marketplaces contain good sellers
worth monitoring, and questionable sellers need explicit handling rather than
blanket exclusion. Only the mechanism changed, from a scoring function to gates
plus a dollar adjustment.

### Revised within v2

Changes to v2 itself, kept separate from the v1 comparison above so that table
stays a record of one revision rather than a running log.

| Was | Now | Reason |
| --- | --- | --- |
| All sources through ChangeDetection (§1) | Tier-0 JSON fetched directly; CD from Phase 3 | CD adds nothing to a vendor JSON API and four ways for Phase 1 to fail in the harness rather than the parser; what it would have given is bought back by persisting raw responses and writing an observation every run (§1) |
| 64 GB assumed normal from 8th gen on (§5) | 32 GB is normal; 2 of 9 tracked chassis reach 64 GB | Populating `chassis.yaml` against vendor documentation refuted it, so the `else_penalty` path is the common case, not the exception (§5) |
| `m2_slots` (§5) | `m2_nvme_slots` | An M.2 connector is not an NVMe socket: B-key sockets are SATA-only and Wi-Fi 2230 sockets are not storage at all. The old name counted both, and did so wrongly in §5's own example |
| "RAM is upgradeable and cheap" (§2) | Upgradeable, not cheap | 2 × 32 GB DDR4 SODIMM is ~$650 in Canada as of 2026-09. §2's worked example inverted: the 16 GB machine plus RAM now *loses* to the 32 GB one. The post-upgrade method is unchanged and still right; only its conclusion moved |
| `ram_max_gb.else_penalty: 120` (§2) | `325` | 120 was set when it was roughly the price of the missing capacity. It now costs ~$325, so the penalty was understating the miss five-fold and charging a permanently-capped chassis less than a capable one paid to actually upgrade |
| — | `prefer_shipped_ram_gb: 32` (§2) | `cost_to_reach` assumes the upgrade is purchasable at the modelled price; DDR4 is end-of-life with supply vanishing and prices guided up 10–20%/month. A tiebreaker and a digest label rather than a dollar term — availability risk is not a price |
| SFF form factor an open scope question (§3, `chassis.yaml`) | SFF in scope, towers out | Size does not change the job for a machine that sits and runs, and SFF chassis take four full-size DIMMs — they are disproportionately the ones reaching 64 GB at all |
| Phase 1 console output unspecified (§9) | Specified: two sections, shown arithmetic, named misses, all listings, warnings, ASCII only | Drafted against the real ten listings, which immediately caught that the ProDesk 600 G3 SFF fails `cpu_cores` and `storage_nvme` and never qualifies, a conclusion two earlier sections had drawn wrongly from its RAM ceiling alone |
| `dell-optiplex-3080-ultra`, inferred (§5, `chassis.yaml`) | `dell-optiplex-3080-micro`, documented | eTek's two "3080 Ultra" listings are 3080 **Micro** machines: the descriptions say Micro 8–9 times against one "Ultra" (inside "Ultra-Compact"), and the URL handles say `3090`, copied from another product. The values (2 slots, 64 GB) are unchanged; the fix removes an inference |
| Storage interface assumed parseable (§2, §5, §6, §9) | Unstated by this source; `storage_nvme` unmet on every listing | `NVMe`, `M.2`, `SATA` and `PCIe` appear zero times across all ten titles and all ten descriptions — eTek says only "SSD". The mocks in §6 and §9 printed `256GB NVMe`, which was assumed, not read. Not recovered from `chassis.yaml` (§5) |
| Two qualifiers expected in Phase 1 (§6, §9) | Zero; ten near-misses | Consequence of the storage row above: `storage_nvme` is unmet on every listing, so nothing clears every requirement. The closest is the $549.99 3080 Micro, which ships 32 GB, reaches 64 GB on one more SODIMM, and misses on nothing but the unstated interface. The mocks are now the real output, and the format has to read well with an empty qualifier section, because on real data that is the case it is in |
| Chassis key "free once the title parses", derived from `canonical_key` (§5) | An explicit `(brand, model_number)` alias table (`chassis_aliases.yaml`) | `canonical_key` has no form-factor segment, and the form factor cannot be read from this vendor's text at all (§5) |
| Desktop Task Scheduler (§9, Phase 2) | GitHub Actions, committing the database back (§1) | A sleeping desktop loses the day rather than polling late, and an observation cannot be backfilled. Still no service (§1) |
| Digest sends daily (§6) | Sends only when something moved, or the poll went quiet (§6, §8) | Ten SKUs that sit still for weeks produce identical daily mail, which is the same thing §6 already refuses for empty digests. Gated on list price and qualification, never effective price -- the latter moves when you edit `rules.yaml`, and a digest about your own keystrokes is noise |
| -- | Poll coverage in every digest (§8) | The cost of the row above: once mail only arrives on change, a dead tracker and a quiet market look identical. Coverage is read from the observation log rather than a success flag, and three silent days send the digest regardless of change |
| -- | `chart.py`, on demand (§6) | "Is this price unusual for this machine" is a question asked while deciding, not every morning. Self-contained HTML with the data inlined: no CDN, no matplotlib, nothing to break later |
| Dependencies listed in the README only | `requirements.txt` | CI needs a declared install, which is the consumer the file was previously waiting for |
| ITRefurbs needs ChangeDetection and selectors (§1, §9 Phase 3) | Also tier-0 Shopify JSON; CD unscheduled | Checked rather than assumed, 2026-09-25: `itrefurbs.ca` serves the same `products.json` shape as eTek's. The premise came from v1's source survey describing it as an HTML storefront, and was never verified |
| Phase 3b before Phase 4 (§9) | eBay pulled ahead of Phase 3b; the rest of Phase 4 stays | Phase 3 added only near-misses, and eBay is the source likely to change the answer. The phase numbers group sources by kind and are unchanged; Best Buy Marketplace still depends on Phase 3b |
| No shipping term in the effective price (§2) | `shipping` and `import_adjustment` added; currency recorded, never converted | The refurbishers' JSON carries no shipping, so it was invisible. On eBay it ran from free to $1,131 across one search, 66 of 110 listings shipped from abroad, and a cheap US listing ranked without it is not cheap. Import cost is a hand-set figure per origin rather than a duty rate, because computers enter duty-free and the real cost is brokerage and returns |
| Per-URL out-of-scope list for every source (§9 Phase 3) | Plus a keyword exclusion list for eBay, counted in the digest (§6) | A per-URL chore works for ten products and not for eBay's volume. Restricted to words naming parts, which avoids the machine-type word that sank a keyword rule in Phase 3 |
| -- | Multi-configuration listings in an unranked section (§6) | A price range across configurations is not one machine. Skipping them hides inventory; expanding variants depends on unverified API data |
| Form factor never parsed (§5) | Parsed on every source, but only to veto an alias, never to resolve one | eBay returns one model number as Micro, SFF and Tower in the same search, and the alias table priced all three as the Micro. The refurbisher finding that form-factor words are boilerplate still holds, so a word is trusted only to reject |
| Seller gates on a 5-star scale (§3) | eBay's percentage and count, `98.0` / `100`, plus `returns_accepted` | The only marketplace reports feedback as a percentage, and a literal 4.5/5 = 90% gates almost nobody. Returns cost 160 of 441 listings in the first poll and stay a gate, because the hardware facts that matter are only checkable once the box is open |
| eBay's per-item spec fields expected to fix CPU parsing (§9) | Not read; `cpus.yaml` entries instead | Checked against the live API: the Processor field repeats the title's "i5 10th Gen". A field that answers nothing is not worth a call |
| -- | Searches restricted to eBay category 179 (§9) | The API, unlike the website, searches every category: 127 of 161 results for one query were parts |
| -- | No eBay username stored anywhere (§3) | The production keyset is enabled under eBay's "I do not persist eBay data" exemption; the alternative was a public deletion-notification endpoint, which is a service (§1). Block list applied at poll time, raw responses scrubbed |
| -- | Marketplace parse failures tallied by reason (§6) | Two warning lines per listing is a hundred lines a day at eBay's volume. The tally keeps every reason visible and names the config entries that would pay off |
| Storage needed a medium word: SSD, NVMe or HDD (§5) | Also "M.2" (read as `ssd`), French units, and a bare capacity of 120 GB or more (type null) | eBay sellers write "512GB M.2" and "16GB 512GB"; 104 of 441 listings failed on storage. Neither states an interface, so both pay the nvme penalty rather than being upgraded. The 120 GB threshold also stops a lone drive figure being read as RAM |
| One chassis per Lenovo model number (§5) | The generation is part of the model; a title naming none is held out. `ram_type` per chassis prices the upgrade (§2) | M70q Gen 5, M80q Gen 3 and M70s Gen 5 take DDR5 under the same model number (PSREF), and eBay lists them: Gen 5 M70qs were priced with DDR4 modules, $470 low. Untitled ones are held out rather than assumed Gen 1; the cost is one qualifier (§5) |
