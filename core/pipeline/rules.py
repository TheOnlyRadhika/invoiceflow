"""Stage 8 · Rules & decision.

THE ONLY PLACE A DECISION IS MADE (Golden rule 1).
Pure functions, no LLM, no I/O except reading ctx -> easy to unit test and explain.

Flow:  flags from earlier stages  +  flags computed here (price variance, PO capacity)
       -> map each flag to a decision via config.REASON_CODES
       -> final status = most severe (REJECT > HUMAN_REVIEW > HOLD > APPROVE_WITH_NOTE > APPROVE)
"""
from decimal import Decimal

from core import config
from core.models import Decision, Flag, RunContext, StepResult
from core.money import format_inr, pct_diff, to_decimal


# ------------------------------------------------------------------ rule checks (pure)
def line_variance_flags(line_matches: list[dict]) -> list[Flag]:
    """Unit price above PO price: >3% HOLD, 1-3% note. Lines not on the PO: HOLD."""
    flags = []
    for m in line_matches:
        if not m["matched"]:
            flags.append(Flag("UNMATCHED_LINE", "Rules",
                              f"Line {m['invoice_line']} '{m['description']}' is not on the PO",
                              {"line": m["invoice_line"], "description": m["description"]}))
            continue
        if m["price_variance_pct"] is None:
            continue
        v = Decimal(m["price_variance_pct"])
        ev = {"line": m["invoice_line"], "invoice_price": m["unit_price"], "po_price": m["po_unit_price"], "variance_pct": str(v)}
        if v > config.PRICE_VARIANCE_NOTE_PCT:
            flags.append(Flag("PRICE_VARIANCE_HIGH", "Rules", f"Line {m['invoice_line']} price +{v}% over PO", ev))
        elif v > config.PRICE_VARIANCE_OK_PCT:
            flags.append(Flag("PRICE_VARIANCE_MINOR", "Rules", f"Line {m['invoice_line']} price +{v}% over PO", ev))
    return flags


def po_capacity(po_total, billed, taxable) -> dict:
    """Cumulative check: does this invoice push total billing past the PO value?"""
    po_total, billed, taxable = to_decimal(po_total), to_decimal(billed), to_decimal(taxable)
    after = billed + taxable
    limit = po_total * (1 + config.PO_OVERRUN_TOLERANCE_PCT / 100)
    remaining = max(po_total - billed, Decimal("0"))
    return {"po_total": po_total, "billed_to_date": billed, "this_invoice": taxable,
            "after_this_invoice": after, "remaining_before": remaining,
            "overrun_pct": pct_diff(after, po_total) if after > po_total else Decimal("0"),
            "overrun": after > limit}


def collect_flags(ctx: RunContext) -> tuple[list[Flag], dict]:
    """Earlier-stage flags + flags computed here. Returns (flags, capacity_info)."""
    flags = list(ctx.flags)
    cap = None
    if ctx.extraction_issues:
        pass   # don't run money rules on numbers we can't trust (LOW_CONFIDENCE already raised)
    elif ctx.po and ctx.extracted and ctx.extracted.taxable_value is not None:
        cap = po_capacity(ctx.po["total_taxable"], ctx.billed_to_date, ctx.extracted.taxable_value)
        if cap["overrun"]:
            flags.append(Flag("PO_OVERRUN", "Rules",
                              f"Billed {format_inr(cap['billed_to_date'])} + this {format_inr(cap['this_invoice'])} "
                              f"= {format_inr(cap['after_this_invoice'])} > PO {format_inr(cap['po_total'])}",
                              {k: str(v) for k, v in cap.items()}))
        flags += line_variance_flags(ctx.line_matches)
    elif ctx.extracted and not ctx.po and not any(f.code in ("PO_NOT_FOUND", "VENDOR_UNKNOWN", "VENDOR_BLOCKED")
                                                  for f in flags):
        flags.append(Flag("PO_NOT_FOUND", "Rules", "Invoice could not be tied to a PO"))
    return flags, cap


# ------------------------------------------------------------------ decision (pure)
def decide(flags: list[Flag], cap: dict | None = None) -> Decision:
    codes = []
    for f in flags:                                   # unique, keep first occurrence
        if f.code not in codes:
            codes.append(f.code)
    if not codes:
        return Decision(status="APPROVE", reason_codes=[], headline="All checks passed: ready to pay")

    rank = {s: i for i, s in enumerate(config.DECISION_PRECEDENCE)}
    codes.sort(key=lambda c: rank[config.REASON_CODES[c][0]])
    status = config.REASON_CODES[codes[0]][0]
    top = next(f for f in flags if f.code == codes[0])

    approvable = None
    if "PO_OVERRUN" in codes and cap:
        approvable = cap["remaining_before"]
    headline = top.message
    return Decision(status=status, reason_codes=codes, headline=headline, approvable_amount=approvable)


def build_evidence(ctx: RunContext, flags: list[Flag], cap: dict | None) -> dict:
    x = ctx.extracted
    ev = {
        "invoice": {"number": x.invoice_number, "date": x.invoice_date, "vendor": x.vendor_name,
                    "taxable_value": x.taxable_value, "tax": (x.cgst or 0) + (x.sgst or 0) + (x.igst or 0),
                    "total": x.total_amount} if x else None,
        "vendor_id": ctx.vendor["vendor_id"] if ctx.vendor else None,
        "po": {"po_id": ctx.po["po_id"], "method": ctx.match_method, "confidence": ctx.match_confidence}
        if ctx.po else None,
        "capacity": {k: str(v) for k, v in cap.items()} if cap else None,
        "duplicate_of": ctx.duplicate_of,
        "flags": [{"code": f.code, "stage": f.stage, "message": f.message, "evidence": f.evidence} for f in flags],
    }
    return ev


def decide_stage(ctx: RunContext) -> StepResult:
    flags, cap = collect_flags(ctx)
    d = decide(flags, cap)
    d.evidence = build_evidence(ctx, flags, cap)
    ctx.decision = d
    status = {"APPROVE": "OK", "APPROVE_WITH_NOTE": "OK"}.get(d.status, "WARN")
    return StepResult(status, f"{d.status} · {', '.join(d.reason_codes) or 'no exceptions'}",
                      {"decision": d.status, "reason_codes": d.reason_codes, "headline": d.headline,
                       "approvable_amount": str(d.approvable_amount) if d.approvable_amount is not None else None,
                       "rules_fired": [{"code": c, "maps_to": config.REASON_CODES[c][0],
                                        "label": config.REASON_CODES[c][1]} for c in d.reason_codes]})
