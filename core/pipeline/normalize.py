"""Stage 3 · Normalize: clean dates, IDs, bank details; compute effective confidence."""
import re
from datetime import datetime

from core import config
from core.db import norm_invoice_no
from core.models import RunContext, StepResult

DATE_FORMATS = ["%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y",
                "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d %b, %Y"]


def norm_date(s: str | None) -> str | None:
    """Day-first (Indian) parsing. Returns ISO YYYY-MM-DD or None."""
    if not s:
        return None
    s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s.strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def norm_gstin(s: str | None) -> str | None:
    return re.sub(r"\s+", "", s).upper() if s else None


def norm_po(s: str | None) -> str | None:
    """'PO 2026/0101', 'po-2026-0101' -> 'PO-2026-0101'. Unknown shapes are kept as-is."""
    if not s:
        return None
    m = re.search(r"PO\W*(\d{4})\W*(\d{3,5})", s.upper())
    return f"PO-{m.group(1)}-{int(m.group(2)):04d}" if m else s.strip().upper()


def digits(s: str | None) -> str | None:
    d = re.sub(r"\D", "", s or "")
    return d or None


def gstin_valid(g: str | None) -> bool:
    return bool(g and re.match(config.GSTIN_REGEX, g))


def normalize(ctx: RunContext) -> StepResult:
    x = ctx.extracted
    changes, warns = {}, []

    for field in ("invoice_date", "due_date"):
        raw = getattr(x, field)
        iso = norm_date(raw)
        if raw and not iso:
            warns.append(f"{field} '{raw}' unparseable")
        if raw != iso:
            changes[field] = f"{raw} → {iso}"
        setattr(x, field, iso)

    for field, fn in (("vendor_gstin", norm_gstin), ("buyer_gstin", norm_gstin), ("po_reference", norm_po),
                      ("bank_ifsc", lambda v: v.replace(" ", "").upper() if v else None),
                      ("bank_account_number", digits)):
        raw = getattr(x, field)
        new = fn(raw)
        if raw != new:
            changes[field] = f"{raw} → {new}"
        setattr(x, field, new)

    ctx.invoice_number_norm = norm_invoice_no(x.invoice_number)

    # Effective confidence = min(LLM's own confidence, our format check)
    fmt_ok = {
        "invoice_number": x.invoice_number is not None,
        "invoice_date": x.invoice_date is not None,
        "vendor_gstin": gstin_valid(x.vendor_gstin),
        "taxable_value": x.taxable_value is not None and x.taxable_value > 0,
        "total_amount": x.total_amount is not None and x.total_amount > 0,
    }
    for f in config.CRITICAL_FIELDS:
        llm_conf = x.field_confidence.get(f, 0.85)
        ctx.effective_conf[f] = round(min(llm_conf, 1.0 if fmt_ok[f] else 0.4), 2)

    status = "WARN" if warns else "OK"
    summary = f"{len(changes)} field(s) normalized" + (f" · {len(warns)} warning(s)" if warns else "")
    return StepResult(status, summary, {"changes": changes, "warnings": warns,
                                        "invoice_number_norm": ctx.invoice_number_norm,
                                        "effective_confidence": ctx.effective_conf})
