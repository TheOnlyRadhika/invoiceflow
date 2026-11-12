"""Stage 9 · Explain (LLM agent C): plain-English narrative from the decision + evidence.
Guardrail: if the LLM fails or contradicts the decision, fall back to a template."""
import re

from core import config, db, llm, prompts
from core.models import RunContext, StepResult
from core.money import format_inr

NEXT_STEP = {
    "APPROVE": "Post to ledger and schedule payment on the due date.",
    "APPROVE_WITH_NOTE": "Post to ledger and schedule payment; the note is kept for audit.",
    "HOLD": "Payment is on hold until the vendor sends what is requested.",
    "HUMAN_REVIEW": "An AP reviewer must check this invoice before anything is paid.",
    "REJECT": "Do not pay. The vendor has been notified.",
}


def template_explanation(ctx: RunContext) -> str:
    d = ctx.decision
    parts = []
    if d.status == "APPROVE":
        parts.append("The invoice matches an approved vendor and its purchase order, the totals and GST reconcile, "
                     "and no duplicate was found.")
    for code in d.reason_codes:
        f = next((f for f in d.evidence.get("flags", []) if f["code"] == code), None)
        msg = f["message"] if f else config.REASON_CODES[code][1]
        parts.append(msg.rstrip(".") + ".")
    if d.approvable_amount is not None:
        parts.append(f"Up to {format_inr(d.approvable_amount)} remains on the PO and could be approved.")
    parts.append(NEXT_STEP[d.status])
    return " ".join(parts)


def _llm_explain(ctx: RunContext) -> str:
    d = ctx.decision
    user = prompts.EXPLAIN_USER.format(status=d.status, codes=", ".join(d.reason_codes) or "none",
                                       headline=d.headline, evidence=db.dumps(d.evidence)[:6000])
    return llm.chat_text(prompts.EXPLAIN_SYSTEM, user)


KEYWORDS = {"APPROVE": r"\bAPPROVED?\b", "APPROVE_WITH_NOTE": r"\bAPPROVED?\b",
            "HOLD": r"\bON HOLD\b|\bHOLD\b", "HUMAN_REVIEW": r"\bREVIEW", "REJECT": r"\bREJECT"}


def _contradicts(text: str, status: str) -> bool:
    """True if the LLM text never mentions the real decision, or promises payment on a blocked invoice."""
    t = text.upper()
    if status in ("HOLD", "HUMAN_REVIEW", "REJECT") and re.search(r"APPROVED FOR PAYMENT|WILL BE PAID|READY TO PAY", t):
        return True
    return not re.search(KEYWORDS[status], t)


def explain(ctx: RunContext) -> StepResult:
    source = "llm"
    try:
        text = _llm_explain(ctx)
        if not text or len(text) < 40 or _contradicts(text, ctx.decision.status):
            raise ValueError("empty or contradicting explanation")
    except Exception as e:
        text, source = template_explanation(ctx), f"template ({str(e)[:80]})"
    ctx.explanation = text
    return StepResult("OK", f"Explanation written ({source.split(' ')[0]})", {"source": source, "text": text})
