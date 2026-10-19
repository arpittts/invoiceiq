from datetime import date
from decimal import Decimal

import pytest

from app.schemas.extraction import (
    ExtractionOutput,
    parse_amount,
    parse_date,
    parse_rate,
    to_invoice_extraction,
)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("1234.50", Decimal("1234.50")),
        ("₹1,23,456.00", Decimal("123456.00")),  # Indian lakh grouping
        ("Rs. 1,200", Decimal("1200")),
        ("INR 99.9", Decimal("99.9")),
        ("1,200/-", Decimal("1200")),
        ("(500.00)", Decimal("-500.00")),
        ("  ", None),
        (None, None),
    ],
)
def test_parse_amount(printed, expected):
    assert parse_amount(printed) == expected


@pytest.mark.parametrize("printed", ["twelve", "1.2.3", "NaN", "Infinity", "9,99,99,99,99,99,999"])
def test_parse_amount_rejects_garbage(printed):
    with pytest.raises(ValueError):
        parse_amount(printed)


def test_parse_rate():
    assert parse_rate("18%") == Decimal("18")
    assert parse_rate("12.00 %") == Decimal("12")
    assert parse_rate(None) is None
    with pytest.raises(ValueError):
        parse_rate("118%")


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("03/04/2026", date(2026, 4, 3)),  # day first, as on Indian invoices
        ("03-04-2026", date(2026, 4, 3)),
        ("03.04.26", date(2026, 4, 3)),
        ("next Tuesday", None),
        ("2026-04-03", date(2026, 4, 3)),
        ("3 Apr 2026", date(2026, 4, 3)),
        ("3rd April 2026", date(2026, 4, 3)),
        ("03-Apr-2026", date(2026, 4, 3)),
        ("April 3, 2026", date(2026, 4, 3)),
        ("03 Apr 26", date(2026, 4, 3)),  # 2-digit year after a month name
        ("03-Apr-26", date(2026, 4, 3)),
    ],
)
def test_parse_date(printed, expected):
    if expected is None:
        with pytest.raises(ValueError):
            parse_date(printed)
    else:
        assert parse_date(printed) == expected


def printed(value, confidence=0.95):
    return {"value": value, "confidence": confidence}


def make_output(**overrides) -> ExtractionOutput:
    line = {
        "description": printed("Brake pad set"),
        "hsn_sac_code": printed("8708 30"),
        "quantity": printed("2"),
        "unit": printed("Nos"),
        "unit_price": printed("1,500.00"),
        "discount": printed(None),
        "taxable_value": printed("3,000.00"),
        "gst_rate": printed("28%"),
        "cgst_amount": printed("420.00"),
        "sgst_amount": printed("420.00"),
        "igst_amount": printed(None),
        "line_total": printed("3,840.00"),
    }
    data = {
        "vendor_name": printed("  Sharma   Auto Parts "),
        "vendor_gstin": printed("27aapfu 0939f1zv"),
        "buyer_name": printed("Mehta Motors"),
        "buyer_gstin": printed(None, 0.9),
        "invoice_number": printed("SAP/2026/0042"),
        "invoice_date": printed("12/10/2026"),
        "place_of_supply": printed("Maharashtra (27)"),
        "line_items": [line],
        "subtotal": printed("3,000.00"),
        "cgst": printed("420.00"),
        "sgst": printed("420.00"),
        "igst": printed(None),
        "total": printed("₹3,840.00", 0.6),
    }
    data.update(overrides)
    return ExtractionOutput.model_validate(data)


def test_to_invoice_extraction_converts_and_cleans_values():
    result = to_invoice_extraction(make_output())

    assert result.vendor_name.value == "Sharma Auto Parts"  # whitespace squeezed
    assert result.vendor_gstin.value == "27AAPFU0939F1ZV"  # spaces removed, uppercased
    assert result.invoice_date.value == date(2026, 10, 12)
    assert result.total.value == Decimal("3840.00")
    assert result.igst.value is None  # missing stays missing, never invented
    item = result.line_items[0]
    assert item.hsn_sac_code.value == "870830"
    assert item.gst_rate.value == Decimal("28")
    assert item.taxable_value.value == Decimal("3000.00")


def test_overall_confidence_is_the_weakest_field():
    assert to_invoice_extraction(make_output()).overall_confidence == 0.6


def test_bad_value_error_names_the_field():
    with pytest.raises(ValueError, match="invoice_date"):
        to_invoice_extraction(make_output(invoice_date=printed("someday")))

    bad_line = make_output().model_dump()["line_items"][0]
    bad_line["quantity"] = printed("two")
    with pytest.raises(ValueError, match="line item 1: quantity"):
        to_invoice_extraction(make_output(line_items=[bad_line]))


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(ValueError, match="confidence"):
        to_invoice_extraction(make_output(total=printed("100", 1.7)))
