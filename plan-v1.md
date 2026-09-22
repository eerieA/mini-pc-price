
The goal is to scrape somewhat credible online shopping hosts for mini PC prices, track them and discover good deals in a timely manner. I will want to use a cost-efficient mini PC to host a EVE NG lab of about 5 to 6 nodes.

Make **ChangeDetection.io the scraping/change-detection engine and deliberately keep the custom service small**. Don't build another scraper. ChangeDetection.io already gives me the hard plumbing: browser/HTTP fetching, CSS/XPath/JSONPath/jq extraction, price/restock detection, schedules, history, and a REST API. It can also send arbitrary JSON HTTP notifications, which is exactly the seam we want for the custom service. ([Change Detection][1])

The architecture I'd use is:

```text
                         ┌──────────────────────────────┐
                         │       ChangeDetection.io     │
                         │                              │
                         │  fetch / browser automation  │
                         │  CSS / XPath / JSON / jq     │
                         │  change detection            │
                         │  price history               │
                         └──────────────┬───────────────┘
                                        │
                               HTTP JSON webhook
                                        │
                                        ▼
                         ┌──────────────────────────────┐
                         │     Deal Intelligence        │
                         │                              │
                         │  normalize listing           │
                         │  parse hardware              │
                         │  calculate deal score        │
                         │  compare price history       │
                         │  deduplicate                 │
                         │  apply seller trust          │
                         │  decide whether to alert     │
                         └──────────────┬───────────────┘
                                        │
                              score >= threshold?
                                  /           \
                                no             yes
                                │               │
                              store            Gmail
```

ChangeDetection.io's current API supports programmatic watch management, tags, notification configuration, filters, price tracking, and browser fetchers, so I can eventually automate most of the configuration rather than manually maintaining dozens of watches. ([Change Detection][1])

## 1. Define the boundary carefully

This is the most important design decision.

### ChangeDetection.io owns

* fetching web pages
* JavaScript rendering
* cookies/browser interactions
* retailer-specific selectors
* detecting that something changed
* detecting price/restock changes
* maintaining raw change history
* triggering the webhook

### My service owns

* product normalization
* extracting/normalizing CPU/RAM/SSD information
* retailer/seller classification
* historical price analysis
* hardware value calculation
* deal scoring
* deduplication
* alert suppression
* Gmail notifications
* my personal "is this actually a good mini-PC?" logic

That keeps my custom code relatively small.

---

# 2. Start with three kinds of watches

Don't immediately create a watch for every product.

I'd use three levels.

### A. Discovery watches

These monitor search/category pages such as:

```text
Canada Computers
  /refurbished-mini-pcs

Uniway
  /tiny-pcs

ITRefurbs
  /mini-pcs

Best Buy
  refurbished mini PCs

Memory Express
  refurbished desktops
```

Their purpose is:

> **Tell me when a new potentially interesting product appears.**

ChangeDetection can extract only the relevant product-card region using CSS/XPath, rather than treating the entire page as meaningful. It also supports JSON/jq extraction when the retailer exposes structured data. ([GitHub][2])

### B. Product watches

Once the discovery service finds:

```text
https://www.example.ca/product/lenovo-m920q...
```

it creates a dedicated watch for that product.

That watch tracks:

* price
* stock
* title
* seller
* condition
* relevant specifications

### C. Historical rechecks

My service periodically asks ChangeDetection for the historical information associated with the product, or maintains its own normalized price observations.

This gives me:

```text
$499 ── $479 ── $449 ── $429 ── $389
                                      ↑
                                  alert!
```

ChangeDetection already has price tracking/history functionality, including configurable minimum/maximum price and percentage-change triggers. ([Change Detection][1])

---

# 3. My first version should probably monitor ~12 sources

I'd start with the sources we discussed:

```text
DIRECT RETAILERS
────────────────
Lenovo Canada / Outlet
Dell Canada / Outlet
Best Buy direct
Canada Computers
Memory Express
Staples Canada

REFURBISHERS
────────────
Uniway
ITRefurbs
REFURB.io

MARKETPLACES
────────────
Amazon.ca
Walmart.ca
Best Buy Marketplace
```

But **don't necessarily create one giant watch per retailer**.

Instead define a source configuration:

```yaml
sources:
  - id: canada_computers
    name: Canada Computers
    trust: 0.95
    discovery_urls:
      - ...
    type: retailer

  - id: uniway
    name: Uniway
    trust: 0.90
    discovery_urls:
      - ...
    type: refurbisher

  - id: amazon
    name: Amazon.ca
    trust: 0.75
    discovery_urls:
      - ...
    type: marketplace
```

The exact URLs/selectors are retailer-specific and should live in configuration rather than code.

