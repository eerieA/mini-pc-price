"""Listing title and description -> normalized specs (plan.md §5).

This is the module that earns tests. Everything else in Phase 1 moves data
between a JSON API, SQLite and a console; this is the part that reads human-
written marketing copy and has to be right about hardware.

Three rules run through it, all of them learned from real listings rather than
anticipated:

  - Never default. An unknown chassis or an unknown CPU sets parse_ok False and
    the listing is held out of the ranking. Guessing a RAM ceiling silently ranks
    a machine as better than it is, and that error is only discovered after the
    box is open (§5).
  - Never infer a listing's configuration from its chassis. The chassis says what
    the box CAN take; only the listing says what is in it.
  - Never read the URL handle. eTek's handles are copied between products and
    contradict their own titles.
"""

import html
import re

# A CPU token like "i5-9500T", "core i5-8500", "i7 8700T".
CPU_RE = re.compile(r"\b(i[3579])[-\s]?(\d{4,5})([A-Z]{0,2})\b", re.I)

# "16GB", "16 GB". Capacities in this position are always RAM in these titles;
# storage is written without the unit ("256 SSD"), which is what separates them.
RAM_RE = re.compile(r"\b(\d{1,3})\s*GB\b", re.I)

# A capacity that names itself as memory: "16GB RAM", "16GB DDR4", or the
# unit-less "16RAM" one vendor writes. Preferred over RAM_RE because it cannot
# be satisfied by a disk.
RAM_LABELLED_RE = re.compile(
    r"\b(\d{1,3})\s*GB\s*(?:of\s*)?(?:RAM|DDR\d|SODIMM|MEMORY)\b"
    r"|\b(\d{1,3})\s*RAM\b", re.I)

# What follows a capacity when that capacity is a disk.
STORAGE_WORD_RE = re.compile(r"\s*(?:GB\s*)?(?:SSD|NVME|HDD|M\.?2|PCIE|SATA)\b",
                             re.I)

# "256 SSD", "256GB SSD", "512GB NVMe", "2 TB HDD". The unit is optional for GB,
# the medium is not. TB needs its unit: a bare "2 SSD" is not a disk size.
STORAGE_RE = re.compile(
    r"\b(?:(\d{3,4})\s*(?:GB\s*)?|(\d)\s*TB\s*)(SSD|NVME|HDD)\b", re.I)

# The parse note that marks an interface read from the description. report.py
# prints it beside the listing, since the ranking then rests on vendor prose.
NVME_FROM_DESCRIPTION = "nvme read from description, not title"

RAM_TYPE_RE = re.compile(r"\bDDR([345])\b", re.I)

BRANDS = {
    "dell": "Dell",
    "hp": "HP",
    "hewlett": "HP",
    "lenovo": "Lenovo",
    "thinkcentre": "Lenovo",
}

# Model numbers: a bare 4-digit number, or an HP-style number plus generation
# ("600 G3"), or a Lenovo-style letter-number ("M73", ThinkStation "P340").
# Lenovo's trailing letter is the form factor (q = Tiny, s = SFF), so it belongs
# to the model number rather than being noise after it: m70q and m70s are
# different chassis with different socket counts, and conflating them prices a
# RAM upgrade ~$350 wrong. eTek stocked only an M73, which is why this pattern
# originally stopped at the digits.
MODEL_RE = re.compile(r"\b([mp]\d{2,3}[a-z]?)\b|\b(\d{3,4})\s*(g\d)?\b", re.I)


def strip_html(body_html):
    """Tags out, entities decoded. Emoji and typographic punctuation survive.

    They survive deliberately: the text is only ever matched against, never
    printed. Anything reaching the console is built from parsed fields, because
    a Windows cp1252 console raises UnicodeEncodeError on a single stray glyph
    and eTek's descriptions are full of them (§9).
    """
    if not body_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", body_html)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_cpu(text, cpus):
    """Find a CPU and look it up. Returns (name, spec) or (None, None).

    The name is recorded exactly as written. eTek's OptiPlex 5060 says
    "core i5-8500" where a Micro would normally carry the 35 W i5-8500T, and the
    temptation is to add the T. Don't: rewriting CPU names to match expectations
    is a road with no end, and it would hide a real discrepancy about which
    machine is actually being sold.
    """
    match = CPU_RE.search(text)
    if not match:
        return None, None
    family, number, suffix = match.groups()
    name = f"{family.lower()}-{number}{suffix.upper()}"
    for known, spec in cpus.items():
        if known.lower() == name.lower():
            return known, spec
    return name, None


