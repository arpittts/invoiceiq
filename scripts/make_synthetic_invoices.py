"""Generate synthetic Indian GST invoices with exact ground truth.

Why synthetic: we know every value on the page, so extraction accuracy can be
measured exactly, and no real customer data ever enters the repository.

Each invoice varies in layout (4 templates), font, tax type (CGST+SGST inside
a state, IGST across states), number of line items, date and amount formatting,
and file type (vector PDF, scanned PNG, slightly rotated JPEG "photo").

The ground truth lists what is *printed*. A value the layout does not print
(e.g. per-line tax in the "modern" layout) is null, because the extractor must
never invent it.

GST rates here are illustrative test data, not tax advice.

Usage (from the repo root):
    backend/.venv/bin/python scripts/make_synthetic_invoices.py --count 20 --seed 42
"""

import argparse
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pymupdf

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

STATES = {
    "27": "Maharashtra", "29": "Karnataka", "07": "Delhi", "33": "Tamil Nadu",
    "24": "Gujarat", "09": "Uttar Pradesh", "19": "West Bengal", "36": "Telangana",
}  # fmt: skip

VENDORS = [
    ("Sharma Auto Parts", "Plot 14, MIDC Bhosari, Pune", "27"),
    ("Lakshmi Traders", "45, Avenue Road, Bengaluru", "29"),
    ("Gupta Electricals", "B-12, Lajpat Nagar II, New Delhi", "07"),
    ("Sri Murugan Stores", "22, Ranganathan Street, Chennai", "33"),
    ("Patel Hardware & Paints", "Near Kankaria Lake, Ahmedabad", "24"),
    ("Verma Wholesale Mart", "Aminabad Market, Lucknow", "09"),
    ("Kolkata Paper House", "7, College Street, Kolkata", "19"),
    ("Deccan Office Supplies", "Abids Road, Hyderabad", "36"),
]
BUYERS = [
    ("Mehta Motors Pvt Ltd", "27"), ("Nair Hospitality LLP", "29"), ("Kapoor Retail", "07"),
    ("Iyer Engineering Works", "33"), ("Desai Builders", "24"), ("Singh Logistics", "09"),
    ("Banerjee Clinic", "19"), ("Reddy Software Labs", "36"), ("Joshi Cafe", "27"),
]  # fmt: skip

# (description, HSN/SAC, unit, price range in rupees, GST rate %)
PRODUCTS = [
    ("Basmati Rice 25kg bag", "1006", "Bag", (1400, 2600), 5),
    ("Fresh Vegetables (assorted)", "0709", "Kg", (30, 90), 0),
    ("Cotton T-Shirt", "6109", "Nos", (180, 650), 5),
    ("Ghee 1 kg tin", "0405", "Nos", (520, 780), 5),
    ("LED Bulb 9W", "8539", "Nos", (55, 140), 18),
    ("Ceramic Floor Tiles 600x600", "6907", "Box", (650, 1450), 18),
    ("Engine Oil 1L", "2710", "Ltr", (280, 520), 18),
    ("Two-wheeler Tyre", "4011", "Nos", (1100, 2400), 18),
    ("Cement 50kg bag", "2523", "Bag", (340, 430), 18),
    ("Printer Paper A4 (500 sheets)", "4802", "Ream", (240, 390), 18),
    ("Mobile Phone Charger", "8504", "Nos", (250, 900), 18),
    ("Steel Almirah", "9403", "Nos", (7800, 14500), 18),
    ("Aerated Soft Drink 2L", "2202", "Btl", (80, 110), 40),
    ("Annual Maintenance Service", "998719", "Job", (2500, 12000), 18),
    ("Plumbing Repair Service", "995461", "Hrs", (350, 900), 18),
]

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TWO_PLACES = Decimal("0.01")


def gstin_checksum(first14: str) -> str:
    """The official mod-36 check character for a GSTIN."""
    total = 0
    for i, char in enumerate(first14):
        product = _GSTIN_CHARS.index(char) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def make_gstin(state_code: str, rng: random.Random) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    pan = (
        "".join(rng.choice(letters) for _ in range(5))
        + "".join(rng.choice("0123456789") for _ in range(4))
        + rng.choice(letters)
    )
    first14 = f"{state_code}{pan}{rng.choice('123456789')}Z"
    return first14 + gstin_checksum(first14)


