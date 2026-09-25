"""Price history -> a self-contained HTML chart (plan.md §6).

    python src/chart.py              writes chart.html and says where
    python src/chart.py --open       and opens it in the browser

On-demand, not mailed. The digest answers "what should I buy today"; this
answers "is this price unusual for this machine", which is a question you ask
while deciding rather than every morning.

No dependencies and no CDN: the data is inlined as JSON and the axes and lines
are drawn as inline SVG. A chart that needs a network to render is a chart that
stops rendering, and matplotlib would add a dependency to a project whose whole
install is requests plus pyyaml.

The prices plotted are LIST prices, not effective prices. Effective price moves
when rules.yaml or parts.yaml is edited, which would put steps in the history
that no vendor ever charged (src/changes.py makes the same choice).
"""

import json
import sys
import webbrowser
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "chart.html"


def series(conn, days=90):
    """One series per listing: its observations oldest-first.

    Listings with a single observation are kept. A flat dot is information --
    it says the machine has been at that price every time it was looked at.
    """
    rows = conn.execute("""
        SELECT l.url, l.title_raw, o.observed_at, o.price, o.in_stock
        FROM observations o
        JOIN listings l ON l.id = o.listing_id
        WHERE o.observed_at >= datetime('now', ?)
        ORDER BY l.id, o.observed_at
    """, (f"-{days} days",)).fetchall()

    grouped = {}
    for row in rows:
        entry = grouped.setdefault(row["url"], {
            "title": row["title_raw"], "url": row["url"], "points": []})
        entry["points"].append({"t": row["observed_at"], "p": row["price"],
                                "s": bool(row["in_stock"])})
    return list(grouped.values())


def render(data, days):
    """The whole page, as one string. Data inlined, nothing fetched."""
    return _TEMPLATE.replace("__DATA__", json.dumps(data)) \
                    .replace("__DAYS__", str(days))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    days = 90
    for arg in argv:
        if arg.startswith("--days="):
            days = int(arg.split("=", 1)[1])

    conn = db.connect(ROOT / "data" / "tracker.db")
    data = series(conn, days)
    conn.close()

    if not data:
        print("No observations yet. Run poll.py first.")
        return 1

    OUTPUT.write_text(render(data, days), encoding="utf-8")
    points = sum(len(d["points"]) for d in data)
    print(f"{OUTPUT} | {len(data)} listings, {points} observations, {days}d")
    if "--open" in argv:
        webbrowser.open(OUTPUT.as_uri())
    return 0