def parse_ram_gb(title, body_text=""):
    """Shipped RAM in GB, from the title, falling back to the description.

    The title is authoritative because the description is boilerplate: it says
    "Upgradeable to 16GB" and quotes DDR4 speeds, both of which match a bare
    capacity pattern.
    """
    for text, note in ((title, None),
                       (body_text, "ram_gb read from description, not title")):
        size = _ram_in(text)
        if size is not None:
            return size, note
    return None, "no RAM capacity found"


def _ram_in(text):
    """The RAM capacity in one string, or None. Never the disk.

    A bare `\\d+GB` is not enough to identify RAM. One vendor writes "16RAM"
    with no unit at all, so the first `\\d+GB` in the title is the SSD -- which
    produced a 256 GB "RAM" reading, a machine that appeared to need no memory
    upgrade, and the cheapest qualifying listing in the report. So look for a
    capacity that says what it is, and only then fall back to a bare one that is
    not adjacent to a storage word.
    """
    match = RAM_LABELLED_RE.search(text)
    if match:
        return int(match.group(1) or match.group(2))

    for match in RAM_RE.finditer(text):
        trailing = text[match.end():match.end() + 12]
        if STORAGE_WORD_RE.match(trailing):
            continue  # "256GB NVMe SSD" -- a disk, whatever the title's order
        return int(match.group(1))
    return None


def parse_storage(text):
    """Returns (size_gb, storage_type) where type is 'ssd', 'nvme' or 'hdd'.

    'ssd' means the vendor said SSD and nothing more. It is NOT upgraded to
    'nvme' by consulting the chassis: an M.2 socket in the chassis table says the
    box accepts an NVMe drive, not that this one has one. Across all ten eTek
    listings the interface is never stated, so every one of them lands here as
    'ssd' and fails the storage_nvme requirement (§5).
    """
    match = STORAGE_RE.search(text)
    if not match:
        return None, None
    gigabytes, terabytes, medium = match.groups()
    # Decimal, as drives are sold: a "2 TB" disk is 2000 GB, not 2048.
    size = int(gigabytes) if gigabytes else int(terabytes) * 1000
    return size, medium.lower()


def listing_storage(title, body_text=""):
    """(size_gb, storage_type, [notes]) for a listing, title first.

    The description is consulted twice, each time narrowly. If the title states
    no drive, the description's first one is taken, as parse_ram_gb does. If the
    title says only "SSD", the description may name the interface -- but only a
    statement at the SAME capacity counts. ITRefurbs' titles say "256 GB SSD"
    while their Key Features say "256GB NVMe SSD", which is about this drive;
    "supports up to 2TB NVMe" is about the product line, and a capacity match is
    what tells the two apart.
    """
    size, medium = parse_storage(title)
    if size is None:
        size, medium = parse_storage(body_text)
        if size is None:
            return None, None, ["no storage found in title"]
        return size, medium, ["storage read from description, not title"]

    if medium == "ssd" and any(
            parse_storage(m.group(0)) == (size, "nvme")
            for m in STORAGE_RE.finditer(body_text)):
        return size, "nvme", [NVME_FROM_DESCRIPTION]
    return size, medium, []


def _brand(text):
    for token, brand in BRANDS.items():
        if re.search(rf"\b{token}", text, re.I):
            return brand
    return None


def _model_number(title):
    """The model number, normalized to the form used in chassis_aliases.yaml.

    The CPU and every storage/RAM capacity are stripped first. Without that, the
    5060 listing's "i5-8500" supplies a perfectly good 4-digit number and the
    machine resolves to the wrong chassis.
    """
    text = CPU_RE.sub(" ", title)
    text = re.sub(r"\b\d{1,4}\s*GB\b", " ", text, flags=re.I)
    text = STORAGE_RE.sub(" ", text)
    text = re.sub(r"\bwindows\s*\d+\b", " ", text, flags=re.I)

    match = MODEL_RE.search(text)
    if not match:
        return None
    lenovo_style, number, generation = match.groups()
    if lenovo_style:
        return lenovo_style.lower()
    return f"{number}{(generation or '').lower()}"


