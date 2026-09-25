# mini-pc-price

A deal tracker that watches Canadian retailers for a mini PC capable of hosting
an EVE-NG lab of 5–6 nodes, and ranks what it finds by what the machine actually
costs to make usable.

This is a tool for one buying decision. It has a single user and a handful of
sources; buying the machine ends the project successfully even if most of
`plan.md` is never built.

## The number it computes

A listing price is not what a machine costs. A $225 box that needs $700 of
memory is more expensive than a $550 box that needs $325, and the whole point of
this tool is to sort by the second number rather than the first:

```text
effective_price = listing price
                + cost to reach 64 GB RAM      (from config/parts.yaml)
                + penalties for what it misses (from config/rules.yaml)
                + a vendor recourse adjustment (from config/sources.yaml)
```

One requirement is absolute: `nested_virt`. A CPU without it cannot run the lab
at any price, so those listings are excluded rather than penalised. Everything
else — the 64 GB ceiling, 6 cores, NVMe — is a preference carrying a dollar
penalty, which is what makes the bar movable as real inventory density becomes
known. See `plan.md` §2.

## Running it

```sh
pip install -r requirements.txt       # Python 3.11+; developed on 3.14
python src/poll.py                    # fetch -> parse -> SQLite
python src/report.py                  # ranking -> console
python src/digest.py --dry-run        # ranking -> email, without sending
python src/chart.py --open            # price history -> chart.html
pytest -q
```

**The daily run happens in GitHub Actions, not here** — see below. These commands
are for running it by hand against whatever the last poll collected.

`poll.py` is safe to run as often as you like; `report.py` reads whatever has
been collected and prints. `chart.py` writes a self-contained `chart.html` with
one line per listing — on demand, because "is this price unusual" is a question
you ask while deciding, not every morning.

`digest.py` emails the same output. It needs three environment variables, kept
out of `config/*.yaml` because those are committed:

```sh
MINIPC_SMTP_USER   the Gmail address that authenticates
MINIPC_SMTP_PASS   a Google App Password (not the account password)
MINIPC_DIGEST_TO   where the digest goes
```

For local testing, copy `.env.example` to `.env` and fill it in — `digest.py`
reads it automatically:

```sh
cp .env.example .env
```

`.env` is git-ignored and `.env.example` holds only placeholders. **Real
environment variables win over `.env`**, so a stale file in the working
directory cannot quietly override a scheduled task that sets them properly.

Sending is the only irreversible step here, so `--dry-run` prints the message
and sends nothing. It works without credentials set.

### Running it daily

`.github/workflows/daily.yml` polls at 12:00 UTC, sends the digest if anything
moved, and commits the updated `data/tracker.db` back to the repo. `git pull`
and the local report and chart read the same history.

It runs in CI rather than on a desktop for one reason: **an observation cannot
be backfilled.** A machine asleep at the scheduled hour simply loses that day,
and the price history is the thing a months-long run is accumulating.

Three repository secrets are required (Settings → Secrets and variables →
Actions):

```
MINIPC_SMTP_USER   the Gmail address that authenticates
MINIPC_SMTP_PASS   a Google App Password
MINIPC_DIGEST_TO   where the digest goes
```

**The digest only sends when something moved** — a price changed, a listing
appeared or vanished, something started or stopped qualifying — or when the poll
itself has gone quiet for three days. Daily mail about ten SKUs that sit still
for weeks is how a digest trains its reader to ignore it. `--force` sends anyway.

Every digest opens with what moved since the last one and a poll-coverage line
(`polled 26 of the last 30 days`). Coverage is computed from the observation log
rather than a success flag, so it cannot be fooled by a poller that died before
writing. This matters more once sending is change-gated: a dead tracker and a
quiet market both produce silence, so coverage is what tells them apart — and a
coverage alert sends the digest even when nothing changed.

GitHub disables scheduled workflows after 60 days of repository inactivity, and
its cron is best-effort under load. Both show up as gaps in that coverage line.

To prove the send path still works when nothing has moved for a while: Actions →
`daily` → **Run workflow**, tick *Send the digest even if nothing moved*. That is
the only way to test Gmail accepting a login from a runner — a local send proves
your own IP and `.env`, not GitHub's.

#### Running the schedule locally instead

`scripts/install-task.ps1` registers a Windows Task Scheduler entry doing the
same thing, and `scripts/run-daily.ps1` is what it runs. Kept for the case where
Actions is stalled — running both means two digests on any day something moves.
Logs land in `logs/daily-YYYY-MM.log`, git-ignored.

## Reading the report

```text
  $954.99  Dell OptiPlex 3080 Micro   $549.99 list  + 325 RAM + 40 pen + 40 src
           i5-10500T / 6c / 32->64GB / 256GB SSD *ships 32GB
           x nvme unknown
```

