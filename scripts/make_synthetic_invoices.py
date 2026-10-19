"""Generate synthetic Indian GST invoices with exact ground truth.

Why synthetic: we know every value on the page, so extraction accuracy can be
measured exactly, and no real customer data ever enters the repository.

Each invoice varies in layout (4 templates), font, tax type (CGST+SGST inside
a state, IGST across states), number of line items, date and amount formatting,
and file type (vector PDF, scanned PNG, slightly rotated JPEG "photo").

Two splits (blueprint section 12):
- dev: the 20 starter invoices (seed 42). Used while building; frozen.
- test: 60 harder invoices (--hard, seed 2026), frozen and only used to report
  results. On top of the above they add long invoices that run onto a second
  PDF page, more products, fonts and sizes, 2-digit years and "Rs." amounts,
  low-resolution scans, blurred and more rotated phone photos, and faint
  low-contrast "photocopies".

The ground truth lists what is *printed*. A value the layout does not print
(e.g. per-line tax in the "modern" layout) is null, because the extractor must
never invent it.

GST rates here are illustrative test data, not tax advice.

Usage (from the repo root):
    backend/.venv/bin/python scripts/make_synthetic_invoices.py            # dev split
    backend/.venv/bin/python scripts/make_synthetic_invoices.py --hard --count 60 --seed 2026 \
        --prefix test --out data/sample_invoices/test --truth evals/datasets/test
"""

