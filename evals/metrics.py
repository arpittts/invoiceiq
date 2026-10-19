"""Scoring helpers shared by the harness and the older check scripts."""

from decimal import Decimal

HEADER_FIELDS = [
    "vendor_name", "vendor_gstin", "buyer_name", "buyer_gstin", "invoice_number",
    "invoice_date", "place_of_supply", "subtotal", "cgst", "sgst", "igst", "total",
]  # fmt: skip
LINE_FIELDS = [
    "description", "hsn_sac_code", "quantity", "unit", "unit_price", "discount",
    "taxable_value", "gst_rate", "cgst_amount", "sgst_amount", "igst_amount", "line_total",
]  # fmt: skip


def same(expected, actual) -> bool:
    """Compare a ground-truth value (string/None) with an extracted typed value."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(actual, Decimal):
        return Decimal(expected) == actual
    if hasattr(actual, "isoformat"):
        return expected == actual.isoformat()
    return " ".join(str(expected).split()) == " ".join(str(actual).split())


def field_results(truth: dict, extraction) -> list[tuple[str, bool]]:
    """(field name, correct?) for every field. Line fields are named 'line.<name>'.

    A missing or extra line item counts all of its fields as wrong; with no
    extraction at all (the read failed), every field is wrong.
    """
    results = []
    for name in HEADER_FIELDS:
        ok = extraction is not None and same(truth[name], getattr(extraction, name).value)
        results.append((name, ok))
    true_lines = truth["line_items"]
    got_lines = extraction.line_items if extraction is not None else []
    for index in range(max(len(true_lines), len(got_lines))):
        for name in LINE_FIELDS:
            ok = (
                index < len(true_lines)
                and index < len(got_lines)
                and same(true_lines[index][name], getattr(got_lines[index], name).value)
            )
            results.append((f"line.{name}", ok))
    return results


def score(truth: dict, extraction) -> tuple[int, int]:
    """(correct fields, total fields)."""
    results = field_results(truth, extraction)
    return sum(ok for _, ok in results), len(results)


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def rate(hits: int, total: int) -> float | None:
    return hits / total if total else None