---

# 4. Use ChangeDetection's webhook as my integration point

This is one of the reasons I like this architecture.

ChangeDetection.io supports HTTP POST notifications, and its notification body can be constructed using Jinja variables. Its documentation specifically recommends `post://` / `posts://` for JSON-style HTTP notifications and `|tojson` for safely encoding variables. ([GitHub][3])

So conceptually:

```text
ChangeDetection
      │
      │ POST /webhooks/changedetection
      │
      │ {
      │   "watch_uuid": "...",
      │   "url": "...",
      │   "title": "...",
      │   "diff": "...",
      │   "current_snapshot": "..."
      │ }
      │
      ▼
Deal Intelligence
```

There are examples in the project's community showing notification bodies carrying things like `diff`, `diff_added`, `diff_removed`, `current_snapshot`, `watch_uuid`, and URL information. ([GitHub][4])

I'd make the webhook endpoint something like:

```text
POST /api/v1/events/changedetection
```

and **immediately persist the raw event** before doing anything clever.

That gives me a debugging trail.

---

# 5. My database can stay tiny

Don't introduce PostgreSQL, Redis, Kafka, Elasticsearch, etc.

For v1:

**SQLite + SQLAlchemy/SQLModel** would be plenty.

Something like:

```text
sources
────────────────────
id
name
type
trust_score

watches
────────────────────
id
changedetection_uuid
source_id
url
product_key

products
────────────────────
id
canonical_key
brand
model
title

listings
────────────────────
id
product_id
source_id
seller
url
condition
currency

observations
────────────────────
id
listing_id
observed_at
price
in_stock
raw_hash

hardware_specs
────────────────────
listing_id
cpu
cpu_cores
cpu_threads
ram_gb
ram_type
ssd_gb
ssd_type

scores
────────────────────
listing_id
calculated_at
hardware_score
price_score
seller_score
deal_score

alerts
────────────────────
listing_id
score
sent_at
```

This gives me enough structure to answer useful questions later.

---

# 6. Normalize the hardware aggressively

This is probably the first genuinely worthwhile custom component.

Retailer A:

```text
Lenovo ThinkCentre M920q Tiny PC
Intel i5-9500T
32GB DDR4
512GB SSD
```

Retailer B:

```text
ThinkCentre M920Q Tiny
Core i5 9500T / 32G / 512G NVMe
```

Retailer C:

```text
Lenovo M920q Refurbished
i5-9500T 32GB 512GB
```

My database should turn all three into approximately:

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
  "storage_gb": 512,
  "storage_type": "NVMe"
}
```

---

# 7. Don't make the hardware parser too clever initially

I'd use a hierarchy:

### Level 1 — structured data

Look for:

```text
JSON-LD
schema.org Product
OpenGraph
embedded product JSON
```

ChangeDetection is particularly useful here because it can parse JSON embedded inside HTML using JSONPath/jq. ([GitHub][2])

### Level 2 — retailer selectors

For each source:

```yaml
selectors:
  title: ".product-title"
  price: ".price"
  availability: ".availability"
  seller: ".seller"
```

### Level 3 — regex fallback

For example:

```text
32GB DDR4 512GB SSD
```

→

```text
RAM = 32GB
RAM type = DDR4
SSD = 512GB
```

### Level 4 — optional LLM

Only if parsing fails.

And I wouldn't start here.

---

# 8. Create a canonical CPU database

This is another small wheel worth building.

Something like:

```yaml
i5-8500T:
  cores: 6
  threads: 6
  generation: 8
  architecture: Coffee Lake
  tdp: 35
  virtualization: true

i5-9500T:
  cores: 6
  threads: 6
  generation: 9
  architecture: Coffee Lake Refresh
  tdp: 35
  virtualization: true

i5-10500T:
  cores: 6
  threads: 12
  generation: 10
  architecture: Comet Lake
  tdp: 35
  virtualization: true

ryzen-7-5750ge:
  cores: 8
  threads: 16
  architecture: Zen 3
  tdp: 35
  virtualization: true
```

I don't need hundreds of CPUs initially.

For my mini-PC project, maybe **50–100 common business CPUs** covers a huge fraction of the interesting inventory.

---

# 9. Build the deal score in layers

I would **not** start with one mysterious formula.

Have independently interpretable components:

```text
DealScore =
    0.40 × HardwareValue
  + 0.30 × PriceValue
  + 0.15 × SellerValue
  + 0.10 × WarrantyValue
  + 0.05 × HistoricalValue
