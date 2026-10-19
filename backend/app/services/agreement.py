"""Do two independent reads of an invoice agree? (v1.2, before auto-approving.)

The model's own confidence is not a usable signal (0.95-1.0 even when wrong), and
some misreads pass every validation rule (e.g. a line total filled in although
the layout doesn't print one). A second, deliberately different read that gives
the same value for every field is evidence that doesn't rely on the model
grading itself. Values are compared after parsing: 1,180.00 and 1180 agree.
"""

from app.schemas.extraction import InvoiceExtraction, LineItem

HEADER_FIELDS = [name for name in InvoiceExtraction.model_fields if name != "line_items"]
LINE_FIELDS = list(LineItem.model_fields)

# Re-read instruction for the second look (added after the normal instruction).
SECOND_LOOK = (
    "This is an independent second reading used to double-check another reading. "
    "Read every field again from the page, character by character, as if for the "
    "first time. Copy values exactly as printed; leave a field null if it is not "
    "printed on the invoice."
)


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.split()) == " ".join(b.split())
    return a == b


def disagreements(first: InvoiceExtraction, second: InvoiceExtraction) -> list[str]:
    """Field paths whose values differ, e.g. ["total", "line_items[2].line_total"].

    A different number of line items is one disagreement: "line_items".
    """
    fields = [
        name
        for name in HEADER_FIELDS
        if not _same(getattr(first, name).value, getattr(second, name).value)
    ]
    if len(first.line_items) != len(second.line_items):
        return [*fields, "line_items"]
    pairs = zip(first.line_items, second.line_items, strict=True)  # same length here
    for number, (a, b) in enumerate(pairs, start=1):
        fields += [
            f"line_items[{number}].{name}"
            for name in LINE_FIELDS
            if not _same(getattr(a, name).value, getattr(b, name).value)
        ]
    return fields
