"""Parser tests (plan.md §7: "the parser is what needs tests").

Every case below is drawn from a real eTek listing or from a hazard one of them
exposed. Where a case looks absurd — a title claiming a model its own description
contradicts, a capacity written without a unit — it is quoted from live data, not
invented.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import specs  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config"


def _load(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def chassis():
    return _load("chassis.yaml")


@pytest.fixture(scope="module")
def cpus():
    return _load("cpus.yaml")


@pytest.fixture(scope="module")
def aliases():
    return _load("chassis_aliases.yaml")


# ── Chassis resolution ───────────────────────────────────────────────────────
# The table that matters. A wrong key here means a wrong RAM ceiling, which is
# the one error that costs money rather than a bad ranking (§5).

CHASSIS_CASES = [
    # The ten real listings, verbatim.
    ("Lenovo M73 Core i5-4570T 8GB 120 SSD Windows 10 Pro Tiny Mini Desktop Pc Refurbished",
     "lenovo-m73-tiny"),
    ("Dell optiplex 9020 Tiny Desktop i5-4590T 8GB 120 SSD Windows 10 Pro PC  Refurbished",
     "dell-optiplex-9020-micro"),
    ("HP ProDesk 600 G3 SFF Desktop core i5-6500T 16GB 120 SSD Windows 11 Pro Refurbished",
     "hp-prodesk-600-g3-sff"),
    ("Dell OptiPlex 5060 core i5-8500 16GB 256 SSD Windows 11 Pro Mini Desktop PC Refurbished",
     "dell-optiplex-5060-micro"),
    ("HP ProDesk 400 G5 Mini Desktop core i5-9500T 16GB 240 SSD Windows 11 Pro Refurbished",
     "hp-prodesk-400-g5-mini"),
    ("Dell optiplex 3070  Mini Desktop i5-9500T 16GB 240 SSD Windows 11 Pro PC  Refurbished",
     "dell-optiplex-3070-micro"),
    ("Dell Optiplex 7070 Mini Desktop i5-9500T 16GB 240 SSD Windows 11 Pro PC  Refurbished",
     "dell-optiplex-7070-micro"),
    ("HP EliteDesk 800 G4 Mini Desktop core i7-8700T 16GB 256 SSD Windows 11 Pro Refurbished",
     "hp-elitedesk-800-g4-mini"),
    # "Optiflex" is the vendor's misspelling and "Ultra" is the vendor's error --
    # the description says Micro throughout. Resolution must survive both,
    # because it reads neither the product name's spelling nor the form factor.
    ("Dell Optiflex 3080 Ultra i5-10500T 16GB 256 SSD Windows 11 Pro Mini Computer Pc Refurbished",
     "dell-optiplex-3080-micro"),
    ("Dell Optiflex 3080 Ultra i5-10500T 32GB 256 SSD Windows 11 Pro Mini Computer Pc Refurbished",
     "dell-optiplex-3080-micro"),
]


@pytest.mark.parametrize("title,expected", CHASSIS_CASES)
def test_chassis_key_resolves_real_listings(title, expected, aliases):
    assert specs.chassis_key(title, aliases) == expected


def test_model_number_ignores_cpu_digits(aliases):
    """The 5060's CPU is "i5-8500" -- four digits that would resolve to nothing,
    or worse, to another machine. The CPU token must be stripped first."""
    title = "Dell OptiPlex 5060 core i5-8500 16GB 256 SSD Windows 11 Pro Mini Desktop"
    assert specs._model_number(title) == "5060"


def test_model_number_ignores_windows_version():
    """"Windows 11" is a bare number in every title in the collection."""
    assert specs._model_number("Dell OptiPlex 3070 Windows 11 Pro") == "3070"


UNRESOLVABLE = [
    ("Dell OptiPlex 5050 Micro i5-7500T 16GB 256 SSD", "model absent from the alias table"),
    ("Refurbished Business Desktop 16GB 256 SSD Windows 11", "no brand"),
    ("Beelink SER5 Ryzen 5 5560U 16GB 500GB", "brand absent from the alias table"),
]


@pytest.mark.parametrize("title,reason", UNRESOLVABLE)
def test_unknown_chassis_is_never_defaulted(title, reason, aliases):
    assert specs.chassis_key(title, aliases) is None, reason


# ── CPU ──────────────────────────────────────────────────────────────────────

def test_cpu_with_t_suffix(cpus):
    name, spec = specs.parse_cpu("Dell Optiflex 3080 Ultra i5-10500T 16GB", cpus)
    assert name == "i5-10500T"
    assert (spec["cores"], spec["threads"]) == (6, 12)


def test_cpu_without_t_suffix_is_not_rewritten(cpus):
    """eTek's 5060 says "core i5-8500", not i5-8500T. Record what the listing
    says: teaching the parser to correct CPU names hides a real discrepancy
    about which machine is being sold."""
    name, spec = specs.parse_cpu("Dell OptiPlex 5060 core i5-8500 16GB", cpus)
    assert name == "i5-8500"
    assert spec is not None and spec["cores"] == 6


def test_i5_4570t_is_dual_core(cpus):
    """The trap in the CPU table: an i5 that is 2c/4t. Reading the badge instead
    of the spec would overstate cpu_cores by a factor of two."""
    _, spec = specs.parse_cpu("Lenovo M73 Core i5-4570T 8GB", cpus)
    assert spec["cores"] == 2


def test_unknown_cpu_is_named_not_guessed(cpus):
    name, spec = specs.parse_cpu("Some Desktop i9-13900K 32GB", cpus)
    assert name == "i9-13900K"
    assert spec is None


# ── RAM ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Dell Optiflex 3080 Ultra i5-10500T 32GB 256 SSD", 32),
    ("Lenovo M73 Core i5-4570T 8GB 120 SSD", 8),
    ("HP ProDesk 400 G5 Mini core i5-9500T 16 GB 240 SSD", 16),
])
def test_parse_ram_gb(title, expected):
    assert specs.parse_ram_gb(title)[0] == expected


def test_ram_falls_back_to_description_with_a_note():
    gb, note = specs.parse_ram_gb("Dell OptiPlex 3070 Micro Windows 11 Pro",
                                  "Intel Core i5-9500T | 16GB RAM | 240GB SSD")
    assert gb == 16
    assert "description" in note


# ── Storage: the interface is never stated ───────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    # No "GB" after the number -- the format eTek actually uses.
    ("Dell OptiPlex 5060 core i5-8500 16GB 256 SSD Windows 11 Pro", (256, "ssd")),
    ("HP ProDesk 400 G5 Mini core i5-9500T 16GB 240 SSD", (240, "ssd")),
    ("Lenovo M73 Core i5-4570T 8GB 120 SSD", (120, "ssd")),
    # Not present in this collection, but the pattern must still read it.
    ("Some Mini PC i5-9500T 16GB 512GB NVMe", (512, "nvme")),
])
def test_parse_storage(title, expected):
    assert specs.parse_storage(title) == expected


def test_chassis_capability_never_upgrades_storage_type(chassis, cpus, aliases):
    """The assertion this decision rests on.

    The 3070 Micro has m2_nvme_slots: 1, so the box accepts an NVMe drive. The
    listing says only "240 SSD". storage_type stays 'ssd' and the listing fails
    storage_nvme -- inferring NVMe from the chassis would assert a fact about
    the unit for sale from a fact about the model.
    """
    title = "Dell optiplex 3070  Mini Desktop i5-9500T 16GB 240 SSD Windows 11 Pro PC"
    result = specs.parse(title, "", chassis, cpus, aliases)
    assert result["m2_nvme_slots"] == 1
    assert result["storage_type"] == "ssd"
    assert result["parse_ok"] is True


# ── Encoding (§9: cp1252 stdout, utf-8 input) ────────────────────────────────

def test_strip_html_survives_emoji_and_entities():
    """eTek descriptions open with an emoji and use &amp; throughout. Reading
    must not raise; only the console output is constrained to ASCII."""
    body = "<p>\U0001f4bc HP EliteDesk 800 G4 &amp; Mini &mdash; 16GB RAM</p>"
    text = specs.strip_html(body)
    assert "\U0001f4bc" in text
    assert "&" in text and "&amp;" not in text


def test_typographic_punctuation_does_not_crash(chassis, cpus, aliases):
    title = "Dell OptiPlex 3070 – Mini Desktop i5-9500T 16GB 240 SSD"
    assert specs.parse(title, "", chassis, cpus, aliases)["chassis_key"] \
        == "dell-optiplex-3070-micro"


# ── End to end ───────────────────────────────────────────────────────────────

def test_parse_fills_every_field_for_a_clean_listing(chassis, cpus, aliases):
    title = ("Dell Optiflex 3080 Ultra i5-10500T 32GB 256 SSD Windows 11 Pro "
             "Mini Computer Pc Refurbished")
    body = "<p>Dell OptiPlex 3080 Micro &mdash; 32GB DDR4 RAM 256GB SSD</p>"
    r = specs.parse(title, body, chassis, cpus, aliases)

    assert r["parse_ok"] is True
    assert r["brand"] == "Dell"
    assert r["chassis_key"] == "dell-optiplex-3080-micro"
    assert r["cpu"] == "i5-10500T"
    assert r["cpu_cores"] == 6
    assert r["ram_gb"] == 32
    assert r["ram_type"] == "DDR4"
    assert r["ram_max_gb"] == 64
    assert r["storage_gb"] == 256
    assert r["storage_type"] == "ssd"
    assert r["nested_virt"] is True
    assert r["canonical_key"] == "dell:optiplex-3080-micro:i5-10500t:32gb:256gb"


def test_unknown_chassis_sets_parse_ok_false(chassis, cpus, aliases):
    r = specs.parse("Dell OptiPlex 5050 Micro i5-8500T 16GB 256 SSD",
                    "", chassis, cpus, aliases)
    assert r["parse_ok"] is False
    assert r["ram_max_gb"] is None
    assert "no chassis key" in r["parse_notes"]


def test_all_ten_real_listings_parse(chassis, cpus, aliases):
    """The Phase 1 bar: specs parse correctly into the schema (§9)."""
    for title, expected_key in CHASSIS_CASES:
        r = specs.parse(title, "", chassis, cpus, aliases)
        assert r["parse_ok"] is True, f"{title!r}: {r['parse_notes']}"
        assert r["chassis_key"] == expected_key


# ── Lenovo model suffixes (Refurbish Canada, 2026-09-24) ─────────────────────
# eTek's only Lenovo was an M73, so MODEL_RE was written as `m\d{2,3}` and never
# had to carry the letter. Lenovo's suffix is the form factor -- q is Tiny, s is
# SFF -- so dropping it does not just lose precision, it loses the one segment
# that distinguishes a 2-slot SODIMM machine from a 4-socket UDIMM one.
@pytest.mark.parametrize("title, model, expected_key", [
    ("Lenovo ThinkCentre M70s SFF Desktop PC | Intel Core i5-10400 2.9GHz",
     "m70s", "lenovo-m70s-sff"),
    ("Lenovo ThinkCentre M70q Gen 5 Tiny Desktop | Intel Core i5-14400T",
     "m70q", "lenovo-m70q-tiny"),
    ("Lenovo ThinkCentre M80q Gen 3 Tiny Desktop PC | Intel Core i5-12500",
     "m80q", "lenovo-m80q-tiny"),
    # Upper case in the wild, and the one that must keep working.
    ("Lenovo THINKCENTRE M70Q Tiny Workstation, Intel Core i7-10700T",
     "m70q", "lenovo-m70q-tiny"),
    ("Lenovo M73 Tiny Desktop i5-4570T 8GB", "m73", "lenovo-m73-tiny"),
])
def test_lenovo_model_suffix_is_kept(title, model, expected_key, chassis, cpus,
                                     aliases):
    assert specs._model_number(title) == model
    result = specs.parse(title, "", chassis, cpus, aliases)
    assert result["chassis_key"] == expected_key


# ── RAM must never be read from the storage figure ───────────────────────────
# Refurbish Canada writes "16RAM" with no unit, so RAM_RE found nothing and took
# the next \d+GB in the title -- which is the SSD. The result was ram_gb: 256, a
# machine that appeared to need no memory upgrade and ranked as the cheapest
# QUALIFYING listing at $449.99. A parser bug that invents a bargain is worse
# than one that drops a row, because the output looks like the answer.
@pytest.mark.parametrize("title, ram_gb, storage_gb", [
    ("HP EliteDesk 800 G6 Mini | Intel Core i5-10500T (10th Gen, 6-Core) "
     "| 16RAM | 256GB NVMe SSD | Windows 11 Pro", 16, 256),
    ("Lenovo ThinkCentre M70q Tiny | i5-10400T | 8RAM | 512GB NVMe", 8, 512),
    # The normal form must keep working.
    ("Dell OptiPlex 7010 Micro i5-13500T 16GB 256GB SSD", 16, 256),
    # RAM after storage in the title: order must not decide the answer.
    ("HP ProDesk 600 G6 Mini | 256GB NVMe SSD | 32GB DDR4", 32, 256),
])
def test_ram_is_never_taken_from_the_storage_figure(title, ram_gb, storage_gb,
                                                    chassis, cpus, aliases):
    result = specs.parse(title, "", chassis, cpus, aliases)
    assert result["ram_gb"] == ram_gb
    assert result["storage_gb"] == storage_gb


def test_ram_absent_is_none_not_the_disk(chassis, cpus, aliases):
    """No RAM stated at all must stay unknown. Borrowing the disk size is how
    the $449.99 phantom qualifier happened."""
    result = specs.parse("Dell OptiPlex 7010 Micro i5-13500T 256GB NVMe SSD",
                         "", chassis, cpus, aliases)
    assert result["ram_gb"] is None


# ── ITRefurbs (Phase 3, 2026-09-27) ──────────────────────────────────────────
# A second vendor's copy, and a different shape from eTek's: a condition prefix
# ("Refurbished (Excellent) - "), specs in a parenthesised slash list, capacities
# in TB, and a description whose Key Features block states the drive interface.
# Titles verbatim from https://itrefurbs.ca/collections/refurbished-desktops.

ITREFURBS_CASES = [
    ("Refurbished (Excellent) - HP EliteDesk 800 G4 Desktop Mini w/  Keyboard and "
     "Mouse (Intel i5-8500 / 16 GB RAM / 256 GB SSD / Windows 11 Pro)",
     "hp-elitedesk-800-g4-mini", 16, 256),
    # No form-factor word at all. Resolved to the Mini because the description's
    # dimensions (6.97 x 6.89 x 1.34 in) are the Desktop Mini's -- see the 800g4
    # note in chassis_aliases.yaml.
    ("Refurbished (Excellent) - HP EliteDesk 800 G4 Desktop (Intel i5-8500 / 8 GB "
     "RAM / 256 GB SSD / Windows 11 Pro)",
     "hp-elitedesk-800-g4-mini", 8, 256),
    ("Refurbished (Excellent) - HP ProDesk 400 G5 Tiny MiniPC - Black (Intel "
     "i5-9500T / 8 GB RAM / 256 SSD / Windows 11 Professional)",
     "hp-prodesk-400-g5-mini", 8, 256),
    # The one that failed before Phase 3: storage in TB.
    ("Refurbished (Excellent) - HP EliteDesk 800 G3 SFF SFF Desktop (Intel i5-7500 "
     "/ 8 GB RAM / 2 TB HDD / Windows 10 Pro)",
     "hp-elitedesk-800-g3-sff", 8, 2000),
    # Titled "Workstation", and a Tiny: the description gives 7" x 7.2" x 1.4",
    # which is PSREF's P340 Tiny. A title-keyword exclusion on "workstation"
    # would have hidden this machine -- the reason out_of_scope.yaml is a list
    # of URLs rather than a rule.
    ("Refurbished (Excellent) - Lenovo ThinkStation P340 Workstation - Raven Black "
     "(Intel i3-10300T / 16 GB RAM / 256 GB SSD / Quadro P620 / Windows 11 Pro)",
     "lenovo-p340-tiny", 16, 256),
]


@pytest.mark.parametrize("title, expected_key, ram_gb, storage_gb", ITREFURBS_CASES)
def test_itrefurbs_listings_parse(title, expected_key, ram_gb, storage_gb,
                                  chassis, cpus, aliases):
    result = specs.parse(title, "", chassis, cpus, aliases)
    assert result["parse_ok"] is True, result["parse_notes"]
    assert result["chassis_key"] == expected_key
    assert (result["ram_gb"], result["storage_gb"]) == (ram_gb, storage_gb)


@pytest.mark.parametrize("title, expected", [
    ("HP EliteDesk 800 G3 SFF (Intel i5-7500 / 8 GB RAM / 2 TB HDD)", (2000, "hdd")),
    ("Asus V500 (i7-13620H / 16 Gb RAM / 1 TB SSD)", (1000, "ssd")),
    ("MXG Astra (Ryzen 7 9800x3D / 32 GB RAM / 2 TB NVMe)", (2000, "nvme")),
])
def test_parse_storage_reads_terabytes(title, expected):
    assert specs.parse_storage(title) == expected


def test_ram_is_not_the_terabyte_disk(chassis, cpus, aliases):
    result = specs.parse("HP EliteDesk 800 G3 SFF (Intel i5-7500 / 8 GB RAM / "
                         "2 TB HDD)", "", chassis, cpus, aliases)
    assert result["ram_gb"] == 8


# ── Storage interface from the description ───────────────────────────────────
# ITRefurbs' titles say "256 GB SSD"; their Key Features block says "256GB NVMe
# SSD". The description is trusted for the interface only when it names the SAME
# capacity the title does. That is what separates a statement about this drive
# from boilerplate about the product line ("supports up to 2TB NVMe").

ITREFURBS_800G4_BODY = (
    "<p>Storage: 256 GB SSD</p><h3>Key Features</h3><p>Memory: 16GB DDR4 RAM "
    "Storage: 256GB NVMe SSD Graphics: Intel UHD Graphics 630</p>")


def test_nvme_read_from_description_at_the_same_capacity(chassis, cpus, aliases):
    title = ("Refurbished (Excellent) - HP EliteDesk 800 G4 Desktop Mini (Intel "
             "i5-8500 / 16 GB RAM / 256 GB SSD / Windows 11 Pro)")
    result = specs.parse(title, ITREFURBS_800G4_BODY, chassis, cpus, aliases)
    assert result["storage_type"] == "nvme"
    assert specs.NVME_FROM_DESCRIPTION in result["parse_notes"]


@pytest.mark.parametrize("body", [
    "Supports up to 2TB NVMe SSD in the M.2 slot",       # a different capacity
    "M.2 PCIe NVMe slot for fast storage",               # no capacity at all
    "Storage: 256 GB SSD",                               # no interface stated
])
def test_nvme_in_description_at_another_capacity_is_ignored(body, chassis, cpus,
                                                            aliases):
    title = "HP EliteDesk 800 G4 Mini (Intel i5-8500 / 16 GB RAM / 256 GB SSD)"
    result = specs.parse(title, body, chassis, cpus, aliases)
    assert result["storage_type"] == "ssd"


def test_description_never_downgrades_a_title_that_says_nvme(chassis, cpus, aliases):
    title = "HP ProDesk 600 G6 Mini | i5-10500T | 16GB | 512GB NVMe SSD"
    result = specs.parse(title, "Storage: 512 GB SSD", chassis, cpus, aliases)
    assert result["storage_type"] == "nvme"


def test_storage_falls_back_to_description_with_a_note(chassis, cpus, aliases):
    title = "HP EliteDesk 800 G4 Mini Desktop i5-8500 16GB Windows 11 Pro"
    result = specs.parse(title, "<p>Storage: 256GB SSD</p>", chassis, cpus, aliases)
    assert result["storage_gb"] == 256
    assert result["parse_ok"] is True
    assert "storage read from description" in result["parse_notes"]


def test_lenovo_thinkstation_model_number():
    """ThinkStation numbers are P-prefixed. "\b340" cannot match inside "P340",
    so without the prefix the model regex finds nothing at all."""
    assert specs._model_number("Lenovo ThinkStation P340 Workstation (Intel "
                               "i3-10300T / 16 GB RAM / 256 GB SSD)") == "p340"


# ── Form-factor guard (eBay) ─────────────────────────────────────────────────
# The alias table maps a model number to one chassis, which held for the
# refurbishers and does not hold on eBay: one search for "OptiPlex 3080 Micro"
# (2026-09-30 capture) returned seven 3080 SFFs and a 3080 Tower among the
# Micros. The guard only ever REJECTS -- a form-factor word never resolves a
# chassis, because the refurbishers' copy is boilerplate (chassis_aliases.yaml).
# Titles verbatim from that capture unless noted.

FORM_FACTOR_CONFLICTS = [
    ("Dell OptiPlex 3080 SFF Core i5-10500 3.10 GHz 8 GB DDR4 256 GB NVMe Windows 11",
     "SFF"),
    ("Dell Optiplex 3080 SFF Desktop i5-10500 3.10GHz 32GB 512GB SSD Windows 11 Pro",
     "SFF"),
    ("Dell OptiPlex 3080 Tower Core i5-10500 3.10 GHz 8 GB DDR4 256 GB NVMe Windows 11",
     "Tower"),
    # Constructed: the reverse direction, a small-chassis word on an SFF alias.
    ("HP ProDesk 600 G3 Mini i5-6500T 8GB 256GB SSD", None),
    ("HP ProDesk 600 G3 USFF i5-6500T 8GB 256GB SSD", "USFF"),
    ("HP EliteDesk 800 G3 Micro i5-6500T 8GB 256GB SSD", "Micro"),
    ("Lenovo ThinkCentre M70s Small Form Factor i5-10400 16GB 256GB SSD", None),
]


@pytest.mark.parametrize("title, word", FORM_FACTOR_CONFLICTS)
def test_form_factor_word_contradicting_the_alias_is_held_out(
        title, word, chassis, cpus, aliases):
    """A 3080 SFF priced as a Micro is the 5050 hazard again: two UDIMM slots
    costed as SODIMMs, or a tower ranked as a mini. Held out, never resolved.

    "Mini" on an SFF alias is NOT a conflict (case 4): "Mini PC" is generic
    marketing on eBay, and rejecting on it would hide real SFFs. "Small Form
    Factor" on an SFF chassis (last case) agrees with it."""
    assert specs.form_factor_conflict(title, specs.chassis_key(title, aliases)) == word
    if word:
        parsed = specs.parse(title, "", chassis, cpus, aliases)
        assert parsed["parse_ok"] is False
        assert "form factor" in parsed["parse_notes"]