```

Then:

### HardwareValue

```text
CPU:        45%
RAM:        30%
Storage:    15%
expandability: 10%
```

### PriceValue

Compare against:

* current comparable listings
* my historical observations
* expected price based on hardware

### SellerValue

```text
manufacturer direct     1.00
established retailer    0.95
dedicated refurbisher   0.85–0.95
marketplace seller      0.50–0.90
unknown seller          0.30–0.60
```

These numbers are **configuration parameters**, not facts about those businesses.

---

# 10. Historical pricing is where this gets really powerful

Suppose I collect:

```text
M920q i5-9500T / 32GB / 512GB

Sep 01   $459
Sep 03   $449
Sep 05   $439
Sep 07   $429
Sep 10   $419
Sep 14   $399
Sep 20   $379
```

My service can calculate:

```text
current:       $379
30d median:    $429
30d minimum:   $379
discount:      11.7%
```

Now I can distinguish:

> **"This is cheap"**

from:

> **"This is cheap relative to what this exact class of machine normally sells for."**

That distinction is extremely valuable.

---

# 11. Deduplicate aggressively

This is another area where my service may earn its keep.

I won't want:

```text
M920q i5-9500T 32GB 512GB
M920Q i5 9500T 32 GB 512 SSD
ThinkCentre M920q Tiny 32G 512G
```

to become three "deals."

Create a canonical product key:

```text
lenovo:m920q:i5-9500t:32gb:512gb
```

Then:

```text
                    canonical product
                           │
             ┌─────────────┼─────────────┐
             │             │             │
         Best Buy       Uniway       REFURB.io
          $419            $399          $379
```

My alert can say:

> **M920q / i5-9500T / 32GB / 512GB — $379**
>
> REFURB.io is currently $20 below Uniway and $40 below Best Buy.

That's much more useful than three independent alerts.

---

# 12. Seller handling should be explicit

For marketplace listings, normalize:

```text
retailer = Best Buy
seller = ABC Computers
fulfillment = Best Buy
seller_rating = 4.7
seller_reviews = 1832
```

Then my scoring engine can decide:

```text
seller_quality = f(
    seller_rating,
    seller_review_count,
    return_policy,
    warranty,
    fulfillment,
    known_seller
)
```

This means I don't have to exclude Amazon/Best Buy Marketplace entirely.

I only make questionable sellers **pay a scoring penalty**.

---

# 13. Alert only when there's an actual reason to care

I would have three alert levels.

### 🔵 Interesting

```text
score >= 75
```

Store it, but don't email.

### 🟡 Good deal

```text
score >= 85
```

Email.

### 🔴 Exceptional

```text
score >= 92
```

Email with a prominent subject.

For example:

```text
🔥 Mini-PC deal: M920q / 32GB / 512GB — $379
```

versus:

```text
Mini-PC candidate: HP EliteDesk 800 G5 — $429
```

---

# 14. My Gmail integration can be very simple

I wouldn't have ChangeDetection send the final email.

Instead:

```text
ChangeDetection
       │
       ▼
Deal Intelligence
       │
       ├── score < 85 → DB
       │
       └── score >= 85
                 │
                 ▼
               Gmail
```

Use Gmail SMTP/app-password/OAuth depending on what I prefer.

That means **only my service knows the scoring logic**.

ChangeDetection remains generic.

---

# 15. Add a daily digest later

This might actually be better than receiving 15 emails.

Every morning:

```text
🖥️ Mini-PC Deal Digest — Sep 20

🔥 94 — Lenovo M920q
   i5-9500T / 32GB / 512GB
   $379 — REFURB.io

🔥 91 — HP EliteDesk 800 G5
   i5-9500T / 32GB / 256GB
   $399 — Staples

🟢 86 — Dell OptiPlex 7070 Micro
   i7-9700T / 32GB / 512GB
   $449 — Uniway

🟢 82 — Lenovo M75q
   Ryzen 5 5650GE / 32GB / 512GB
   $479 — Canada Computers
```

Then reserve immediate alerts for:

```text
score >= 92
```

This prevents my system from becoming another notification source I eventually ignore.

---

# 16. Project layout

I'd keep the repository almost boringly simple:

```text
mini-pc-deal-intelligence/
│
├── docker-compose.yml
│
├── config/
│   ├── sources.yaml
│   ├── scoring.yaml
│   └── cpus.yaml
│
├── src/
│   ├── api/
│   │   └── changedetection.py
│   │
│   ├── parsing/
│   │   ├── product.py
│   │   ├── specs.py
│   │   └── cpu.py
│   │
│   ├── scoring/
│   │   ├── hardware.py
│   │   ├── price.py
│   │   ├── seller.py
│   │   └── deal.py
│   │
│   ├── storage/
│   │   ├── models.py
│   │   └── repository.py
│   │
│   ├── notifications/
│   │   └── gmail.py
│   │
│   └── main.py
│
├── tests/
│
└── README.md
```

And:

```text
docker-compose
│
├── changedetection
│
└── deal-intelligence
```

That's basically the entire initial infrastructure.

---

# 17. Phase the implementation

I would **not** try to implement all 12 retailers simultaneously.

### Phase 1 — prove the pipeline

Use **one retailer**:

```text
Canada Computers
       │
       ▼
