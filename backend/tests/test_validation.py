"""Unit tests for every validation rule, using clean invoices and injected errors."""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.validation import InvoiceData, LineData, validate

TODAY = date(2026, 10, 18)
D = Decimal
STARTER = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "starter"


def intra_state() -> InvoiceData:
    """A correct Maharashtra -> Maharashtra invoice (CGST + SGST), two lines."""
    return InvoiceData(
        vendor_gstin="27AAPFU0939F1ZV",
        buyer_gstin=None,
        invoice_number="SAP/2026/0042",
        invoice_date=date(2026, 10, 12),
        place_of_supply="Maharashtra (27)",
        subtotal=D("3950.00"),
        cgst=D("355.50"),
        sgst=D("355.50"),
        igst=None,
        total=D("4661.00"),
        lines=[
            LineData(
                D("2"),
                D("1500.00"),
                None,
                D("3000.00"),
                D("18"),
                D("270.00"),
                D("270.00"),
                None,
                D("3540.00"),
            ),
            LineData(
                D("10"),
                D("100.00"),
                D("50.00"),
                D("950.00"),
                D("18"),
                D("85.50"),
                D("85.50"),
                None,
                D("1121.00"),
            ),
        ],
    )


def inter_state() -> InvoiceData:
    """A correct Maharashtra -> Karnataka invoice (IGST)."""
    return InvoiceData(
        vendor_gstin="27AAPFU0939F1ZV",
        buyer_gstin=None,
        invoice_number="INV-1",
        invoice_date=date(2026, 9, 1),
        place_of_supply="Karnataka",
        subtotal=D("1000.00"),
        cgst=None,
        sgst=None,
        igst=D("180.00"),
        total=D("1180.00"),
        lines=[
            LineData(
                D("4"),
                D("250.00"),
                None,
                D("1000.00"),
                D("18"),
                None,
                None,
                D("180.00"),
                D("1180.00"),
            )
        ],
    )


def codes(inv: InvoiceData, **kwargs) -> set[str]:
    return {issue.rule_code for issue in validate(inv, today=TODAY, max_age_days=730, **kwargs)}


def with_line(inv: InvoiceData, index: int, **changes) -> InvoiceData:
    lines = list(inv.lines)
    lines[index] = replace(lines[index], **changes)
    return replace(inv, lines=lines)


# --- clean invoices pass ---------------------------------------------------


def test_clean_invoices_have_no_issues():
    assert codes(intra_state()) == set()
    assert codes(inter_state()) == set()


@pytest.mark.parametrize("truth_file", sorted(STARTER.glob("inv_*.json")), ids=lambda p: p.stem)
def test_every_starter_ground_truth_passes(truth_file):
    """No false alarms on the 20 correct synthetic invoices (several layouts, round-off)."""
    inv = InvoiceData.from_dict(json.loads(truth_file.read_text()))
    issues = validate(inv, today=TODAY, max_age_days=730)
    assert [i for i in issues if i.severity == "error"] == []


# --- REQUIRED --------------------------------------------------------------


@pytest.mark.parametrize("field", ["vendor_gstin", "invoice_number", "invoice_date", "total"])
def test_required_fields(field):
    assert "REQUIRED" in codes(replace(intra_state(), **{field: None}))


def test_buyer_gstin_is_optional():
    assert codes(replace(intra_state(), buyer_gstin=None)) == set()


# --- GSTIN_FORMAT and STATE_CODE ---------------------------------------------


def test_misread_gstin_character_fails_checksum():
    assert "GSTIN_FORMAT" in codes(replace(intra_state(), vendor_gstin="27AAPFU0938F1ZV"))


def test_malformed_buyer_gstin():
    assert "GSTIN_FORMAT" in codes(replace(intra_state(), buyer_gstin="27AAPFU0939F1Z"))


def test_unknown_state_code():
    assert "STATE_CODE" in codes(replace(inter_state(), vendor_gstin="45AAPFU0939F1ZV"))


# --- TAX_TYPE ----------------------------------------------------------------


def test_igst_on_an_intra_state_supply():
    bad = replace(inter_state(), place_of_supply="Maharashtra")
    assert "TAX_TYPE" in codes(bad)


def test_cgst_on_an_inter_state_supply():
    bad = replace(intra_state(), place_of_supply="Karnataka (29)")
    assert "TAX_TYPE" in codes(bad)


def test_place_of_supply_falls_back_to_buyer_state():
    # No place of supply printed: the buyer's GSTIN says Karnataka, so CGST is wrong.
    bad = replace(intra_state(), place_of_supply=None, buyer_gstin="29AAPFU0939F1ZT")
    assert codes(bad) >= {"TAX_TYPE"}


def test_unreadable_place_of_supply_is_a_warning():
    inv = replace(intra_state(), place_of_supply="Atlantis")
    issues = validate(inv, today=TODAY, max_age_days=730)
    assert [(i.rule_code, i.severity) for i in issues] == [("TAX_TYPE", "warning")]


# --- LINE_MATH ---------------------------------------------------------------


def test_wrong_taxable_value():
    bad = with_line(intra_state(), 0, taxable_value=D("3100.00"))
    issues = validate(bad, today=TODAY, max_age_days=730)
    line_math = [i for i in issues if i.rule_code == "LINE_MATH"]
    assert line_math[0].field == "line_items[1].taxable_value"
    assert "₹3,000.00" in line_math[0].message


