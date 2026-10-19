"""Inject realistic misreads into correct invoices; check the rules catch them.

Each injection mimics a mistake a vision model makes (a swapped digit, a skipped
line, a wrong tax column). No AI involved: free, fast and deterministic (seeded).
"""

import copy
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.validation import InvoiceData, validate  # noqa: E402

TODAY = date(2026, 10, 18)
MAX_AGE_DAYS = 730
DIGITS, LETTERS = "0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def swap_adjacent_digits(text: str, rng: random.Random) -> str | None:
    """'1,56,808.98' -> '1,65,808.98': a classic misread. None if no swap changes it."""
    chars = list(text)
    spots = [
        i
        for i in range(len(chars) - 1)
        if chars[i].isdigit() and chars[i + 1].isdigit() and chars[i] != chars[i + 1]
    ]
    if not spots:
        return None
    i = rng.choice(spots)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


# Each injection: (name, expected rule, function(truth, rng) -> changed truth, or None if N/A)
def misread_gstin_char(t, rng):
    gstin = t["vendor_gstin"]
    i = rng.randrange(2, 14)  # keep the state code; the checksum covers positions 0-13
    pool = DIGITS if gstin[i].isdigit() else LETTERS  # same character class, so format passes
    t["vendor_gstin"] = gstin[:i] + rng.choice(pool.replace(gstin[i], "")) + gstin[i + 1 :]
    return t


def invalid_state_code(t, rng):
    t["vendor_gstin"] = rng.choice(["41", "45", "50", "63", "88"]) + t["vendor_gstin"][2:]
    return t


def transposed_taxable_value(t, rng):
    line = rng.choice(t["line_items"])
    swapped = swap_adjacent_digits(line["taxable_value"], rng)
    if swapped is None:
        return None
    line["taxable_value"] = swapped
    return t


def misread_quantity(t, rng):
    line = rng.choice(t["line_items"])
    line["quantity"] = str(Decimal(line["quantity"]) + rng.choice([1, 2, 10]))
    return t


def misread_total_digit(t, rng):
    t["total"] = str(Decimal(t["total"]) + rng.choice([1, -1]) * rng.choice([9, 10, 90, 100]))
    return t


def wrong_gst_rate(t, rng):
    line = rng.choice(t["line_items"])
    other = {"0": "5", "5": "18", "18": "5", "40": "18"}
    line["gst_rate"] = other.get(line["gst_rate"], "12")
    return t


def swapped_tax_type(t, rng):
    """IGST written as CGST + SGST (or the reverse) while the states stay the same."""
    if t["igst"] is not None:
        half = str((Decimal(t["igst"]) / 2).quantize(Decimal("0.01")))
        t["cgst"], t["sgst"], t["igst"] = half, half, None
        for line in t["line_items"]:
            if line["igst_amount"] is not None:
                half = str((Decimal(line["igst_amount"]) / 2).quantize(Decimal("0.01")))
                line["cgst_amount"], line["sgst_amount"], line["igst_amount"] = half, half, None
    else:
        t["igst"] = str(Decimal(t["cgst"]) + Decimal(t["sgst"]))
        t["cgst"] = t["sgst"] = None
        for line in t["line_items"]:
            if line["cgst_amount"] is not None:
                line["igst_amount"] = str(
                    Decimal(line["cgst_amount"]) + Decimal(line["sgst_amount"])
                )
                line["cgst_amount"] = line["sgst_amount"] = None
    return t


def cgst_sgst_mismatch(t, rng):
    if t["sgst"] is None:
        return None  # only intra-state invoices have both
    t["sgst"] = str(Decimal(t["sgst"]) + rng.choice([10, 50, 100]))
    return t


def future_date(t, rng):
    t["invoice_date"] = (TODAY + timedelta(days=rng.randint(1, 60))).isoformat()
    return t


def missing_required_field(t, rng):
    t[rng.choice(["vendor_gstin", "invoice_number", "invoice_date", "total"])] = None
    return t


def dropped_line_item(t, rng):
    if len(t["line_items"]) < 2:
        return None
    t["line_items"].pop(rng.randrange(len(t["line_items"])))
    return t


INJECTIONS = [
    ("misread GSTIN character", "GSTIN_FORMAT", misread_gstin_char),
    ("invalid state code", "STATE_CODE", invalid_state_code),
    ("transposed digits in a taxable value", "LINE_MATH", transposed_taxable_value),
    ("misread quantity", "LINE_MATH", misread_quantity),
    ("one digit off in the total", "TOTAL_MATH", misread_total_digit),
    ("wrong GST rate on a line", "TAX_MATH", wrong_gst_rate),
    ("IGST and CGST/SGST swapped", "TAX_TYPE", swapped_tax_type),
    ("CGST and SGST differ", "TAX_MATH", cgst_sgst_mismatch),
    ("future invoice date", "DATE_RANGE", future_date),
    ("required field missing", "REQUIRED", missing_required_field),
    ("a line item was skipped", "TOTAL_MATH", dropped_line_item),
]
TRIALS_PER_INVOICE = 3  # each injection is tried 3 times per invoice with different choices


def error_codes(truth: dict, **kwargs) -> set[str]:
    issues = validate(
        InvoiceData.from_dict(truth), today=TODAY, max_age_days=MAX_AGE_DAYS, **kwargs
    )
    return {issue.rule_code for issue in issues if issue.severity == "error"}


def false_alarms(truths: dict[str, dict]) -> dict[str, list[str]]:
    """Errors raised on correct invoices: each one is a rule bug."""
    return {name: sorted(error_codes(t)) for name, t in truths.items() if error_codes(t)}


def catch_rates(truths: dict[str, dict], seed: int = 7) -> dict:
    """Inject every kind of misread into every invoice, TRIALS_PER_INVOICE times."""
    rng = random.Random(seed)
    results = defaultdict(lambda: {"tried": 0, "caught_by_rule": 0, "caught_any": 0, "missed": []})
    for name, label_rule, inject in INJECTIONS:
        for invoice_id, truth in truths.items():
            for _ in range(TRIALS_PER_INVOICE):
                changed = inject(copy.deepcopy(truth), rng)
                if changed is None or changed == truth:
                    continue
                found = error_codes(changed)
                row = results[name]
                row["rule"] = label_rule
                row["tried"] += 1
                row["caught_by_rule"] += label_rule in found
                row["caught_any"] += bool(found)
                if not found:
                    row["missed"].append(invoice_id)

    # Duplicates come from the database, so they are injected through the arguments.
    dup = results["same invoice uploaded twice"]
    dup["rule"] = "DUPLICATE"
    for truth in truths.values():
        found = error_codes(truth, same_file_ids=[1], same_number_ids=[1])
        dup["tried"] += 1
        dup["caught_by_rule"] += "DUPLICATE" in found
        dup["caught_any"] += bool(found)

    tried = sum(r["tried"] for r in results.values())
    return {
        "injected": tried,
        "catch_rate_any_error": sum(r["caught_any"] for r in results.values()) / tried,
        "catch_rate_expected_rule": sum(r["caught_by_rule"] for r in results.values()) / tried,
        "by_injection": dict(results),
    }
