"""Generate the sample invoice PDFs (spec section 9).

Run:  python scripts/generate_invoices.py
Out:  samples/01..06 *.pdf   (03 is an image-only "scanned" PDF)
"""
import io
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageFilter                      # noqa: E402
import pypdfium2 as pdfium                               # noqa: E402
from reportlab.lib import colors                         # noqa: E402
from reportlab.lib.pagesizes import A4                   # noqa: E402
from reportlab.lib.units import mm                       # noqa: E402
from reportlab.pdfgen import canvas                      # noqa: E402

from core.config import BUYER_GSTIN, BUYER_NAME, SAMPLES_DIR   # noqa: E402
from core.money import format_inr                       # noqa: E402

W, H = A4
BUYER_ADDR = ["No. 14, Residency Road", "Bengaluru, Karnataka 560025"]


def rs(x) -> str:
    return "Rs. " + format_inr(x, symbol=False)


# ------------------------------------------------------------------ data
INVOICES = {
    "01_happy_acme.pdf": dict(
        layout="classic",
        vendor="Acme Office Supplies Pvt Ltd",
        vendor_addr=["Plot 22, Peenya Industrial Area", "Bengaluru, Karnataka 560058"],
        gstin="29AAACA1111A1Z1", number="ACME/INV/0057", date="08-11-2026", due="08-12-2026",
        po="PO-2026-0101",
        lines=[("Ergonomic office chair", "9401", 50, 4000), ("Office desk 120cm", "9403", 20, 7500)],
        tax="intra", bank=("HDFC Bank", "HDFC0001234", "50100012345678"),
    ),
    "02_split_po_overrun.pdf": dict(
        layout="modern",
        vendor="Bharat Cloud Services LLP",
        vendor_addr=["7th Floor, Lotus Corporate Park", "Goregaon East, Mumbai, Maharashtra 400063"],
        gstin="27AAJFB2222B1Z2", number="BCS/2026/1203", date="03-12-2026", due="02-01-2027",
        po="PO-2026-0102",
        lines=[("Managed cloud hosting - December 2026", "998315", 1, 200000),
               ("Additional bandwidth 2TB", "998315", 1, 40000)],
        tax="inter", bank=("ICICI Bank", "ICIC0000456", "000405012345"),
    ),
    "03_scanned_duplicate.pdf": dict(
        layout="modern", scanned=True,
        vendor="Bharat Cloud Services LLP",
        vendor_addr=["7th Floor, Lotus Corporate Park", "Goregaon East, Mumbai, Maharashtra 400063"],
        gstin="27AAJFB2222B1Z2", number="BCS-2026-1102", date="04-11-2026", due="04-12-2026",
        po="PO-2026-0102",
        lines=[("Managed cloud hosting - November 2026", "998315", 1, 200000)],
        tax="inter", bank=("ICICI Bank", "ICIC0000456", "000405012345"),
        stamp="REMINDER - PAYMENT PENDING",
    ),
    "04_missing_po_sharma.pdf": dict(
        layout="classic",
        vendor="Sharma Logistics",
        vendor_addr=["Survey No. 41, Hosur Road", "Electronic City, Bengaluru 560100"],
        gstin="29ABCPS3333C1Z3", number="SL/26-27/0318", date="10-11-2026", due="10-12-2026",
        po=None,
        lines=[("Freight charges - Bengaluru to Chennai, full truck load, Oct-Nov 2026", "996511", 12, 15000)],
        tax="intra", bank=("State Bank of India", "SBIN0001111", "30012345678"),
    ),
    "05_bank_change_acme.pdf": dict(
        layout="classic",
        vendor="Acme Office Supplies Pvt Ltd",
        vendor_addr=["Plot 22, Peenya Industrial Area", "Bengaluru, Karnataka 560058"],
        gstin="29AAACA1111A1Z1", number="ACME/INV/0061", date="09-11-2026", due="09-12-2026",
        po="PO-2026-0104",
        lines=[("A4 copier paper 75gsm (ream)", "4802", 100, 300)],
        tax="intra", bank=("Kotak Mahindra Bank", "KKBK0000789", "9988776655"),
        bank_note="IMPORTANT: Please note our NEW bank details below. "
                  "Kindly update your records and remit all future payments to this account.",
    ),
    "06_blocked_vendor.pdf": dict(
        layout="modern",
        vendor="Nimbus Print Works",
        vendor_addr=["18, Anna Salai", "Chennai, Tamil Nadu 600002"],
        gstin="33AADCN4444D1Z4", number="NPW/112", date="07-11-2026", due="07-12-2026",
        po=None,
        lines=[("Printed marketing brochures A5, 5000 nos", "4911", 1, 25000)],
        tax="inter", bank=("Axis Bank", "UTIB0002222", "912010012345"),
    ),
}