def test_discount_is_taken_into_account():
    assert "LINE_MATH" not in codes(intra_state())  # line 2 has a ₹50 discount
    assert "LINE_MATH" in codes(with_line(intra_state(), 1, discount=None))


def test_rounding_within_one_rupee_is_accepted():
    assert "LINE_MATH" not in codes(with_line(intra_state(), 0, taxable_value=D("3000.80")))


# --- TAX_MATH ----------------------------------------------------------------


def test_wrong_line_rate():
    assert "TAX_MATH" in codes(with_line(intra_state(), 0, gst_rate=D("12")))


def test_cgst_and_sgst_must_match():
    assert "TAX_MATH" in codes(with_line(intra_state(), 0, sgst_amount=D("280.00")))


def test_invoice_tax_must_equal_line_taxes():
    assert "TAX_MATH" in codes(replace(intra_state(), cgst=D("365.50"), sgst=D("365.50")))


def test_invoice_level_tax_when_lines_show_no_tax():
    """Some layouts print only taxable value and rate per line."""
    clean = inter_state()
    no_line_tax = with_line(clean, 0, igst_amount=None, line_total=None)
    assert codes(no_line_tax) == set()
    assert "TAX_MATH" in codes(replace(no_line_tax, igst=D("120.00"), total=D("1120.00")))


# --- TOTAL_MATH --------------------------------------------------------------


def test_wrong_total():
    assert "TOTAL_MATH" in codes(replace(intra_state(), total=D("4761.00")))


def test_wrong_subtotal():
    assert "TOTAL_MATH" in codes(replace(intra_state(), subtotal=D("3590.00")))


def test_round_off_within_a_rupee_is_accepted():
    assert codes(replace(intra_state(), total=D("4661.49"))) == set()


def test_total_without_line_totals_uses_subtotal_plus_tax():
    inv = with_line(inter_state(), 0, line_total=None)
    assert codes(inv) == {"LINE_TOTAL_MISSING"}  # a warning only: the math still checks out
    assert "TOTAL_MATH" in codes(replace(inv, total=D("1280.00")))


# --- DATE_RANGE --------------------------------------------------------------


def test_future_date_is_an_error():
    issues = validate(
        replace(intra_state(), invoice_date=date(2026, 12, 1)), today=TODAY, max_age_days=730
    )
    assert [(i.rule_code, i.severity) for i in issues] == [("DATE_RANGE", "error")]


def test_old_date_is_a_warning():
    issues = validate(
        replace(intra_state(), invoice_date=date(2023, 1, 1)), today=TODAY, max_age_days=730
    )
    assert [(i.rule_code, i.severity) for i in issues] == [("DATE_RANGE", "warning")]


# --- DUPLICATE ---------------------------------------------------------------


def test_duplicates_reported_with_the_earlier_invoice():
    issues = validate(
        intra_state(), today=TODAY, max_age_days=730, same_file_ids=[7], same_number_ids=[7]
    )
    assert [(i.rule_code, i.field) for i in issues] == [
        ("DUPLICATE", "file"),
        ("DUPLICATE", "invoice_number"),
    ]
    assert "invoice 7" in issues[0].message


# --- missing values never crash a rule ----------------------------------------


def test_mostly_empty_invoice_only_reports_required_fields():
    assert codes(InvoiceData(lines=[LineData()])) == {"REQUIRED"}


# --- LINE_TOTAL_MISSING (v1.1) -------------------------------------------------


def test_tax_amounts_without_a_line_total_is_a_warning():
    inv = with_line(intra_state(), 0, line_total=None)
    issues = validate(inv, today=TODAY, max_age_days=730)
    assert [(i.rule_code, i.severity, i.field) for i in issues] == [
        ("LINE_TOTAL_MISSING", "warning", "line_items[1].line_total")
    ]


def test_a_layout_without_line_tax_needs_no_line_total():
    inv = with_line(
        intra_state(), 0, cgst_amount=None, sgst_amount=None, igst_amount=None, line_total=None
    )
    assert "LINE_TOTAL_MISSING" not in codes(inv)


# --- HSN_MISMATCH and RATE_MISMATCH (Phase 5) ----------------------------------


def test_printed_code_from_another_heading_is_a_warning():
    inv = with_line(intra_state(), 0, hsn_sac_code="8708 30", suggested_hsn_code="87141090")
    issues = validate(inv, today=TODAY, max_age_days=730)
    assert [(i.rule_code, i.severity, i.field) for i in issues] == [
        ("HSN_MISMATCH", "warning", "line_items[1].hsn_sac_code")
    ]


def test_same_heading_is_not_a_mismatch():
    inv = with_line(intra_state(), 0, hsn_sac_code="8708", suggested_hsn_code="87083000")
    assert codes(inv) == set()


def test_rate_different_from_the_default_is_a_warning():
    inv = with_line(intra_state(), 0, suggested_hsn_code="8714", suggested_gst_rate=D("5"))
    issues = validate(inv, today=TODAY, max_age_days=730)
    assert [(i.rule_code, i.severity) for i in issues] == [("RATE_MISMATCH", "warning")]
    assert "verify" in issues[0].message


def test_rate_mismatch_message_prints_plain_percentages():
    inv = with_line(
        inter_state(),
        0,
        gst_rate=D("40.00"),
        suggested_gst_rate=D("18.00"),
        suggested_hsn_code="2202",
    )
    [issue] = [
        i for i in validate(inv, today=TODAY, max_age_days=730) if i.rule_code == "RATE_MISMATCH"
    ]
    assert "rate 40% differs from the default 18%" in issue.message
