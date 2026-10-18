"""Tests for scripts/make_synthetic_invoices.py: the ground truth must be exactly right."""

import importlib.util
import random
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_synthetic_invoices.py"
spec = importlib.util.spec_from_file_location("make_synthetic_invoices", SCRIPT)
gen = importlib.util.module_from_spec(spec)
sys.modules["make_synthetic_invoices"] = gen
spec.loader.exec_module(gen)


def test_gstin_checksum_matches_a_known_valid_gstin():
    # 27AAPFU0939F1ZV is the sample GSTIN used in official GST documentation.
    assert gen.gstin_checksum("27AAPFU0939F1Z") == "V"


def test_generated_gstins_have_valid_format_and_state():
    rng = random.Random(1)
    for _ in range(50):
        gstin = gen.make_gstin("29", rng)
        assert len(gstin) == 15 and gstin.startswith("29") and gstin[13] == "Z"
        assert gen.gstin_checksum(gstin[:14]) == gstin[14]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("5", "5.00"), ("999.5", "999.50"), ("1234", "1,234.00"),
     ("123456.78", "1,23,456.78"), ("12345678", "1,23,45,678.00")],
)  # fmt: skip
def test_indian_grouping(value, expected):
    assert gen.indian_grouping(Decimal(value)) == expected


@pytest.fixture(scope="module")
def invoices():
    rng = random.Random(42)
    return [gen.build_invoice(i, rng) for i in range(40)]


def test_line_and_invoice_math_is_consistent(invoices):
    for inv in invoices:
        for line in inv["lines"]:
            assert line.taxable == gen.money(line.quantity * line.unit_price) - line.discount
            taxes = (line.cgst or 0) + (line.sgst or 0) + (line.igst or 0)
            assert line.total == line.taxable + taxes
            if inv["intra_state"]:
                assert line.cgst == line.sgst and line.igst is None
            else:
                assert line.cgst is None and line.igst is not None
        assert inv["subtotal"] == sum(line.taxable for line in inv["lines"])
        taxes = (inv["cgst"] or 0) + (inv["sgst"] or 0) + (inv["igst"] or 0)
        assert abs(inv["total"] - (inv["subtotal"] + taxes)) < 1  # round-off is under ₹1


def test_ground_truth_never_contains_values_the_layout_does_not_print(invoices):
    for inv in invoices:
        truth = gen.ground_truth(inv)
        layout = gen.LAYOUTS[inv["layout"]]
        for item in truth["line_items"]:
            if not layout["line_tax"]:
                assert item["cgst_amount"] is item["igst_amount"] is item["line_total"] is None
            if not layout["discount"]:
                assert item["discount"] is None


def test_every_printed_value_appears_in_the_pdf(invoices):
    for inv in invoices[:8]:  # two of each layout
        raw = pymupdf.open(stream=gen.render_pdf(inv), filetype="pdf")[0].get_text()
        # NFKC expands typographic ligatures (e.g. "ﬃ" in "Office") back to plain letters.
        text = " ".join(unicodedata.normalize("NFKC", raw).split())
        truth = gen.ground_truth(inv)
        for key in ("vendor_name", "vendor_gstin", "invoice_number", "place_of_supply"):
            assert truth[key] in text, (inv["id"], key)
        if truth["buyer_gstin"]:
            assert truth["buyer_gstin"] in text
        for item in truth["line_items"]:
            assert item["hsn_sac_code"] in text


def test_same_seed_gives_identical_invoices():
    first = gen.ground_truth(gen.build_invoice(0, random.Random(7)))
    second = gen.ground_truth(gen.build_invoice(0, random.Random(7)))
    assert first == second