import argparse
import io
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
# Only used by --hard, so the dev split stays exactly reproducible.
EXTRA_PRODUCTS = [
    ("Toothpaste 150g", "3306", "Nos", (85, 160), 5),
    ("Bathing Soap (pack of 4)", "3401", "Pkt", (120, 240), 5),
    ("Split Air Conditioner 1.5 Ton", "8415", "Nos", (28000, 42000), 18),
    ("LED Television 43 inch", "8528", "Nos", (19000, 31000), 18),
    ("Emulsion Paint 20L", "3209", "Bkt", (3200, 6400), 18),
    ("PVC Pipe 4 inch x 3m", "3917", "Nos", (420, 980), 18),
    ("Exercise Notebook 172 pages", "4820", "Nos", (35, 70), 0),
    ("Wheat Atta 10kg", "1101", "Bag", (380, 560), 5),
    ("Sugar 50kg bag", "1701", "Bag", (1900, 2400), 5),
    ("Courier Service", "9968", "Job", (150, 1800), 18),
    ("Pan Masala 100g", "2106", "Pkt", (180, 320), 40),
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


# --hard style overrides (font, size, date and amount formats).
HARD_FONTS = ["sans-serif", "serif", "monospace", "droid"]  # droid: Droid Sans, bundled with MuPDF
HARD_DATES = ["%d/%m/%y", "%d-%m-%Y", "%d %b %y", "%d.%m.%y"]
HARD_AMOUNTS = ["grouped", "rupee", "plain", "rs"]


def build_invoice(index: int, rng: random.Random, hard: bool = False, long: bool = False) -> dict:
    """One invoice. `hard` varies style more; `long` gives 30-40 lines (2+ PDF pages)."""
    layout_name = list(LAYOUTS)[index % len(LAYOUTS)]
    layout = LAYOUTS[layout_name]
    vendor_name, vendor_address, vendor_state = rng.choice(VENDORS)
    buyer_name, buyer_state = rng.choice(BUYERS)
    # About a third of invoices go to a buyer in the same state (CGST + SGST).
    if rng.random() < 0.35:
        buyer_state = vendor_state
    intra_state = buyer_state == vendor_state
    buyer_registered = rng.random() > 0.2

    pool = PRODUCTS + EXTRA_PRODUCTS if hard else PRODUCTS
    count = rng.randint(30, 40) if long else rng.randint(1, 7)
    if count <= len(pool):
        products = rng.sample(pool, count)
    else:  # long bills repeat items from different lots
        products = rng.sample(pool, len(pool)) + rng.choices(pool, k=count - len(pool))
    lines, seen = [], set()
    for description, hsn, unit, (low, high), rate in products:
        if description in seen:
            description = f"{description} (Lot {rng.randint(10, 99)})"
        seen.add(description)
        quantity = Decimal(rng.randint(1, 20))
        if unit == "Kg" and rng.random() < 0.5:
            quantity = Decimal(rng.randint(5, 95)) / 2  # e.g. 12.5 kg
        if hard and unit in ("Hrs", "Ltr") and rng.random() < 0.5:
            quantity = Decimal(rng.randint(3, 39)) / 2  # e.g. 7.5 hours
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

    year_suffix = rng.choice(["2026-27", "26-27"])
    number = rng.choice([
        f"{vendor_name[:3].upper()}/{year_suffix}/{rng.randint(1, 999):04d}",
        f"INV-{rng.randint(10000, 99999)}",
        f"{rng.randint(100, 9999)}",
    ])  # fmt: skip

    style = {}
    if hard:
        style = {
            "font": rng.choice(HARD_FONTS),
            "size": rng.choice([7.5, 8, 9, 10]),
            "date": rng.choice(HARD_DATES + [layout["date"]]),
            "amount": rng.choice(HARD_AMOUNTS),
        }

    return {
        "id": f"inv_{index + 1:03d}",
        "layout": layout_name,
        "style": style,
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
        **_totals({"layout": layout_name, "intra_state": intra_state, "lines": lines}),
    }


def _totals(inv: dict) -> dict:
    lines, intra_state = inv["lines"], inv["intra_state"]
    subtotal = sum((line.taxable for line in lines), Decimal(0))
    cgst = sum((line.cgst for line in lines if line.cgst), Decimal(0)) if intra_state else None
    sgst = sum((line.sgst for line in lines if line.sgst), Decimal(0)) if intra_state else None
    igst = None if intra_state else sum((line.igst for line in lines), Decimal(0))
    exact_total = subtotal + (cgst or 0) + (sgst or 0) + (igst or 0)
    # The letterhead layout rounds the payable amount to whole rupees, as many shops do.
    round_off = None
    total = exact_total
    if inv["layout"] == "letterhead":
        total = exact_total.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        round_off = total - exact_total
    return {"subtotal": subtotal, "cgst": cgst, "sgst": sgst, "igst": igst,
            "round_off": round_off, "total": total}  # fmt: skip


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
    if style == "rs":
        return f"Rs. {grouped}"
    return f"₹{grouped}" if style == "rupee" else grouped


def fmt_qty(quantity: Decimal) -> str:
    return f"{quantity:.2f}" if quantity != quantity.to_integral() else str(int(quantity))


def render_html(inv: dict) -> str:
    layout = {**LAYOUTS[inv["layout"]], **inv.get("style", {})}
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
    <div style="font-family:{layout["font"]};font-size:{layout.get("size", 9)}pt">
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
    # The compact layout uses a narrower A5 page, like a shop receipt.
    width, height = (420, 595) if inv["layout"] == "compact" else (595, 842)
    if inv.get("style"):
        return _render_flowing_pdf(inv, width, height)
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_htmlbox(pymupdf.Rect(30, 30, width - 30, height - 30), render_html(inv), css=CSS)
    return doc.tobytes()


def _render_flowing_pdf(inv: dict, width: float, height: float) -> bytes:
    """--hard invoices: text flows onto further pages instead of shrinking to fit."""
    archive, css = None, CSS
    if inv["style"]["font"] == "droid":
        archive = pymupdf.Archive()
        archive.add((pymupdf.Font("cjk").buffer, "droid.ttf"))  # Droid Sans, bundled with MuPDF
        css += "@font-face { font-family: droid; src: url(droid.ttf); }"
    story = pymupdf.Story(html=render_html(inv), user_css=css, archive=archive)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.Rect(0, 0, width, height)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(mediabox + (30, 30, -30, -30))
        story.draw(device)
        writer.end_page()
    writer.close()
    doc = pymupdf.open(stream=buffer.getvalue(), filetype="pdf")
    doc.subset_fonts()  # embed only the glyphs used (Droid Sans alone is 3.5 MB)
    return doc.tobytes(garbage=3, deflate=True)


def to_image(pdf: bytes, kind: str, rng: random.Random) -> bytes:
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    if kind == "png":  # a flat scan
        return page.get_pixmap(dpi=130, alpha=False).tobytes("png")
    # A phone "photo": slightly rotated, lower-quality JPEG.
    angle = rng.choice([-2.0, -1.2, 1.0, 1.8])
    zoom = 150 / 72
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(angle), alpha=False)
    return pixmap.tobytes("jpeg", jpg_quality=70)