def chassis_key(title, aliases):
    """Map a title onto a chassis.yaml key via the alias table.

    Brand plus model number only — the form-factor word is not consulted, because
    in this vendor's copy it is boilerplate. One listing's description contains
    "tiny", "micro", "sff", "usff", "ultra" and "small form factor" at once, and
    both 3080 listings say "Ultra" in the title while describing a Micro
    throughout. See config/chassis_aliases.yaml for the full argument.
    """
    brand = _brand(title)
    number = _model_number(title)
    if not brand or not number:
        return None
    return aliases.get(brand.lower(), {}).get(number)


def parse(title, body_html, chassis, cpus, aliases):
    """Parse one listing. Returns a specs dict including parse_ok and notes.

    `chassis`, `cpus` and `aliases` are passed in rather than loaded here so the
    tests can run against the real config files without touching the filesystem
    per case.
    """
    body_text = strip_html(body_html)
    combined = f"{title} {body_text}"
    notes = []

    key = chassis_key(title, aliases)
    if key is None:
        notes.append(f"no chassis key for title: {title!r}")
    chassis_spec = chassis.get(key) if key else None
    if key and chassis_spec is None:
        notes.append(f"chassis key {key!r} not in chassis.yaml")

    cpu_name, cpu_spec = parse_cpu(title, cpus)
    if cpu_name is None:
        notes.append("no CPU found in title")
    elif cpu_spec is None:
        notes.append(f"cpu not in cpus.yaml: {cpu_name}")

    ram_gb, ram_note = parse_ram_gb(title, body_text)
    if ram_note:
        notes.append(ram_note)

    storage_gb, storage_type, storage_notes = listing_storage(title, body_text)
    notes.extend(storage_notes)

    ram_type_match = RAM_TYPE_RE.search(combined)
    brand = _brand(title)

    specs = {
        "brand": brand,
        "model": key.replace(f"{brand.lower()}-", "", 1) if key and brand else None,
        "chassis_key": key,
        "cpu": cpu_name,
        "cpu_cores": cpu_spec["cores"] if cpu_spec else None,
        "cpu_threads": cpu_spec["threads"] if cpu_spec else None,
        "nested_virt": cpu_spec["nested_virt"] if cpu_spec else None,
        "ram_gb": ram_gb,
        "ram_type": f"DDR{ram_type_match.group(1)}" if ram_type_match else None,
        "ram_slots": chassis_spec["ram_slots"] if chassis_spec else None,
        "ram_max_gb": chassis_spec["ram_max_gb"] if chassis_spec else None,
        "m2_nvme_slots": chassis_spec["m2_nvme_slots"] if chassis_spec else None,
        "storage_gb": storage_gb,
        "storage_type": storage_type,
    }

    # An unstated storage interface is NOT a parse failure. It is the expected
    # state of every listing from this source, and holding them all out of the
    # ranking would print an empty report rather than an honest one. It costs the
    # storage_nvme penalty instead — §2's mechanism for "worse, not disqualified".
    specs["parse_ok"] = bool(chassis_spec and cpu_spec and ram_gb and storage_gb)
    specs["parse_notes"] = "; ".join(notes) if notes else None
    specs["canonical_key"] = _canonical_key(specs)
    return specs


def _canonical_key(specs):
    """Cross-retailer dedup key (§4). Unused until Phase 3, cheap to fill now.

    Deliberately not the chassis key: this one identifies a configuration across
    vendors, that one identifies a physical chassis, and neither can produce the
    other.
    """
    parts = [specs["brand"], specs["model"], specs["cpu"],
             specs["ram_gb"], specs["storage_gb"]]
    if not all(parts):
        return None
    brand, model, cpu, ram, storage = parts
    return f"{brand.lower()}:{model}:{cpu.lower()}:{ram}gb:{storage}gb"
