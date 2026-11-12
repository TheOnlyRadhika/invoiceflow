"""Stage 7 · Duplicate check: exact (hash / normalized number) and possible (amount+date)."""
from datetime import date

from core import config, db
from core.models import RunContext, StepResult
from core.money import close_enough


def _days_apart(a: str | None, b: str | None) -> int | None:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def _brief(inv: dict) -> dict:
    return {k: inv.get(k) for k in ("invoice_number", "invoice_date", "po_id", "total_amount",
                                    "status", "source", "run_id")}


def duplicate_check(ctx: RunContext) -> StepResult:
    hit = db.find_invoice_by_hash(ctx.file_hash)
    if hit:
        ctx.duplicate_of = _brief(hit)
        ctx.add_flag("DUPLICATE_EXACT", "Duplicate check", "Same file already processed", original=ctx.duplicate_of)
        return StepResult("WARN", f"Exact duplicate: same file as {hit['invoice_number']}", {"original": ctx.duplicate_of, "match": "file_hash"})

    if not ctx.vendor:
        return StepResult("SKIP", "Number check skipped: vendor unknown (file hash checked)", {})

    x = ctx.extracted
    vid = ctx.vendor["vendor_id"]
    if ctx.invoice_number_norm:
        hit = db.find_invoice_by_number(vid, ctx.invoice_number_norm)
        if hit:
            ctx.duplicate_of = _brief(hit)
            ctx.add_flag("DUPLICATE_EXACT", "Duplicate check",
                         f"Invoice '{x.invoice_number}' matches already-recorded '{hit['invoice_number']}'",
                         original=ctx.duplicate_of, normalized=ctx.invoice_number_norm)
            return StepResult("WARN", f"Exact duplicate of {hit['invoice_number']} ({hit['status']})",
                              {"original": ctx.duplicate_of, "match": "vendor + normalized invoice number",
                               "normalized": ctx.invoice_number_norm})

    for inv in db.invoices_for_vendor(vid):
        days = _days_apart(inv["invoice_date"], x.invoice_date)
        if close_enough(inv["total_amount"], x.total_amount, config.ROUNDING_TOLERANCE_INR) \
                and days is not None and days <= config.DUP_DATE_WINDOW_DAYS:
            ctx.duplicate_of = _brief(inv)
            ctx.add_flag("DUPLICATE_POSSIBLE", "Duplicate check",
                         f"Same amount within {days} day(s) of {inv['invoice_number']}", original=ctx.duplicate_of)
            return StepResult("WARN", f"Possible duplicate of {inv['invoice_number']} (same total, {days}d apart)",
                              {"original": ctx.duplicate_of, "match": "vendor + amount + date window"})

    return StepResult("OK", "No duplicates found", {"checked": ["file_hash", "invoice_number", "amount+date"]})