def blur(pixmap: pymupdf.Pixmap, factor: float) -> pymupdf.Pixmap:
    """Soften by shrinking and scaling back up (like an out-of-focus camera)."""
    width, height = round(pixmap.width * factor), round(pixmap.height * factor)
    small = pymupdf.Pixmap(pixmap, width, height, None)
    return pymupdf.Pixmap(small, pixmap.width, pixmap.height, None)


def degrade(pdf: bytes, kind: str, rng: random.Random) -> tuple[bytes, str, list[str]]:
    """--hard images: (file bytes, extension, what was done). First page only."""
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    if kind == "scan":  # a cheap low-resolution scan
        dpi = rng.choice([80, 90, 100])
        return page.get_pixmap(dpi=dpi, alpha=False).tobytes("png"), "png", [f"scan {dpi}dpi"]
    if kind == "photo":  # a phone photo: more rotation, out of focus, warm paper
        angle = rng.choice([-1, 1]) * rng.uniform(2.5, 4.5)
        zoom = 150 / 72
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(angle), alpha=False)
        sharpness = rng.choice([0.45, 0.55, 0.65])
        pixmap = blur(pixmap, sharpness)
        pixmap.tint_with(0x262626, 0xF0E6D2)
        quality = rng.choice([45, 55, 65])
        done = [f"rotated {angle:.1f}°", f"blur {sharpness}", f"jpeg q{quality}"]
        return pixmap.tobytes("jpeg", jpg_quality=quality), "jpg", done
    # A faint photocopy: grey, low contrast, slightly soft.
    pixmap = page.get_pixmap(dpi=110, alpha=False, colorspace=pymupdf.csGRAY)
    pixmap = blur(pixmap, 0.75)
    dark = rng.choice([0x5A, 0x6E, 0x80])
    pixmap.tint_with(dark * 0x010101, 0xE4E4E4)
    return pixmap.tobytes("png"), "png", [f"photocopy (ink #{dark:02X})", "blur 0.75"]


# ---------------------------------------------------------------------------


def make_hard(index: int, rng: random.Random) -> tuple[dict, bytes, str, list[str]]:
    """A --hard invoice: 40% PDF (some long, 2 pages), 20% each scan/photo/photocopy."""
    kind = {2: "scan", 3: "photo", 4: "photocopy"}.get(index % 5, "pdf")
    long = kind == "pdf" and rng.random() < 0.4
    inv = build_invoice(index, rng, hard=True, long=long)
    pdf = render_pdf(inv)
    if kind == "pdf":
        return inv, pdf, "pdf", [f"{len(pymupdf.open(stream=pdf, filetype='pdf'))} page(s)"]
    # Images show one page, so keep image invoices short enough to fit on it.
    while len(pymupdf.open(stream=pdf, filetype="pdf")) > 1:
        inv["lines"].pop()
        inv.update(_totals(inv))
        pdf = render_pdf(inv)
    data, extension, done = degrade(pdf, kind, rng)
    return inv, data, extension, done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/sample_invoices")
    parser.add_argument("--truth", default="evals/datasets/starter")
    parser.add_argument("--hard", action="store_true", help="the harder test-split variants")
    parser.add_argument("--prefix", default="inv", help="file name prefix, e.g. test")
    args = parser.parse_args()

    rng = random.Random(args.seed)  # same seed -> identical invoices every run
    out_dir, truth_dir = Path(args.out), Path(args.truth)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for index in range(args.count):
        if args.hard:
            inv, data, kind, degradations = make_hard(index, rng)
        else:
            inv = build_invoice(index, rng)
            pdf = render_pdf(inv)
            kind = {3: "png", 4: "jpg"}.get(index % 5, "pdf")  # 60% PDF, 20% scan, 20% photo
            data = pdf if kind == "pdf" else to_image(pdf, kind, rng)
            degradations = []
        inv["id"] = inv["id"].replace("inv", args.prefix, 1)
        file_path = out_dir / f"{inv['id']}.{kind}"
        file_path.write_bytes(data)

        truth_path = truth_dir / f"{inv['id']}.json"
        truth_path.write_text(json.dumps(ground_truth(inv), indent=2) + "\n")
        manifest.append({
            "id": inv["id"], "file": str(file_path), "truth": str(truth_path),
            "layout": inv["layout"], "format": kind,
            "tax_type": "intra" if inv["intra_state"] else "inter",
            "line_items": len(inv["lines"]),
            **({"degradations": degradations} if args.hard else {}),
        })  # fmt: skip

    (truth_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(manifest)} invoices to {out_dir} and ground truth to {truth_dir}")


if __name__ == "__main__":
    main()
