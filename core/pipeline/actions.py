"""Stage 10 · Action (LLM agent D drafts, code decides WHAT action happens).
APPROVE*      -> mock ledger entry
HOLD          -> vendor email draft + review queue
HUMAN_REVIEW  -> review queue (+ internal fraud alert if bank changed; vendor is NOT emailed)
REJECT        -> vendor notice draft"""
import json
from datetime import date, timedelta

from core import config, db, llm, prompts
from core.models import RunContext, StepResult
from core.money import format_inr

GL_RULES = [("chair", "1520 · Furniture & Fixtures"), ("desk", "1520 · Furniture & Fixtures"),
            ("hosting", "6110 · Cloud & IT Services"), ("freight", "6320 · Freight Outward"),
            ("paper", "6050 · Office Supplies"), ("labour", "6330 · Warehouse Labour")]
SIGN_OFF = "Accounts Payable\nZenith Retail Pvt Ltd"


def gl_code(ctx: RunContext) -> str:
    text = " ".join((li.description or "") for li in ctx.extracted.line_items).lower()
    return next((gl for kw, gl in GL_RULES if kw in text), "6999 · Unclassified (review)")


def ledger_entry(ctx: RunContext) -> dict:
    x = ctx.extracted
    due = x.due_date or (date.today() + timedelta(days=30)).isoformat()
    return {"type": "LEDGER_POST", "status": "POSTED (mock)", "vendor_id": ctx.vendor["vendor_id"],
            "invoice_number": x.invoice_number, "po_id": ctx.po["po_id"] if ctx.po else None,
            "gl_code": gl_code(ctx), "taxable": str(x.taxable_value),
            "input_tax_credit": str((x.cgst or 0) + (x.sgst or 0) + (x.igst or 0)),
            "payable": str(x.total_amount), "payment_due": due,
            "note": ctx.decision.headline if ctx.decision.status == "APPROVE_WITH_NOTE" else None}


def _llm_draft(purpose: str, context: dict) -> dict:
    data, _ = llm.chat_json(prompts.DRAFT_VENDOR_SYSTEM,
                            prompts.DRAFT_VENDOR_USER.format(purpose=purpose, context=db.dumps(context)))
    if not data.get("subject") or not data.get("body"):
        raise ValueError("draft missing subject/body")
    return {"subject": data["subject"], "body": data["body"]}


def _template_hold(ctx: RunContext) -> dict:
    x, d = ctx.extracted, ctx.decision
    issues = "\n".join(f"- {f['message']}" for f in d.evidence["flags"]
                       if config.REASON_CODES[f["code"]][0] == "HOLD")
    ask = ""
    if d.approvable_amount is not None:
        ask = (f"\nThe PO has {format_inr(d.approvable_amount)} remaining. Please send either a revised invoice "
               f"for {format_inr(d.approvable_amount)}, or the PO amendment approved by our procurement team "
               f"covering the difference.")
    return {"subject": f"Invoice {x.invoice_number} on hold: action needed",
            "body": f"Dear {x.vendor_name},\n\nYour invoice {x.invoice_number} dated {x.invoice_date} "
                    f"({format_inr(x.total_amount)}) is on hold for the following reason(s):\n{issues}\n{ask}\n\n"
                    f"We will release payment as soon as this is resolved.\n\nRegards,\n{SIGN_OFF}"}


def _template_reject(ctx: RunContext) -> dict:
    x, d = ctx.extracted, ctx.decision
    if "DUPLICATE_EXACT" in d.reason_codes and ctx.duplicate_of:
        o = ctx.duplicate_of
        reason = (f"it duplicates invoice {o['invoice_number']} dated {o['invoice_date']}, which is already "
                  f"recorded in our system (status: {o['status']}). No further action is needed from you.")
    elif "VENDOR_BLOCKED" in d.reason_codes:
        reason = "your vendor account is not currently approved with us. Please contact our procurement team."
    else:
        reason = d.headline
    return {"subject": f"Invoice {x.invoice_number if x else ''} not processed",
            "body": f"Dear {(x.vendor_name if x else 'Vendor')},\n\nWe could not process invoice "
                    f"{x.invoice_number if x else ''} because {reason}\n\nRegards,\n{SIGN_OFF}"}