def totals(inv):
    taxable = sum(Decimal(q) * Decimal(p) for _, _, q, p in inv["lines"])
    if inv["tax"] == "intra":
        cgst = sgst = (taxable * Decimal("0.09")).quantize(Decimal("0.01"))
        igst = Decimal(0)
    else:
        cgst = sgst = Decimal(0)
        igst = (taxable * Decimal("0.18")).quantize(Decimal("0.01"))
    return taxable, cgst, sgst, igst, taxable + cgst + sgst + igst


def wrap(c, text, width, font, size) -> list[str]:
    """Split text into lines that fit `width` at word boundaries."""
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if c.stringWidth(trial, font, size) > width and cur:
            lines.append(cur); cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


# ------------------------------------------------------------------ layouts
def draw_classic(c: canvas.Canvas, inv):
    taxable, cgst, sgst, igst, total = totals(inv)
    m = 18 * mm
    c.setFont("Helvetica-Bold", 16); c.drawString(m, H - 25 * mm, inv["vendor"])
    c.setFont("Helvetica", 9)
    y = H - 31 * mm
    for line in inv["vendor_addr"] + [f"GSTIN: {inv['gstin']}"]:
        c.drawString(m, y, line); y -= 4.5 * mm
    c.setFont("Helvetica-Bold", 20); c.drawRightString(W - m, H - 25 * mm, "TAX INVOICE")
    c.setFont("Helvetica", 9.5)
    meta = [("Invoice No.", inv["number"]), ("Invoice Date", inv["date"]), ("Due Date", inv["due"])]
    if inv["po"]:
        meta.append(("PO Number", inv["po"]))
    y = H - 33 * mm
    for k, v in meta:
        c.drawRightString(W - m - 40 * mm, y, k + ":"); c.drawRightString(W - m, y, v); y -= 5 * mm

    y = H - 62 * mm
    c.setStrokeColor(colors.grey); c.line(m, y + 4 * mm, W - m, y + 4 * mm)
    c.setFont("Helvetica-Bold", 10); c.drawString(m, y - 2 * mm, "Bill To")
    c.setFont("Helvetica", 9.5)
    yy = y - 7 * mm
    for line in [BUYER_NAME] + BUYER_ADDR + [f"GSTIN: {BUYER_GSTIN}"]:
        c.drawString(m, yy, line); yy -= 4.5 * mm

    # table: fixed column anchors (left for text, right edge for numbers)
    y = yy - 6 * mm
    x_no, x_desc, x_hsn = m + 1 * mm, m + 9 * mm, m + 92 * mm
    r_qty, r_rate, r_amt = m + 122 * mm, m + 148 * mm, W - m - 1 * mm
    c.setFillColor(colors.HexColor("#e8e8e8")); c.rect(m, y - 2 * mm, W - 2 * m, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 9)
    c.drawString(x_no, y, "#"); c.drawString(x_desc, y, "Description"); c.drawString(x_hsn, y, "HSN/SAC")
    c.drawRightString(r_qty, y, "Qty"); c.drawRightString(r_rate, y, "Rate"); c.drawRightString(r_amt, y, "Amount")
    c.setFont("Helvetica", 9)
    y -= 8 * mm
    for n, (desc, hsn, q, p) in enumerate(inv["lines"], 1):
        c.drawString(x_no, y, str(n))
        line_y = y
        for chunk in wrap(c, desc, 80 * mm, "Helvetica", 9):
            c.drawString(x_desc, line_y, chunk); line_y -= 4.2 * mm
        c.drawString(x_hsn, y, hsn)
        c.drawRightString(r_qty, y, str(q))
        c.drawRightString(r_rate, y, format_inr(p, symbol=False))
        c.drawRightString(r_amt, y, format_inr(Decimal(q) * Decimal(p), symbol=False))
        y = line_y - 3 * mm
    c.line(m, y + 3 * mm, W - m, y + 3 * mm)

    rows = [("Taxable Value", taxable)]
    if inv["tax"] == "intra":
        rows += [("CGST @ 9%", cgst), ("SGST @ 9%", sgst)]
    else:
        rows += [("IGST @ 18%", igst)]
    for k, v in rows:
        c.drawRightString(W - m - 40 * mm, y - 2 * mm, k); c.drawRightString(W - m - 1 * mm, y - 2 * mm, rs(v)); y -= 5.5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - m - 40 * mm, y - 3 * mm, "Total Amount Due")
    c.drawRightString(W - m - 1 * mm, y - 3 * mm, rs(total))

    y -= 20 * mm
    if inv.get("bank_note"):
        c.setFillColor(colors.HexColor("#b00020")); c.setFont("Helvetica-Bold", 9.5)
        for chunk in wrap(c, inv["bank_note"], W - 2 * m, "Helvetica-Bold", 9.5):
            c.drawString(m, y, chunk); y -= 4.5 * mm
        y -= 3 * mm
        c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10); c.drawString(m, y, "Bank Details for Payment"); y -= 5 * mm
    c.setFont("Helvetica", 9.5)
    bank, ifsc, acct = inv["bank"]
    for line in [f"Bank: {bank}", f"A/c Name: {inv['vendor']}", f"A/c No: {acct}", f"IFSC: {ifsc}"]:
        c.drawString(m, y, line); y -= 4.5 * mm
    c.setFont("Helvetica-Oblique", 8); c.setFillColor(colors.grey)
    c.drawString(m, 15 * mm, "This is a computer generated invoice. Subject to Bengaluru jurisdiction.")


