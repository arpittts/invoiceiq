"""GST reference data and GSTIN helpers. Pure functions, no database.

State codes: the first two digits of a GSTIN. Sources checked 2026-10-18; they
disagree on whether 25 (Daman and Diu, merged into 26 in 2020) and 28 (Andhra
Pradesh before the 2014 split, now 37) still appear on registrations, so both are
accepted as legacy codes rather than flagging real invoices as invalid.
"""

import re

STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu (legacy)", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh (legacy)", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}  # fmt: skip

# Other spellings seen on invoices, mapped to a state code.
_ALIASES = {
    "new delhi": "07", "nct of delhi": "07", "orissa": "21", "pondicherry": "34",
    "j&k": "01", "jammu & kashmir": "01", "andaman & nicobar islands": "35",
    "dadra and nagar haveli": "26", "daman and diu": "26", "uttaranchal": "05",
}  # fmt: skip

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHECKSUM_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum(first14: str) -> str:
    """The GSTIN check character (mod 36). Weights alternate 1, 2 across the 14 characters;
    each product contributes (product // 36) + (product % 36) to the sum."""
    total = 0
    for i, char in enumerate(first14):
        product = _CHECKSUM_CHARS.index(char) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _CHECKSUM_CHARS[(36 - total % 36) % 36]


def gstin_problem(gstin: str) -> str | None:
    """Why a GSTIN is invalid, or None if it passes the format and checksum checks."""
    if not GSTIN_PATTERN.fullmatch(gstin):
        return "does not match the 15-character GSTIN format"
    if gstin_checksum(gstin[:14]) != gstin[14]:
        return "check character is wrong (likely a misread character)"
    return None


def state_code_of_place(place: str | None) -> str | None:
    """Find the state code in a place-of-supply text like 'Maharashtra (27)' or '27-Maharashtra'."""
    if not place:
        return None
    for code in re.findall(r"\b(\d{2})\b", place):
        if code in STATE_CODES:
            return code
    text = re.sub(r"\s+", " ", place.lower()).strip()
    # Longest names first, so "Andhra Pradesh" is not matched inside a longer name by mistake.
    names = sorted(
        [(name.lower().removesuffix(" (legacy)"), code) for code, name in STATE_CODES.items()]
        + list(_ALIASES.items()),
        key=lambda pair: -len(pair[0]),
    )
    for name, code in names:
        if code in ("25", "28"):
            continue  # a printed state name means the current code, not a legacy one
        if re.search(rf"\b{re.escape(name)}\b", text):
            return code
    return None