def internal_alert(ctx: RunContext) -> dict:
    f = next(f for f in ctx.flags if f.code == "BANK_DETAILS_CHANGED")
    v = ctx.vendor
    return {"to": "AP Lead (internal)", "priority": "HIGH",
            "subject": f"⚠ Bank detail change on {v['name']} invoice {ctx.extracted.invoice_number}: payment blocked",
            "body": (f"Invoice {ctx.extracted.invoice_number} ({format_inr(ctx.extracted.total_amount)}) asks for "
                     f"payment to a NEW account:\n  On invoice: A/c {f.evidence['on_invoice']['account']}, "
                     f"IFSC {f.evidence['on_invoice']['ifsc']}\n  On file:    A/c {f.evidence['on_file']['account']}, "
                     f"IFSC {f.evidence['on_file']['ifsc']}\n\nThis is a common invoice-fraud pattern. Do NOT pay "
                     f"and do NOT reply to the email this invoice came from.\nVerify by calling the vendor on the "
                     f"number already on file: {v['contact_phone']}.\nOnly update the vendor master after a "
                     f"verified call-back and a cancelled cheque on letterhead.")}


def act(ctx: RunContext) -> StepResult:
    d = ctx.decision
    action: dict = {"decision": d.status}
    drafted_by = "template"

    if d.status in ("APPROVE", "APPROVE_WITH_NOTE"):
        action.update(kind="LEDGER_POST", ledger=ledger_entry(ctx))
        summary = f"Posted to ledger (mock) · {action['ledger']['gl_code']} · pay by {action['ledger']['payment_due']}"
    elif d.status == "HOLD":
        ctx_info = {"vendor": ctx.extracted.vendor_name, "invoice": ctx.extracted.invoice_number,
                    "invoice_date": ctx.extracted.invoice_date, "total": str(ctx.extracted.total_amount),
                    "reasons": [f["message"] for f in d.evidence["flags"]],
                    "po": d.evidence.get("po"), "capacity": d.evidence.get("capacity"),
                    "approvable_amount": str(d.approvable_amount) if d.approvable_amount is not None else None}
        try:
            email = _llm_draft("Invoice is ON HOLD. Explain why and exactly what the vendor must send.", ctx_info)
            drafted_by = "llm"
        except Exception:
            email = _template_hold(ctx)
        action.update(kind="VENDOR_EMAIL_DRAFT", to=ctx.vendor["contact_email"], email=email, queued_for_review=True)
        summary = f"Vendor email drafted ({drafted_by}) · queued for AP review"
    elif d.status == "HUMAN_REVIEW":
        if "BANK_DETAILS_CHANGED" in d.reason_codes:
            action.update(kind="INTERNAL_FRAUD_ALERT", alert=internal_alert(ctx), queued_for_review=True)
            summary = "Payment blocked · internal fraud alert raised · vendor NOT emailed"
        else:
            action.update(kind="REVIEW_QUEUE", queued_for_review=True,
                          note=f"Reviewer to check: {', '.join(d.reason_codes)}")
            summary = "Queued for human review"
    else:  # REJECT
        try:
            if ctx.extracted is None:
                raise ValueError("no data")
            email = _template_reject(ctx)           # rejection wording stays deterministic
        except Exception:
            email = {"subject": "Invoice not processed", "body": d.headline}
        action.update(kind="VENDOR_NOTICE_DRAFT", to=(ctx.vendor or {}).get("contact_email"), email=email)
        summary = "Rejection notice drafted · nothing posted"

    ctx.action = action
    return StepResult("OK", summary, json.loads(db.dumps(action)))
