"""Generate synthetic GSTR-2B files for the 20 dev-split invoices, with known answers.

The 20 dev invoices play "the books" (all approved). For each month they cover
(May-Sep 2026) this writes a GSTR-2B JSON in the documented layout, where each
invoice was given one scenario:

    exact            reported as in the books                     -> matched
    reformatted      number typed differently (case, separators,
                     leading zeros)                               -> matched
    amount_changed   taxable value and tax reported higher         -> mismatched
    date_changed     reported 2 days later                         -> mismatched
    renumbered       a number that can't be normalised (prefix
                     dropped), same amounts                        -> possible
    omitted          the supplier never reported it               -> books_only (ITC at risk)
    late             reported in the next month's 2B               -> matched (next month)

plus 3 supplier invoices that aren't in the books (-> only_2b). labels.json lists
the expected result of every row and every month's "at risk" invoices.

Synthetic: the layout follows public descriptions of the GSTR-2B JSON, not a
file downloaded from the GST portal.

Usage (from the repo root): backend/.venv/bin/python scripts/make_gstr2b.py
"""

import json
import random
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOOKS = REPO / "evals/datasets/starter"
OUT = REPO / "evals/datasets/gstr2b"
RECIPIENT = "27AAACR5055K1Z7"  # the buyer filing this return (synthetic)

SCENARIOS = (
    ["exact"] * 8
    + ["reformatted"] * 3
    + ["amount_changed"] * 2
    + ["date_changed"]
    + ["renumbered"] * 2
    + ["omitted"] * 2
    + ["late"] * 2
)
EXPECTED = {
    "exact": "matched",
    "reformatted": "matched",
    "amount_changed": "mismatched",
    "date_changed": "mismatched",
    "renumbered": "possible",
    "late": "matched",
}


def money(value) -> Decimal:
    return Decimal(value or 0)


def reformat(number: str, rng: random.Random) -> str:
    """Same invoice number, typed differently: lower case, other separators, zeros."""
    parts = re.findall(r"[A-Za-z]+|\d+", number)
    parts = [p.lower() if p.isalpha() else ("00" + p if rng.random() < 0.5 else p) for p in parts]
    return rng.choice([" ", "-", ".", "/"]).join(parts)


def renumber(number: str) -> str:
    """A number our normalising can't line up: only the last group, with a new prefix."""
    last = re.findall(r"\d+", number)[-1]
    return f"S{int(last) + 1000}"


def month_of(day: date) -> date:
    return day.replace(day=1)


def next_month(first: date) -> date:
    return (first + timedelta(days=32)).replace(day=1)


def main() -> None:
    rng = random.Random(7)
    books = [json.loads(p.read_text()) for p in sorted(BOOKS.glob("inv_*.json"))]
    scenarios = SCENARIOS[:]
    rng.shuffle(scenarios)

    statements: dict[date, list] = {}  # month -> [(ctin, name, inv dict)]
    labels, at_risk = [], {}
    for truth, scenario in zip(books, scenarios, strict=True):
        day = date.fromisoformat(truth["invoice_date"])
        period = month_of(day)
        statements.setdefault(period, [])
        if scenario == "omitted":
            at_risk.setdefault(period.isoformat(), []).append(truth["invoice_number"])
            continue
        number, reported_day = truth["invoice_number"], day
        taxable, igst = money(truth["subtotal"]), money(truth["igst"])
        cgst, sgst, value = money(truth["cgst"]), money(truth["sgst"]), money(truth["total"])
        if scenario == "reformatted":
            number = reformat(number, rng)
        elif scenario == "renumbered":
            number = renumber(number)
        elif scenario == "date_changed":
            reported_day = day + timedelta(days=2)
        elif scenario == "amount_changed":
            extra = Decimal("100.00")
            rate = (igst + cgst + sgst) / taxable if taxable else Decimal(0)
            added_tax = (extra * rate).quantize(Decimal("0.01"))
            taxable += extra
            if igst:
                igst += added_tax
            else:
                cgst += (added_tax / 2).quantize(Decimal("0.01"))
                sgst += (added_tax / 2).quantize(Decimal("0.01"))
            value += extra + added_tax
        report_period = next_month(period) if scenario == "late" else period
        statements.setdefault(report_period, [])
        rate = ((igst + cgst + sgst) / taxable * 100).quantize(Decimal(1)) if taxable else 0
        statements[report_period].append(
            (
                truth["vendor_gstin"],
                truth["vendor_name"],
                {
                    "inum": number,
                    "dt": reported_day.strftime("%d-%m-%Y"),
                    "val": float(value),
                    "pos": RECIPIENT[:2],
                    "rev": "N",
                    "itcavl": "Y",
                    "items": [
                        {
                            "rt": float(rate),
                            "txval": float(taxable),
                            "igst": float(igst),
                            "cgst": float(cgst),
                            "sgst": float(sgst),
                            "cess": 0,
                        }
                    ],
                },
            )
        )
        labels.append(
            {
                "period": report_period.isoformat(),
                "supplier_gstin": truth["vendor_gstin"],
                "invoice_number": number,
                "scenario": scenario,
                "expected": EXPECTED[scenario],
            }
        )

    # Three supplier invoices that aren't in the books at all.
    months = sorted(statements)
    for n in range(3):
        period = months[n % len(months)]
        ctin = f"24EXTRA{n:04d}A1Z{n}"
        statements[period].append(
            (
                ctin,
                f"Unbooked Supplier {n + 1}",
                {
                    "inum": f"EXT-{n + 1}",
                    "dt": (period + timedelta(days=10)).strftime("%d-%m-%Y"),
                    "val": 1180.0,
                    "pos": RECIPIENT[:2],
                    "rev": "N",
                    "itcavl": "Y",
                    "items": [{"rt": 18, "txval": 1000.0, "igst": 180.0, "cgst": 0, "sgst": 0}],
                },
            )
        )
        labels.append(
            {
                "period": period.isoformat(),
                "supplier_gstin": ctin,
                "invoice_number": f"EXT-{n + 1}",
                "scenario": "not_in_books",
                "expected": "only_2b",
            }
        )

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("gstr2b-*.json"):
        old.unlink()
    for period, rows in sorted(statements.items()):
        suppliers: dict[str, dict] = {}
        for ctin, name, invoice in rows:
            suppliers.setdefault(ctin, {"ctin": ctin, "trdnm": name, "inv": []})["inv"].append(
                invoice
            )
        body = {
            "data": {
                "gstin": RECIPIENT,
                "rtnprd": period.strftime("%m%Y"),
                "version": "synthetic",
                "docdata": {"b2b": list(suppliers.values())},
            }
        }
        (OUT / f"gstr2b-{period:%Y-%m}.json").write_text(json.dumps(body, indent=1) + "\n")
    (OUT / "labels.json").write_text(
        json.dumps({"rows": labels, "at_risk": at_risk}, indent=1) + "\n"
    )
    print(
        f"Wrote {len(statements)} monthly GSTR-2B files ({sum(map(len, statements.values()))} "
        f"supplier invoices) and labels to {OUT.relative_to(REPO)}"
    )


if __name__ == "__main__":
    main()