# Kept as one template rather than assembled from fragments: it is a document,
# not a data structure, and reading it as a document is how a layout bug is
# spotted. The SVG is built in the browser because the alternative -- computing
# every coordinate in Python -- puts the layout in two languages at once.
_TEMPLATE = """<!DOCTYPE html>
<meta charset="utf-8">
<title>Mini-PC price history</title>
<style>
  :root { --bg:#fbfbfa; --fg:#1a1a18; --grid:#e3e3e0; --muted:#6b6b66; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16161a; --fg:#e8e8e4; --grid:#2c2c33; --muted:#8a8a84; }
  }
  body { background:var(--bg); color:var(--fg); margin:0; padding:24px;
         font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
  h1 { font-size:18px; margin:0 0 4px; font-weight:600; }
  .sub { color:var(--muted); margin-bottom:20px; }
  .wrap { max-width:1000px; margin:0 auto; }
  svg { width:100%; height:420px; display:block; }
  .legend { display:flex; flex-wrap:wrap; gap:6px 16px; margin-top:16px; }
  .item { display:flex; align-items:center; gap:7px; cursor:pointer;
          font-size:12px; padding:3px 6px; border-radius:4px; }
  .item:hover { background:var(--grid); }
  .item.off { opacity:.35; }
  .sw { width:11px; height:11px; border-radius:2px; flex:none; }
  .item a { color:var(--muted); text-decoration:none; }
  .item a:hover { text-decoration:underline; }
  #tip { position:fixed; pointer-events:none; background:var(--fg);
         color:var(--bg); padding:6px 9px; border-radius:5px; font-size:12px;
         opacity:0; transition:opacity .1s; white-space:nowrap; z-index:9; }
  text { fill:var(--muted); font-size:11px; }
</style>
<div class="wrap">
  <h1>Mini-PC price history</h1>
  <div class="sub" id="sub"></div>
  <svg id="c" viewBox="0 0 1000 420" preserveAspectRatio="none"></svg>
  <div class="legend" id="legend"></div>
</div>
<div id="tip"></div>
<script>
const DATA = __DATA__, DAYS = __DAYS__;
const PAL = ["#c2410c","#0369a1","#15803d","#a16207","#7e22ce","#be123c",
             "#0f766e","#4338ca","#b45309","#0891b2"];
const L=52, R=16, T=14, B=34, W=1000, H=420;
const hidden = new Set();

const times = DATA.flatMap(d => d.points.map(p => new Date(p.t+"Z").getTime()));
const t0 = Math.min(...times), t1 = Math.max(...times);
const span = (t1 - t0) || 1;

function bounds() {
  const ps = DATA.filter((d,i) => !hidden.has(i))
                 .flatMap(d => d.points.map(p => p.p));
  if (!ps.length) return [0, 1];
  let lo = Math.min(...ps), hi = Math.max(...ps);
  if (lo === hi) { lo -= 20; hi += 20; }
  const pad = (hi - lo) * 0.08;
  return [Math.max(0, lo - pad), hi + pad];
}
const x = t => L + ((t - t0) / span) * (W - L - R);
const y = (p, lo, hi) => T + (1 - (p - lo) / (hi - lo)) * (H - T - B);

function draw() {
  const [lo, hi] = bounds();
  let s = "";
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, yy = y(v, lo, hi);
    s += `<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/>`;
    s += `<text x="${L-8}" y="${yy+4}" text-anchor="end">$${v.toFixed(0)}</text>`;
  }
  const oneDay = 864e5;
  const step = Math.max(1, Math.round(span / oneDay / 6));
  for (let t = t0; t <= t1 + 1; t += step * oneDay) {
    const d = new Date(t);
    s += `<text x="${x(t)}" y="${H-12}" text-anchor="middle">` +
         `${d.getMonth()+1}/${d.getDate()}</text>`;
  }
  DATA.forEach((d, i) => {
    if (hidden.has(i)) return;
    const col = PAL[i % PAL.length];
    const pts = d.points.map(p => [x(new Date(p.t+"Z").getTime()),
                                   y(p.p, lo, hi), p]);
    if (pts.length > 1) {
      s += `<path d="M${pts.map(q => q[0]+","+q[1]).join("L")}" fill="none" ` +
           `stroke="${col}" stroke-width="2" stroke-linejoin="round"/>`;
    }
    pts.forEach(([cx, cy, p]) => {
      s += `<circle cx="${cx}" cy="${cy}" r="${pts.length>1?3:4.5}" fill="${col}"` +
           ` fill-opacity="${p.s?1:.25}" stroke="${col}" data-i="${i}"` +
           ` data-t="${p.t}" data-p="${p.p}" data-s="${p.s?1:0}"/>`;
    });
  });
  document.getElementById("c").innerHTML = s;
}

const tip = document.getElementById("tip");
document.getElementById("c").addEventListener("mouseover", e => {
  const c = e.target.closest("circle"); if (!c) return;
  const d = DATA[+c.dataset.i];
  const stock = c.dataset.s === "1" ? "" : " (out of stock)";
  tip.textContent = `$${(+c.dataset.p).toFixed(2)} - ` +
    `${c.dataset.t.slice(0,10)}${stock} - ${d.title.slice(0,46)}`;
  tip.style.opacity = 1;
});
document.getElementById("c").addEventListener("mousemove", e => {
  tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = (e.clientY - 34) + "px";
});
document.getElementById("c").addEventListener("mouseout", () => tip.style.opacity = 0);

document.getElementById("legend").innerHTML = DATA.map((d, i) =>
  `<span class="item" data-i="${i}">` +
  `<span class="sw" style="background:${PAL[i % PAL.length]}"></span>` +
  `<span>${d.title.slice(0, 44)}</span>` +
  `<a href="${d.url}" target="_blank" rel="noopener">open</a></span>`).join("");

document.getElementById("legend").addEventListener("click", e => {
  const item = e.target.closest(".item");
  if (!item || e.target.tagName === "A") return;
  const i = +item.dataset.i;
  hidden.has(i) ? hidden.delete(i) : hidden.add(i);
  item.classList.toggle("off");
  draw();
});

const obs = DATA.reduce((n, d) => n + d.points.length, 0);
document.getElementById("sub").textContent =
  `${DATA.length} listings, ${obs} observations, last ${DAYS} days. ` +
  `Click a listing to hide it. Hollow points were out of stock.`;
draw();
</script>
"""

if __name__ == "__main__":
    sys.exit(main())