def money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def indian_grouping(value: Decimal) -> str:
    """Decimal('123456.5') -> '1,23,456.50' (lakh/crore grouping)."""
    sign = "-" if value < 0 else ""
    whole, fraction = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{sign}{whole}.{fraction}"


# ---------------------------------------------------------------------------
# Building one invoice
# ---------------------------------------------------------------------------


@dataclass
class Line:
    description: str
    hsn: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    discount: Decimal
    rate: int
    taxable: Decimal
    cgst: Decimal | None
    sgst: Decimal | None
    igst: Decimal | None
    total: Decimal


# Layout settings: what each template prints and how.
LAYOUTS = {
    "classic": {"font": "sans-serif", "discount": True, "line_tax": True,
                "date": "%d/%m/%Y", "pos": "{name} ({code})", "amount": "grouped"},
    "modern": {"font": "sans-serif", "discount": False, "line_tax": False,
               "date": "%d-%b-%Y", "pos": "{code}-{name}", "amount": "rupee"},
    "compact": {"font": "monospace", "discount": False, "line_tax": False,
                "date": "%d.%m.%Y", "pos": "{name}", "amount": "plain"},
    "letterhead": {"font": "serif", "discount": True, "line_tax": True,
                   "date": "%d %B %Y", "pos": "{name} (State Code: {code})", "amount": "grouped"},
}  # fmt: skip


def build_invoice(index: int, rng: random.Random) -> dict:
    layout_name = list(LAYOUTS)[index % len(LAYOUTS)]
    layout = LAYOUTS[layout_name]
    vendor_name, vendor_address, vendor_state = rng.choice(VENDORS)
    buyer_name, buyer_state = rng.choice(BUYERS)
    # About a third of invoices go to a buyer in the same state (CGST + SGST).
    if rng.random() < 0.35:
        buyer_state = vendor_state
    intra_state = buyer_state == vendor_state
    buyer_registered = rng.random() > 0.2

    lines = []
    for description, hsn, unit, (low, high), rate in rng.sample(PRODUCTS, rng.randint(1, 7)):
        quantity = Decimal(rng.randint(1, 20))
        if unit == "Kg" and rng.random() < 0.5:
            quantity = Decimal(rng.randint(5, 95)) / 2  # e.g. 12.5 kg
        price = money(Decimal(rng.randint(low * 100, high * 100)) / 100)
        gross = money(quantity * price)
        discount = Decimal(0)
        if layout["discount"] and rng.random() < 0.3:
            discount = money(gross * Decimal(rng.choice([2, 5, 10])) / 100)
        taxable = gross - discount
        if intra_state:
            half = money(taxable * rate / 200)
            cgst, sgst, igst = half, half, None
            total = taxable + half + half
        else:
            cgst, sgst, igst = None, None, money(taxable * rate / 100)
            total = taxable + igst
        lines.append(
            Line(description, hsn, quantity, unit, price, discount, rate, taxable,
                 cgst, sgst, igst, total)
        )  # fmt: skip

    subtotal = sum((line.taxable for line in lines), Decimal(0))
    cgst = sum((line.cgst for line in lines if line.cgst), Decimal(0)) if intra_state else None
    sgst = sum((line.sgst for line in lines if line.sgst), Decimal(0)) if intra_state else None
    igst = None if intra_state else sum((line.igst for line in lines), Decimal(0))
    exact_total = subtotal + (cgst or 0) + (sgst or 0) + (igst or 0)
    # The letterhead layout rounds the payable amount to whole rupees, as many shops do.
    round_off = None
    total = exact_total
    if layout_name == "letterhead":
        total = exact_total.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        round_off = total - exact_total

    year_suffix = rng.choice(["2026-27", "26-27"])
    number = rng.choice([
        f"{vendor_name[:3].upper()}/{year_suffix}/{rng.randint(1, 999):04d}",
        f"INV-{rng.randint(10000, 99999)}",
        f"{rng.randint(100, 9999)}",
    ])  # fmt: skip

    return {
        "id": f"inv_{index + 1:03d}",
        "layout": layout_name,
        "vendor_name": vendor_name,
        "vendor_address": vendor_address,
        "vendor_gstin": make_gstin(vendor_state, rng),
        "buyer_name": buyer_name,
        "buyer_gstin": make_gstin(buyer_state, rng) if buyer_registered else None,
        "buyer_state": buyer_state,
        "invoice_number": number,
        "invoice_date": date(2026, 9, 30) - timedelta(days=rng.randint(0, 150)),
        "place_of_supply": layout["pos"].format(name=STATES[buyer_state], code=buyer_state),
        "intra_state": intra_state,
        "lines": lines,
        "subtotal": subtotal,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "round_off": round_off,
        "total": total,
    }


