import pytest

from app.services.gst import STATE_CODES, gstin_checksum, gstin_problem, state_code_of_place

VALID = "27AAPFU0939F1ZV"  # the sample GSTIN used in official GST documentation


def test_checksum_of_known_gstin():
    assert gstin_checksum(VALID[:14]) == "V"
    assert gstin_problem(VALID) is None


@pytest.mark.parametrize(
    ("gstin", "reason"),
    [
        ("27AAPFU0939F1Z", "format"),  # 14 characters
        ("27AAPFU0939F1XV", "format"),  # 14th character must be Z
        ("27AAPFUO939F1ZV", "format"),  # letter O where a digit belongs
        ("27AAPFU0939F1ZW", "check character"),  # last character wrong
        ("27AAPFU0938F1ZV", "check character"),  # one digit misread: 9 -> 8
    ],
)
def test_invalid_gstins_are_explained(gstin, reason):
    assert reason in gstin_problem(gstin)


def test_single_character_misreads_are_almost_always_caught():
    """Change each of the first 14 characters to every other legal character:
    the checksum should catch nearly all of them."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    caught = total = 0
    for position in range(14):
        for char in chars:
            if char == VALID[position]:
                continue
            candidate = VALID[:position] + char + VALID[position + 1 :]
            if not candidate[:2].isdigit():
                continue
            total += 1
            caught += gstin_problem(candidate) is not None
    assert caught / total > 0.95


@pytest.mark.parametrize(
    ("place", "code"),
    [
        ("Maharashtra (27)", "27"),
        ("27-Maharashtra", "27"),
        ("Karnataka", "29"),
        ("Tamil Nadu (State Code: 33)", "33"),
        ("NCT of Delhi", "07"),
        ("New Delhi", "07"),
        ("Andhra Pradesh", "37"),  # a name means the current code, not legacy 28
        ("Orissa", "21"),
        ("Somewhere", None),
        (None, None),
    ],
)
def test_state_code_of_place(place, code):
    assert state_code_of_place(place) == code


def test_state_codes_are_two_digits():
    assert all(len(code) == 2 and code.isdigit() for code in STATE_CODES)
    assert "38" in STATE_CODES and "37" in STATE_CODES and "26" in STATE_CODES