| Element | Means |
| --- | --- |
| `$954.99` | Effective price — what the machine costs once it can do the job |
| `+ 325 RAM` | Real parts cost to reach 64 GB, priced in `parts.yaml` |
| `+ 40 pen` | A requirement missed, charged at its `rules.yaml` penalty |
| `+ 40 src` | Vendor recourse risk (`source_adjustment`) |
| `32->64GB` | Ships with 32 GB, reaches 64 GB |
| `*` | Ships at or above `prefer_shipped_ram_gb` — no EOL DDR4 to source |
| `x ...` | Which requirements it misses |

**`cost_to_reach` and a penalty are never both charged for the same
requirement.** A machine that can reach 64 GB pays the memory; one that cannot
pays the penalty instead.

**The penalties are flat and the parts costs are real, so the ranking can
mislead.** At `else_penalty: 325` against ~$650–700 of actual memory, a machine
capped at 32 GB can rank *above* one that reaches 64 GB. That is a known
distortion, documented rather than tuned away (`plan.md` §2) — which is why the
report separates qualifying machines from near misses instead of printing one
sorted list.

Read the `warnings` block at the bottom. It carries conclusions that are not
recoverable from the ranking: listings that failed to parse, watches that are
idle, and requirements no listing in the database meets.

## Configuration

Thresholds, penalties, vendor adjustments and hardware facts live in
`config/*.yaml`, never in code — that is what keeps the tool tunable by hand.

| File | Holds |
| --- | --- |
| `rules.yaml` | The gate, the tunable requirements, their penalties |
| `sources.yaml` | Where listings come from, and each vendor's `source_adjustment` |
| `watch_urls.yaml` | Hand-seeded product URLs, for listings found by hand |
| `chassis.yaml` | RAM ceiling and M.2 slots per model — the requirement invisible in a listing |
| `chassis_aliases.yaml` | Brand + model number → chassis key |
| `cpus.yaml` | Cores, threads and `nested_virt` per CPU |
| `parts.yaml` | RAM and NVMe upgrade costs, with the date they were priced |
| `listing_overrides.yaml` | Vendor-confirmed facts a listing page does not state |
| `digest.yaml` | Where the daily email goes (no secrets) |

Two config rules worth knowing before editing:

- **Never default an unknown chassis.** A missing `chassis.yaml` entry means the
  RAM ceiling cannot be computed. Assuming 64 GB ranks a machine as better than
  it is, and that error is only discovered after the box is open — so unknown
  chassis are held out of the ranking and named in the warnings instead.
- **Chassis capability is not listing configuration.** `m2_nvme_slots: 1` says
  the model *accepts* an NVMe drive, not that the unit for sale *has* one. The
  code will not infer the second from the first.

## What's stored

Two tables, in `data/tracker.db`.

- `listings` — the current state of a product page, keyed on its URL.
- `observations` — an append-only log of what it cost each time we looked.

Every run writes one observation per listing, **including runs where nothing
changed**. An observation cannot be backfilled: re-polling gives today's price,
never last month's. That asymmetry is why the database is committed to git
despite being build output, and why this project polls on a schedule instead of
reacting to change notifications.

To read a listing's price history:

```sql
SELECT o.observed_at, o.price, o.compare_at_price
FROM observations o JOIN listings l ON l.id = o.listing_id
WHERE l.url = ?
ORDER BY o.observed_at;
```

`data/raw/` holds every fetch response verbatim and is *not* committed. Nothing
reads it in the normal path; it exists so a parser bug found on day 10 can be
fixed against day 1's bytes.

## What works today

One tier-0 source (eTek's Shopify `products.json`), plus hand-seeded Shopify
product URLs via `watch_urls.yaml`. Polls daily in GitHub Actions, emails a
ranked digest when something moves, and charts price history on demand. No
ChangeDetection and no selectors — those arrive with the first HTML source.

A seeded URL that is not a Shopify product page is recorded as **pending** and
named in the report's warnings rather than parsed — HTML sources need selectors,
which arrive in Phase 3. A watch that quietly did nothing would be
indistinguishable from one that found nothing.

`plan.md` is the design of record and carries the reasoning behind every choice
above; its §10 records what the superseded first draft said and why each part
changed. `research/` holds the saved vendor evidence behind the source
decisions.

## Conventions

- **ASCII only in console output.** A Windows console is cp1252, and printing a
  single arrow or check mark raises `UnicodeEncodeError` and kills the run. Read
  files with an explicit `encoding='utf-8'`; write output with `x`, `*`, `!` and
  `->`.
- **`tests/test_specs.py` is the suite that matters.** Spec extraction from
  vendor titles is where bugs actually live — the titles are inconsistent, and
  at least one names a model its own description contradicts.
