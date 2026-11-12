"""Stage 4 · Validate: required fields, GSTIN format, arithmetic, GST type, confidence."""
from decimal import Decimal

from core import config
from core.models import RunContext, StepResult
from core.money import close_enough, format_inr
from core.pipeline.normalize import gstin_valid


def validate(ctx: RunContext) -> StepResult:
    x = ctx.extracted
    checks: list[dict] = []

    def check(name, ok, msg):
        checks.append({"check": name, "ok": ok, "detail": msg})
        return ok

    # 1. Required fields
    missing = [f for f in config.CRITICAL_FIELDS if getattr(x, f) is None]
    if not check("required_fields", not missing, f"missing: {missing}" if missing else "all present"):
        ctx.add_flag("MISSING_CRITICAL_FIELD", "Validate", f"Missing {', '.join(missing)}", missing=missing)

    # 2. GSTIN format
    check("vendor_gstin_format", gstin_valid(x.vendor_gstin), x.vendor_gstin or "absent")
    if x.buyer_gstin:
        check("billed_to_us", x.buyer_gstin == config.BUYER_GSTIN,
              f"buyer GSTIN {x.buyer_gstin} vs ours {config.BUYER_GSTIN}")

    # 3. Arithmetic: lines sum to taxable, taxable + tax = total
    tol = config.ROUNDING_TOLERANCE_INR
    line_sum = Decimal("0")
    for li in x.line_items:
        amt = li.amount if li.amount is not None else (
            li.quantity * li.unit_price if li.quantity is not None and li.unit_price is not None else None)
        line_sum += amt or 0
    tax = (x.cgst or 0) + (x.sgst or 0) + (x.igst or 0)
    arith_errors = []
    if x.line_items and x.taxable_value is not None and not close_enough(line_sum, x.taxable_value, tol):
        arith_errors.append(f"lines {format_inr(line_sum)} ≠ taxable {format_inr(x.taxable_value)}")
    if x.taxable_value is not None and x.total_amount is not None and \
            not close_enough(x.taxable_value + tax, x.total_amount, tol):
        arith_errors.append(f"taxable + tax {format_inr(x.taxable_value + tax)} ≠ total {format_inr(x.total_amount)}")
    if ctx.extraction_issues:
        arith_errors = []          # reported once, below, as untrusted amounts
    if not check("arithmetic", not arith_errors, "; ".join(arith_errors) or "lines, tax and total reconcile"):
        ctx.add_flag("ARITHMETIC_MISMATCH", "Validate", "; ".join(arith_errors), errors=arith_errors)

    # 3b. Amounts that still don't reconcile after the AI re-read can't be trusted
    if ctx.extraction_issues:
        check("amounts_trustworthy", False, "; ".join(ctx.extraction_issues))
        ctx.add_flag("LOW_CONFIDENCE", "Validate",
                     "Amounts don't reconcile even after an AI re-read; money checks skipped",
                     issues=ctx.extraction_issues)

    # 4. GST type and rate (intra-state: CGST+SGST 9%+9%, inter-state: IGST 18%)
    if gstin_valid(x.vendor_gstin) and x.taxable_value and not ctx.extraction_issues:
        intra = x.vendor_gstin[:2] == config.BUYER_STATE
        half = (x.taxable_value * config.GST_RATE / 2).quantize(Decimal("0.01"))
        full = (x.taxable_value * config.GST_RATE).quantize(Decimal("0.01"))
        if intra:
            ok = close_enough(x.cgst or 0, half, tol) and close_enough(x.sgst or 0, half, tol) and not (x.igst or 0)
            expect = f"intra-state: CGST {format_inr(half)} + SGST {format_inr(half)}"
        else:
            ok = close_enough(x.igst or 0, full, tol) and not (x.cgst or 0) and not (x.sgst or 0)
            expect = f"inter-state: IGST {format_inr(full)}"
        if not check("gst_type_rate", ok, expect):
            ctx.add_flag("TAX_TYPE_MISMATCH", "Validate", f"Expected {expect}",
                         cgst=x.cgst, sgst=x.sgst, igst=x.igst)

    # 5. Confidence on critical fields
    low = {f: c for f, c in ctx.effective_conf.items() if c < config.MIN_FIELD_CONFIDENCE
           and getattr(x, f) is not None}
    if not check("confidence", not low, f"low: {low}" if low else "all critical fields ≥ "
                 f"{config.MIN_FIELD_CONFIDENCE}"):
        ctx.add_flag("LOW_CONFIDENCE", "Validate", f"Low confidence on {', '.join(low)}", fields=low)

    failed = [c["check"] for c in checks if not c["ok"]]
    status = "WARN" if failed else "OK"
    summary = "All checks passed" if not failed else f"{len(failed)} check(s) failed: {', '.join(failed)}"
    return StepResult(status, summary, {"checks": checks})
