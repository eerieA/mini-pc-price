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
