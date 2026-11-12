"""Stage 2 · Extract (LLM agent A): invoice text/images -> ExtractedInvoice.

Self-check: after the first read, code verifies the numbers (lines = taxable,
taxable + tax = total, tax ≈ 18%). If they don't fit, the model is asked to
re-read with the specific inconsistency. The better of the two reads is kept.
The LLM only reads. It never decides (Golden rule 1)."""
from decimal import Decimal

from core import config, db, llm, prompts
from core.models import ExtractedInvoice, RunContext, StepResult
from core.money import close_enough, format_inr


def consistency_issues(x: ExtractedInvoice) -> list[str]:
    """Numeric sanity checks on a reading. Empty list = consistent."""
    issues = []
    tol = config.ROUNDING_TOLERANCE_INR
    tax = (x.cgst or 0) + (x.sgst or 0) + (x.igst or 0)
    amounts = [li.amount for li in x.line_items if li.amount is not None]
    if amounts and x.taxable_value is not None and not close_enough(sum(amounts), x.taxable_value, tol):
        issues.append(f"line amounts sum to {format_inr(sum(amounts))} but taxable value is {format_inr(x.taxable_value)}")
    if x.taxable_value and x.total_amount is not None and not close_enough(x.taxable_value + tax, x.total_amount, tol):
        issues.append(f"taxable {format_inr(x.taxable_value)} + tax {format_inr(tax)} ≠ total {format_inr(x.total_amount)}")
    if x.taxable_value and tax:
        expected = (x.taxable_value * config.GST_RATE).quantize(Decimal("0.01"))
        if not close_enough(tax, expected, tol):
            rate = (Decimal(tax) / x.taxable_value * 100).quantize(Decimal("0.01"))
            issues.append(f"total GST {format_inr(tax)} is {rate}% of taxable {format_inr(x.taxable_value)}; "
                          f"expected 18% ({format_inr(expected)})")
    for li in x.line_items:
        if li.quantity is not None and li.unit_price is not None and li.amount is not None \
                and not close_enough(li.quantity * li.unit_price, li.amount, tol):
            issues.append(f"line '{li.description}': {li.quantity} × {format_inr(li.unit_price)} ≠ {format_inr(li.amount)}")
    return issues


def _llm_extract(ctx: RunContext, recheck: str | None = None) -> tuple[dict, dict]:
    """Isolated so tests can mock it. Returns (extracted_dict, meta)."""
    if ctx.is_scanned:
        user = recheck or prompts.EXTRACT_USER_VISION
        return llm.chat_vision_json(prompts.EXTRACT_SYSTEM, user, ctx.page_images, schema=ExtractedInvoice)
    user = prompts.EXTRACT_USER_TEXT.format(text=ctx.text[:12000])
    if recheck:
        user += "\n\n" + recheck
    return llm.chat_json(prompts.EXTRACT_SYSTEM, user, schema=ExtractedInvoice)


def extract(ctx: RunContext) -> StepResult:
    self_check = None
    cached = db.get_cached_extraction(ctx.file_hash)
    if cached:
        ctx.extracted = ExtractedInvoice.model_validate(cached)
        meta = {"model": "cache", "attempts": 0, "latency_ms": 0, "cache_hit": True}
    else:
        try:
            data, meta = _llm_extract(ctx)
            ctx.extracted = ExtractedInvoice.model_validate(data)
        except Exception as e:
            ctx.add_flag("EXTRACTION_FAILED", "Extract", f"Could not extract invoice data: {e}")
            return StepResult("FAIL", "Extraction failed. Routed to a human.", {"error": str(e)[:300]})

        # ---- self-check: re-read once if the numbers don't fit together
        issues = consistency_issues(ctx.extracted)
        if issues:
            self_check = {"first_read_issues": issues, "first_read": {
                k: str(getattr(ctx.extracted, k)) for k in ("taxable_value", "cgst", "sgst", "igst", "total_amount")}}
            try:
                recheck = prompts.EXTRACT_RECHECK.format(
                    issues="\n".join(f"- {i}" for i in issues),
                    first=db.dumps(ctx.extracted.model_dump(mode="json"))[:3000])
                data2, meta2 = _llm_extract(ctx, recheck=recheck)
                second = ExtractedInvoice.model_validate(data2)
                issues2 = consistency_issues(second)
                meta["latency_ms"] = (meta.get("latency_ms") or 0) + (meta2.get("latency_ms") or 0)
                if len(issues2) < len(issues):
                    ctx.extracted = second
                    self_check.update(outcome="corrected on re-read" if not issues2 else "partly corrected on re-read",
                                      remaining_issues=issues2)
                else:
                    self_check.update(outcome="re-read did not resolve; kept first read", remaining_issues=issues)
            except Exception as e:
                self_check.update(outcome=f"re-read failed: {str(e)[:120]}", remaining_issues=issues)
        db.put_cached_extraction(ctx.file_hash, ctx.extracted.model_dump(mode="json"))
        meta["cache_hit"] = False

    x = ctx.extracted
    # Scans: numbers that don't reconcile may be misreads -> untrusted.
    # Digital PDFs: text is exact, so a mismatch is the vendor's error -> Validate raises ARITHMETIC_MISMATCH.
    ctx.extraction_issues = consistency_issues(x) if ctx.is_scanned else []
    found = sum(1 for f in ("invoice_number", "invoice_date", "vendor_name", "vendor_gstin",
                            "po_reference", "taxable_value", "total_amount") if getattr(x, f) is not None)
    path = "vision" if ctx.is_scanned else "text"
    model = config.VISION_MODEL if ctx.is_scanned else config.TEXT_MODEL
    summary = (f"{x.vendor_name or 'Unknown vendor'} · {x.invoice_number or 'no invoice no.'} · "
               f"{len(x.line_items)} line(s) · {found}/7 key fields"
               + (" · cached" if meta.get("cache_hit") else "")
               + (f" · self-check: {self_check['outcome']}" if self_check else ""))
    status = "WARN" if self_check and self_check.get("remaining_issues") else "OK"
    return StepResult(status, summary, {
        "path": path, "model": model if not meta.get("cache_hit") else "cache",
        "latency_ms": meta.get("latency_ms"), "attempts": meta.get("attempts"),
        "self_check": self_check, "extracted": x.model_dump(mode="json"),
    })
