"""Tests for scripts/ingest_hsn.py's parsing (the PDF and Excel themselves aren't in git)."""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_hsn.py"
spec = importlib.util.spec_from_file_location("ingest_hsn", SCRIPT)
ingest = importlib.util.module_from_spec(spec)
sys.modules["ingest_hsn"] = ingest
spec.loader.exec_module(ingest)


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("2202 99 21, 2202 99 29", ["22029921", "22029929"]),
        ("0802, 0813", ["0802", "0813"]),
        ("27 01", ["2701"]),  # code split across table columns
        ("3901 to 3903", ["3901", "3902", "3903"]),
        ("9603 [other than 9603 10 00]", ["9603"]),  # exclusions are ignored
        ("Any Chapter", []),
        ("8516 or 8517", ["8516", "8517"]),
        # A bare pair after a tariff item is that item's ending, not a chapter
        ("2906 11 10, 30, 3301", ["29061110", "29061130", "3301"]),
        ("2403 (other than 2403 19 21, 2403 19 29)", ["2403"]),
    ],
)
def test_parse_codes(text, codes):
    assert ingest.parse_codes(text) == codes


def test_schedule_heading_gives_total_gst():
    assert ingest.gst_from_heading("Schedule II – 9 %") == ("II", Decimal("18"))
    assert ingest.gst_from_heading("Schedule III– 20%") == ("III", Decimal("40"))
    assert ingest.gst_from_heading("Schedule V – 0.125%") == ("V", Decimal("0.250"))
    assert ingest.gst_from_heading("Explanation. For the purposes") is None


def entry(schedule, serial, codes, rate, description="x"):
    return ingest.RateEntry(
        schedule, serial, codes, description, Decimal(rate), ingest.parse_codes(codes)
    )


def index(*entries):
    by_prefix = {}
    for e in entries:
        for code in e.codes:
            by_prefix.setdefault(code, []).append(e)
    return by_prefix


def test_most_specific_entry_wins():
    rates = index(
        entry("II", 1, "2202", "18", "Waters"), entry("III", 2, "2202 10 10", "40", "Aerated")
    )
    assert ingest.assign_rate("22021010", rates)[0] == Decimal("40")
    assert ingest.assign_rate("22029990", rates)[0] == Decimal("18")  # falls back to the heading


def test_conflicting_entries_give_no_single_rate():
    rates = index(entry("I", 1, "6109", "5", "of value up to 2500"), entry("II", 2, "6109", "18"))
    rate, note = ingest.assign_rate("61091000", rates)
    assert rate is None
    assert "depends on the description (5% or 18%)" in note


def test_code_without_entry():
    rate, note = ingest.assign_rate("01011010", index(entry("I", 1, "0401", "5")))
    assert rate is None and "No rate entry" in note


def test_rates_print_without_scientific_notation():
    assert ingest.pct(Decimal("40.00")) == "40"
    assert ingest.pct(Decimal("0.250")) == "0.25"


def test_amendment_01_2026_is_applied_by_schedule_and_serial():
    assert ingest.AMENDMENT_01_2026[("III", "2")] == "2202 91 00, 2202 99 91, 2202 99 99"


def test_a_chapter_wide_entry_never_gives_a_single_rate():
    """Sch I S.No 19 is listed under Chapter 07 but means only dried herbs ('jaribooti');
    it once gave every fresh vegetable 5%."""
    rates = index(entry("I", "19", "07", "5", "Herb, bark, dry plant, dry root"))
    rate, note = ingest.assign_rate("07020000", rates)
    assert rate is None and "chapter-wide" in note


def test_an_exemption_gives_nil_and_cites_10_2025():
    rates = index(
        entry("I", "19", "07", "5", "Herb, bark, dry plant"),
        entry("Nil", "28", "0702", "0", "Tomatoes, fresh or chilled"),
    )
    rate, note = ingest.assign_rate("07020000", rates)
    assert rate == Decimal(0) and "Notification 10/2025 S.No 28 (nil rated)" in note


def test_a_conditional_exemption_leaves_the_rate_to_a_person():
    """Rice is nil when loose, 5% when pre-packaged and labelled: no single rate."""
    rates = index(
        entry("I", "48", "1006", "5", "Rice, pre-packaged and labelled"),
        entry("Nil", "60", "1006", "0", "Rice, other than pre-packaged and labelled"),
    )
    rate, note = ingest.assign_rate("10063020", rates)
    assert rate is None and "0% or 5%" in note


def test_amendment_19_2025_moves_tobacco_to_40_and_removes_the_28_schedule():
    inserted = {
        (schedule, serial): Decimal(central) * 2
        for schedule, serial, _, _, central in ingest.AMENDMENT_19_2025_INSERT
    }
    assert inserted[("III", "14")] == Decimal(40)  # pan masala
    assert inserted[("II", "4A")] == Decimal(18)  # biris
    assert ingest.AMENDMENT_19_2025_OMIT == {"VII"}
    rates = index(
        *(
            entry(schedule, serial, codes, Decimal(central) * 2, description)
            for schedule, serial, codes, description, central in ingest.AMENDMENT_19_2025_INSERT
        )
    )
    assert ingest.assign_rate("21069020", rates)[0] == Decimal(40)
    assert ingest.assign_rate("24031921", rates)[0] == Decimal(18)  # biris, more specific
    assert ingest.assign_rate("24039910", rates)[0] == Decimal(40)  # other tobacco


def test_services_get_rates_only_where_15_2025_restates_the_heading():
    services = {}
    for e in ingest.service_entries():
        services.setdefault(e.codes[0], []).append(e)
    assert ingest.assign_rate("996812", services)[0] == Decimal(18)  # courier
    beauty_rate, beauty_note = ingest.assign_rate("999721", services)
    assert beauty_rate == Decimal(5) and "input tax credit" in beauty_note
    assert ingest.assign_rate("999711", services)[0] == Decimal(18)  # washing, cleaning
    assert ingest.assign_rate("998819", services)[0] is None  # job work: depends on the goods
    assert ingest.assign_rate("998719", services)[0] is None  # repairs: not loaded