@pytest.mark.parametrize("title", [
    # eBay, verbatim: every small-form-factor spelling in the capture.
    "Dell Optiplex 3080 Micro PC i5-10500T 16GB DDR4 256GB m2 SSD WIN11 w/AC Adapter",
    "Dell Optiplex MFF 3080 i5-10500T 2.30Ghz 16 GB Ram 256GB NVMe Window 11 Pro WIFI",
    "Dell OptiPlex 3080 i5-10500T 256GB 8GB Black Micro Desktop Win 11 Computer PC",
    "Mini PC Dell OptiPlex 3080 Micro NVMe SSD i3 i5 i7 16/32GB RAM WiFi Win 11 Pro",
    # The refurbishers' boilerplate must keep resolving: "Ultra" and "Tiny Mini".
    "Dell Optiflex 3080 Ultra i5-10500T 16GB 256 SSD Windows 11 Pro Mini Computer Pc Refurbished",
    "Lenovo M73 Core i5-4570T 8GB 120 SSD Windows 10 Pro Tiny Mini Desktop Pc Refurbished",
    "HP ProDesk 600 G3 SFF Desktop core i5-6500T 16GB 120 SSD Windows 11 Pro Refurbished",
    "Refurbished (Excellent) - HP EliteDesk 800 G3 SFF SFF Desktop (Intel i5-7500 / 8 GB RAM / 2 TB HDD / Windows 10 Pro)",
    # No form-factor word at all resolves as before.
    "Refurbished (Excellent) - HP EliteDesk 800 G4 Desktop (Intel i5-8500 / 8 GB RAM / 256 GB SSD / Windows 11 Pro)",
])
def test_consistent_or_absent_form_factor_is_not_a_conflict(title, aliases):
    assert specs.form_factor_conflict(title, specs.chassis_key(title, aliases)) is None


def test_mini_tower_is_a_tower_not_a_mini():
    """"Mini Tower" contains "mini", and reading it as a small chassis would wave
    a tower through on a Micro alias."""
    assert specs.form_factor_conflict("Dell OptiPlex 3080 Mini Tower i5-10500",
                                      "dell-optiplex-3080-micro") == "Mini Tower"