ChangeDetection
       │
       ▼
Deal Intelligence
       │
       ▼
SQLite
       │
       ▼
console/log
```

No Gmail yet.

Get:

```text
URL → normalized product → specs → score
```

working reliably.

### Phase 2 — add notifications

```text
score >= threshold
       │
       ▼
Gmail
```

### Phase 3 — add 3–4 refurbishers

```text
Canada Computers
Uniway
ITRefurbs
REFURB.io
```

These are especially useful because their seller identity is relatively straightforward.

### Phase 4 — manufacturer/direct retailers

```text
Lenovo
Dell
Best Buy direct
Memory Express
Staples
```

### Phase 5 — marketplace support

Finally:

```text
Amazon
Walmart
Best Buy Marketplace
```

with seller-quality scoring.

---

# 18. One thing I would deliberately *not* build yet

Don't build a sophisticated "search engine."

My first system can simply monitor a curated set of search/category URLs.

For example:

```yaml
queries:
  - "mini pc"
  - "tiny desktop"
  - "micro desktop"
  - "ThinkCentre Tiny"
  - "OptiPlex Micro"
  - "EliteDesk Mini"
  - "ProDesk Mini"
```

Then filter candidates locally.

Once I've accumulated enough data, **then** I can decide whether automated product discovery is worth the complexity.

---

# 19. And ChangeDetection can grow with me

This architecture has a nice property: I am not locking myself into my initial implementation.

ChangeDetection already supports:

* REST API
* JSON monitoring
* jq/JSONPath
* browser-based fetching
* browser steps
* custom headers
* proxy configuration
* scheduled checks
* price/restock detection
* arbitrary HTTP notifications. ([Change Detection][1])

So my custom service can remain focused on the genuinely domain-specific stuff.

If a retailer requires some bizarre JavaScript interaction, **that's a ChangeDetection problem**.

If I decide:

> "A 16GB M920q at $300 isn't necessarily better than a 32GB M720q at $350 for my EVE-NG use case."

**That's my deal-intelligence problem.**

That separation is exactly what I'd aim for.

---

## The resulting system

Eventually I picture my mini-PC server running:

```text
                    ┌───────────────────────┐
                    │       Proxmox         │
                    └──────────┬────────────┘
                               │
                    ┌──────────▼────────────┐
                    │      Docker           │
                    │                       │
                    │ ┌───────────────────┐ │
                    │ │ ChangeDetection   │ │
                    │ │                   │ │
                    │ │ 100+ watches      │ │
                    │ └─────────┬─────────┘ │
                    │           │           │
                    │ ┌─────────▼─────────┐ │
                    │ │ Deal Intelligence  │ │
                    │ │                   │ │
                    │ │ Python/FastAPI     │ │
                    │ │ SQLite             │ │
                    │ └─────────┬─────────┘ │
                    │           │           │
                    │ ┌─────────▼─────────┐ │
                    │ │ Gmail notifier    │ │
                    │ └───────────────────┘ │
                    └───────────────────────┘
```

And the nice part is that **the same mini-PC I'm eventually buying for the EVE-NG community could also run this deal-monitoring service essentially for free**.

I'd consider that a very good fit for my "don't reinvent wheels, but build the missing intelligence" philosophy. ChangeDetection handles the messy web-monitoring machinery; my code becomes a relatively small **Canadian hardware deal normalization/scoring engine** on top of it. ([GitHub][2])

[1]: https://changedetection.io/docs/api_v1/?utm_source=chatgpt.com "ChangeDetection.io API"
[2]: https://github.com/dgtlmoon/changedetection.io?utm_source=chatgpt.com "GitHub - dgtlmoon/changedetection.io: Best and simplest tool for website change detection, web page monitoring, and website change alerts. Perfect for tracking content changes, price drops, restock alerts, and website defacement monitoring—all for free or enjoy our SaaS plan! · GitHub"
[3]: https://github.com/dgtlmoon/changedetection.io/wiki/Notification-configuration-notes?utm_source=chatgpt.com "Notification configuration notes · dgtlmoon/changedetection.io Wiki · GitHub"
[4]: https://github.com/dgtlmoon/changedetection.io/discussions/3330?utm_source=chatgpt.com "Notification transfer as json partly empty · dgtlmoon changedetection.io · Discussion #3330 · GitHub"