# ---------------------------------------------------------------------------
# Ground truth: exactly what is printed, typed
# ---------------------------------------------------------------------------


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def ground_truth(inv: dict) -> dict:
    layout = LAYOUTS[inv["layout"]]
    items = []
    for line in inv["lines"]:
        items.append({
            "description": line.description,
            "hsn_sac_code": line.hsn,
            "quantity": _num(line.quantity),
            "unit": line.unit,
            "unit_price": _num(line.unit_price),
            # A discount column only exists in some layouts; an empty cell prints "-".
            "discount": _num(line.discount) if layout["discount"] and line.discount else None,
            "taxable_value": _num(line.taxable),
            "gst_rate": str(line.rate),
            "cgst_amount": _num(line.cgst) if layout["line_tax"] else None,
            "sgst_amount": _num(line.sgst) if layout["line_tax"] else None,
            "igst_amount": _num(line.igst) if layout["line_tax"] else None,
            "line_total": _num(line.total) if layout["line_tax"] else None,
        })  # fmt: skip
    return {
        "vendor_name": inv["vendor_name"],
        "vendor_gstin": inv["vendor_gstin"],
        "buyer_name": inv["buyer_name"],
        "buyer_gstin": inv["buyer_gstin"],
        "invoice_number": inv["invoice_number"],
        "invoice_date": inv["invoice_date"].isoformat(),
        "place_of_supply": inv["place_of_supply"],
        "line_items": items,
        "subtotal": _num(inv["subtotal"]),
        "cgst": _num(inv["cgst"]),
        "sgst": _num(inv["sgst"]),
        "igst": _num(inv["igst"]),
        "total": _num(inv["total"]),
    }


# ---------------------------------------------------------------------------
# Rendering to HTML, then PDF / image
# ---------------------------------------------------------------------------


def fmt_amount(value: Decimal, style: str) -> str:
    if style == "plain":
        return f"{value:.2f}"
    grouped = indian_grouping(value)
    return f"₹{grouped}" if style == "rupee" else grouped


def fmt_qty(quantity: Decimal) -> str:
    return f"{quantity:.2f}" if quantity != quantity.to_integral() else str(int(quantity))