def draw_modern(c: canvas.Canvas, inv):
    taxable, cgst, sgst, igst, total = totals(inv)
    m = 16 * mm
    c.setFillColor(colors.HexColor("#1f3a5f")); c.rect(0, H - 38 * mm, W, 38 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18); c.drawString(m, H - 18 * mm, inv["vendor"].upper())
    c.setFont("Helvetica", 8.5)
    c.drawString(m, H - 24 * mm, ", ".join(inv["vendor_addr"]))
    c.drawString(m, H - 29 * mm, f"GST Reg. No. {inv['gstin']}")
    c.setFont("Helvetica-Bold", 12); c.drawRightString(W - m, H - 18 * mm, "INVOICE")

    c.setFillColor(colors.black)
    y = H - 50 * mm
    c.setFont("Helvetica-Bold", 9); c.drawString(m, y, "BILLED TO")
    c.setFont("Helvetica", 9)
    yy = y - 5 * mm
    for line in [BUYER_NAME, ", ".join(BUYER_ADDR), f"GSTIN {BUYER_GSTIN}"]:
        c.drawString(m, yy, line); yy -= 4.5 * mm
    meta = [("Bill No", inv["number"]), ("Bill Date", inv["date"]), ("Payment Due", inv["due"])]
    if inv["po"]:
        meta.append(("Your Ref / PO", inv["po"]))
    yv = y
    for k, v in meta:
        c.setFont("Helvetica-Bold", 9); c.drawString(W - m - 70 * mm, yv, k)
        c.setFont("Helvetica", 9); c.drawRightString(W - m, yv, v); yv -= 5 * mm

    y = min(yy, yv) - 10 * mm
    c.setFillColor(colors.HexColor("#1f3a5f")); c.rect(m, y - 2.5 * mm, W - 2 * m, 7.5 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 9)
    c.drawString(m + 2 * mm, y, "SERVICE / ITEM"); c.drawString(m + 100 * mm, y, "SAC")
    c.drawRightString(m + 130 * mm, y, "UNITS"); c.drawRightString(W - m - 2 * mm, y, "LINE TOTAL (INR)")
    c.setFillColor(colors.black); c.setFont("Helvetica", 9)
    y -= 9 * mm
    for desc, hsn, q, p in inv["lines"]:
        c.drawString(m + 2 * mm, y, desc)
        c.setFont("Helvetica", 7.5); c.setFillColor(colors.grey)
        c.drawString(m + 2 * mm, y - 4 * mm, f"@ {format_inr(p, symbol=False)} per unit")
        c.setFont("Helvetica", 9); c.setFillColor(colors.black)
        c.drawString(m + 100 * mm, y, hsn); c.drawRightString(m + 130 * mm, y, str(q))
        c.drawRightString(W - m - 2 * mm, y, format_inr(Decimal(q) * Decimal(p), symbol=False))
        y -= 11 * mm

    c.setStrokeColor(colors.HexColor("#1f3a5f")); c.line(m + 90 * mm, y + 4 * mm, W - m, y + 4 * mm)
    rows = [("Sub-total (before tax)", taxable)]
    rows += [("CGST 9%", cgst), ("SGST 9%", sgst)] if inv["tax"] == "intra" else [("IGST 18%", igst)]
    for k, v in rows:
        c.drawString(m + 92 * mm, y - 1 * mm, k); c.drawRightString(W - m - 2 * mm, y - 1 * mm, format_inr(v, symbol=False)); y -= 5.5 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(m + 92 * mm, y - 3 * mm, "GRAND TOTAL"); c.drawRightString(W - m - 2 * mm, y - 3 * mm, rs(total))

    if inv.get("stamp"):
        c.saveState(); c.setFillColor(colors.HexColor("#cc0000")); c.setFont("Helvetica-Bold", 22)
        c.translate(W / 2, H / 2 - 40 * mm); c.rotate(18); c.drawCentredString(0, 0, inv["stamp"]); c.restoreState()

    y = 40 * mm
    bank, ifsc, acct = inv["bank"]
    c.setFont("Helvetica-Bold", 9); c.drawString(m, y, "REMIT TO")
    c.setFont("Helvetica", 9)
    c.drawString(m, y - 5 * mm, f"{bank} | IFSC {ifsc} | Account {acct}")
    c.setFont("Helvetica-Oblique", 8); c.setFillColor(colors.grey)
    c.drawString(m, 15 * mm, "Thank you for your business. Late payments attract 1.5% interest per month.")


# ------------------------------------------------------------------ build
def build_pdf(inv) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(inv["number"]); c.setAuthor(inv["vendor"])
    (draw_classic if inv["layout"] == "classic" else draw_modern)(c, inv)
    c.showPage(); c.save()
    return buf.getvalue()


def make_scanned(pdf_bytes: bytes) -> bytes:
    """Render -> grayscale -> slight rotation, noise, blur -> image-only PDF."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=160 / 72).to_pil().convert("L")
    img = img.rotate(0.8, expand=True, fillcolor=255)
    noise = Image.effect_noise(img.size, 18).point(lambda p: 255 if p > 120 else p)
    img = Image.blend(img, noise, 0.05).filter(ImageFilter.GaussianBlur(0.35))
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PDF", resolution=160)
    return out.getvalue()


def main():
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for name, inv in INVOICES.items():
        data = build_pdf(inv)
        if inv.get("scanned"):
            data = make_scanned(data)
        (SAMPLES_DIR / name).write_bytes(data)
        print(f"wrote samples/{name}  total={rs(totals(inv)[4])}")


if __name__ == "__main__":
    main()
