"""Comparing two reads field by field (values after parsing)."""

import json

from app.schemas.extraction import ExtractionOutput, to_invoice_extraction
from app.services.agreement import disagreements
from tests.test_extraction_service import valid_reply


def read(**changes):
    data = json.loads(valid_reply())
    for path, value in changes.items():
        if "." in path:
            index, field = path.split(".")
            data["line_items"][int(index)][field]["value"] = value
        else:
            data[path]["value"] = value
    return to_invoice_extraction(ExtractionOutput.model_validate(data))


def test_identical_reads_agree():
    assert disagreements(read(), read()) == []


def test_formatting_differences_are_not_disagreements():
    """1,180.00 and 1180.00 are the same amount; confidence is ignored."""
    first = read()
    second_data = json.loads(valid_reply())
    second_data["total"]["value"] = second_data["total"]["value"].replace(",", "")
    second_data["total"]["confidence"] = 0.4
    second = to_invoice_extraction(ExtractionOutput.model_validate(second_data))
    assert disagreements(first, second) == []


def test_header_and_line_differences_are_named():
    assert disagreements(read(), read(total="9,999.00", **{"0.line_total": None})) == [
        "total",
        "line_items[1].line_total",
    ]


def test_a_different_number_of_lines_is_one_disagreement():
    first = read()
    data = json.loads(valid_reply())
    data["line_items"] = data["line_items"] * 2
    second = to_invoice_extraction(ExtractionOutput.model_validate(data))
    assert disagreements(first, second) == ["line_items"]