def render_html(inv: dict) -> str:
    layout = LAYOUTS[inv["layout"]]
    a = lambda value: fmt_amount(value, layout["amount"])  # noqa: E731
    intra = inv["intra_state"]
    buyer_gstin = f"<br>GSTIN: {inv['buyer_gstin']}" if inv["buyer_gstin"] else ""
    date_text = inv["invoice_date"].strftime(layout["date"])

    headers = ["#", "Description", "HSN/SAC", "Qty", "Unit", "Rate"]
    if layout["discount"]:
        headers.append("Disc.")
    headers += ["Taxable Value", "GST %"]
    if layout["line_tax"]:
        headers += ["CGST", "SGST", "Total"] if intra else ["IGST", "Total"]

    rows = []
    for number, line in enumerate(inv["lines"], start=1):
        cells = [str(number), line.description, line.hsn, fmt_qty(line.quantity), line.unit,
                 a(line.unit_price)]  # fmt: skip
        if layout["discount"]:
            cells.append(a(line.discount) if line.discount else "-")
        rate_text = f"{line.rate}.00%" if inv["layout"] == "letterhead" else f"{line.rate}%"
        cells += [a(line.taxable), rate_text]
        if layout["line_tax"]:
            cells += [a(line.cgst), a(line.sgst)] if intra else [a(line.igst)]
            cells.append(a(line.total))
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")

    totals = [("Taxable Amount" if inv["layout"] == "modern" else "Sub Total", inv["subtotal"])]
    if intra:
        totals += [("CGST", inv["cgst"]), ("SGST", inv["sgst"])]
    else:
        totals.append(("IGST", inv["igst"]))
    if inv["round_off"] is not None:
        totals.append(("Round Off", inv["round_off"]))
    totals.append(("Grand Total" if inv["layout"] != "compact" else "TOTAL", inv["total"]))
    totals_html = "".join(
        f"<tr><td class='label'>{label}</td><td class='num'>{a(value)}</td></tr>"
        for label, value in totals
    )

    # Stacked blocks (MuPDF's HTML engine overlaps side-by-side table cells).
    # Layouts differ in which block comes first.
    bill_to = f"<p><b>Bill To:</b><br>{inv['buyer_name']}{buyer_gstin}</p>"
    meta = (
        f"<p>Invoice No: {inv['invoice_number']}<br>Invoice Date: {date_text}<br>"
        f"Place of Supply: {inv['place_of_supply']}</p>"
    )
    blocks = [meta, bill_to] if inv["layout"] in ("modern", "letterhead") else [bill_to, meta]

    header_band = "background:#1f4e79;color:white;padding:8px;" if inv["layout"] == "modern" else ""
    return f"""
    <div style="font-family:{layout["font"]};font-size:9pt">
      <div style="{header_band}">
        <div style="font-size:15pt;font-weight:bold">{inv["vendor_name"]}</div>
        <div>{inv["vendor_address"]}</div>
        <div>GSTIN: {inv["vendor_gstin"]}</div>
      </div>
      <h2 style="text-align:center">TAX INVOICE</h2>
      {"".join(blocks)}
      <br>
      <table class="items" style="width:100%;border-collapse:collapse">
        <tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr>
        {"".join(rows)}
      </table>
      <table style="width:45%;margin-left:55%;margin-top:8px">{totals_html}</table>
      <p style="margin-top:24px">This is a computer generated invoice.</p>
    </div>"""


CSS = """
.items th, .items td { border: 0.5px solid #888; padding: 2px 3px; }
.items th { background: #eee; }
td.num { text-align: right; }
td.label { font-weight: bold; }
"""


def render_pdf(inv: dict) -> bytes:
    doc = pymupdf.open()
    # The compact layout uses a narrower A5 page, like a shop receipt.
    width, height = (420, 595) if inv["layout"] == "compact" else (595, 842)
    page = doc.new_page(width=width, height=height)
    page.insert_htmlbox(pymupdf.Rect(30, 30, width - 30, height - 30), render_html(inv), css=CSS)
    return doc.tobytes()


def to_image(pdf: bytes, kind: str, rng: random.Random) -> bytes:
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    if kind == "png":  # a flat scan
        return page.get_pixmap(dpi=130, alpha=False).tobytes("png")
    # A phone "photo": slightly rotated, lower-quality JPEG.
    angle = rng.choice([-2.0, -1.2, 1.0, 1.8])
    zoom = 150 / 72
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(angle), alpha=False)
    return pixmap.tobytes("jpeg", jpg_quality=70)


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/sample_invoices")
    parser.add_argument("--truth", default="evals/datasets/starter")
    args = parser.parse_args()

    rng = random.Random(args.seed)  # same seed -> identical invoices every run
    out_dir, truth_dir = Path(args.out), Path(args.truth)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for index in range(args.count):
        inv = build_invoice(index, rng)
        pdf = render_pdf(inv)
        kind = {3: "png", 4: "jpg"}.get(index % 5, "pdf")  # 60% PDF, 20% scan, 20% photo
        data = pdf if kind == "pdf" else to_image(pdf, kind, rng)
        file_path = out_dir / f"{inv['id']}.{kind}"
        file_path.write_bytes(data)

        truth_path = truth_dir / f"{inv['id']}.json"
        truth_path.write_text(json.dumps(ground_truth(inv), indent=2) + "\n")
        manifest.append({
            "id": inv["id"], "file": str(file_path), "truth": str(truth_path),
            "layout": inv["layout"], "format": kind,
            "tax_type": "intra" if inv["intra_state"] else "inter",
            "line_items": len(inv["lines"]),
        })  # fmt: skip

    (truth_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(manifest)} invoices to {out_dir} and ground truth to {truth_dir}")


if __name__ == "__main__":
    main()
