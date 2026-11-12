"""Orchestrator: runs the 10 stages in order, yields live StepEvents, persists everything.

Usage (UI or CLI):
    for event in run_pipeline("inv.pdf", pdf_bytes):
        render(event)            # event.status RUNNING -> OK/WARN/FAIL/SKIP
"""
import time
from typing import Iterator

from core import db
from core.models import RunContext, StepEvent, StepResult
from core.pipeline.actions import act
from core.pipeline.duplicate import duplicate_check
from core.pipeline.explain import explain
from core.pipeline.extract import extract
from core.pipeline.ingest import ingest
from core.pipeline.normalize import normalize
from core.pipeline.po_match import po_match
from core.pipeline.rules import decide_stage
from core.pipeline.validate import validate
from core.pipeline.vendor import vendor_check

# (display name, function, needs extracted data?)
STAGES = [
    ("Ingest", ingest, False),
    ("Extract", extract, False),
    ("Normalize", normalize, True),
    ("Validate", validate, True),
    ("Vendor check", vendor_check, True),
    ("PO match", po_match, True),
    ("Duplicate check", duplicate_check, False),
    ("Rules & decision", decide_stage, False),
    ("Explain", explain, False),
    ("Action", act, False),
]
STAGE_NAMES = [s[0] for s in STAGES]


def run_pipeline(filename: str, file_bytes: bytes, ctx_out: list | None = None) -> Iterator[StepEvent]:
    """Generator. Pass ctx_out=[] to receive the final RunContext (ctx_out[0])."""
    db.init_db()
    ctx = RunContext.new(filename, file_bytes)
    if ctx_out is not None:
        ctx_out.append(ctx)
    db.insert_run(ctx.run_id, filename, ctx.started_at)
    t_run = time.time()

    for seq, (name, fn, needs_data) in enumerate(STAGES, 1):
        yield StepEvent(run_id=ctx.run_id, seq=seq, stage=name, status="RUNNING", summary="Running…")
        t0 = time.time()
        if needs_data and ctx.extracted is None:
            res = StepResult("SKIP", "Skipped: no extracted data", {})
        elif name == "Duplicate check" and ctx.file_hash is None:
            res = StepResult("SKIP", "Skipped: file not ingested", {})
        else:
            try:
                res = fn(ctx)
            except Exception as e:                       # a stage must never crash the run
                res = StepResult("FAIL", f"{name} failed: {type(e).__name__}: {str(e)[:160]}", {})
                if name not in ("Explain", "Action"):
                    ctx.add_flag("STAGE_FAILED", name, f"{name} failed: {e}")
        ms = int((time.time() - t0) * 1000)
        db.insert_step(ctx.run_id, seq, name, res.status, ms, res.summary, res.detail)
        yield StepEvent(run_id=ctx.run_id, seq=seq, stage=name, status=res.status,
                        summary=res.summary, detail=res.detail, duration_ms=ms)
        if name == "Ingest" and res.status == "FAIL":
            ctx.add_flag("EXTRACTION_FAILED", "Ingest", res.summary)
            # still run Rules/Explain/Action so the run gets a decision
            for seq2, (n2, f2, _) in enumerate(STAGES[7:], 8):
                yield StepEvent(run_id=ctx.run_id, seq=seq2, stage=n2, status="RUNNING", summary="Running…")
                r2 = f2(ctx)
                db.insert_step(ctx.run_id, seq2, n2, r2.status, 0, r2.summary, r2.detail)
                yield StepEvent(run_id=ctx.run_id, seq=seq2, stage=n2, status=r2.status,
                                summary=r2.summary, detail=r2.detail)
            break

    _finalize(ctx, int((time.time() - t_run) * 1000))


def _finalize(ctx: RunContext, duration_ms: int) -> None:
    d, x = ctx.decision, ctx.extracted
    needs_review = d.status in ("HOLD", "HUMAN_REVIEW")
    db.finalize_run(
        ctx.run_id, file_hash=ctx.file_hash, duration_ms=duration_ms, decision=d.status,
        reason_codes=d.reason_codes, headline=d.headline,
        extracted=x.model_dump(mode="json") if x else {}, evidence=d.evidence | {
            "approvable_amount": str(d.approvable_amount) if d.approvable_amount is not None else None},
        explanation=ctx.explanation, action=ctx.action or {},
        vendor_id=(ctx.vendor or {}).get("vendor_id"),
        vendor_name=(ctx.vendor or {}).get("name") or (x.vendor_name if x else None),
        invoice_number=x.invoice_number if x else None,
        total_amount=x.total_amount if x else None,
        po_id=(ctx.po or {}).get("po_id"), is_scanned=int(ctx.is_scanned),
        review_status="PENDING" if needs_review else "NONE",
    )
    # Record the invoice so future duplicates / PO billing see it. Rejected ones are not recorded.
    if x and d.status != "REJECT":
        db.insert_invoice(run_id=ctx.run_id, vendor_id=(ctx.vendor or {}).get("vendor_id"),
                          invoice_number=x.invoice_number, invoice_number_norm=ctx.invoice_number_norm,
                          invoice_date=x.invoice_date, po_id=(ctx.po or {}).get("po_id"),
                          taxable_value=x.taxable_value, total_amount=x.total_amount,
                          file_hash=ctx.file_hash, status=d.status)


def apply_review(run_id: str, review_status: str, note: str) -> None:
    """Human override from the Review Queue. Updates run + invoice status (affects PO billing)."""
    db.update_review(run_id, review_status, note)
    new_invoice_status = {"OVERRIDDEN_APPROVE": "APPROVE", "OVERRIDDEN_REJECT": "REJECTED_BY_REVIEW"}.get(review_status)
    if new_invoice_status:
        db.update_invoice_status_for_run(run_id, new_invoice_status)
