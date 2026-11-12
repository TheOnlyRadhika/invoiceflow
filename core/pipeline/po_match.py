"""Stage 6 · PO match.
Explicit PO reference -> use it. Otherwise the PO inference AGENT (LLM + tools)
proposes a PO, and deterministic scoring verifies it (agent can't overrule math)."""
from decimal import Decimal

from core import config, db, fuzzy, llm, prompts
from core.models import RunContext, StepResult
from core.money import pct_diff, to_decimal


# ------------------------------------------------------------------ deterministic scoring
def line_similarity(inv_line, po_line) -> dict:
    desc = fuzzy.token_set(inv_line.description or "", po_line["description"]) / 100
    price_ok = (inv_line.unit_price is not None and
                abs(pct_diff(inv_line.unit_price, po_line["unit_price"])) <= config.PRICE_VARIANCE_OK_PCT)
    return {"desc": round(desc, 2), "price_ok": price_ok,
            "score": round(0.5 * desc + 0.5 * (1.0 if price_ok else 0.0), 3)}


def match_lines(inv_lines, po_lines) -> list[dict]:
    """Best PO line for each invoice line + unit-price variance."""
    out = []
    for i, il in enumerate(inv_lines, 1):
        best, best_sim = None, {"desc": 0, "price_ok": False, "score": 0}
        for pl in po_lines:
            sim = line_similarity(il, pl)
            if sim["score"] > best_sim["score"]:
                best, best_sim = pl, sim
        matched = best is not None and (best_sim["desc"] * 100 >= config.LINE_MATCH_FUZZY_MIN or
                                        (best_sim["desc"] >= 0.5 and best_sim["price_ok"]))
        out.append({
            "invoice_line": i, "description": il.description,
            "unit_price": str(il.unit_price) if il.unit_price is not None else None,
            "quantity": str(il.quantity) if il.quantity is not None else None,
            "po_line": best["line_no"] if matched else None,
            "po_description": best["description"] if matched else None,
            "po_unit_price": str(best["unit_price"]) if matched else None,
            "desc_similarity": best_sim["desc"],
            "price_variance_pct": (str(pct_diff(il.unit_price, best["unit_price"]))
                                   if matched and il.unit_price is not None else None),
            "matched": matched,
        })
    return out


def score_po(ctx: RunContext, po: dict) -> dict:
    lines = db.get_po_lines(po["po_id"])
    billed = db.get_billed_to_date(po["po_id"])
    inv_lines = ctx.extracted.line_items
    line_scores = [max((line_similarity(il, pl)["score"] for pl in lines), default=0) for il in inv_lines]
    line_score = sum(line_scores) / len(line_scores) if line_scores else 0
    remaining = to_decimal(po["total_taxable"]) - billed
    taxable = ctx.extracted.taxable_value or Decimal("0")
    if taxable <= remaining * Decimal("1.01"):
        fit = 1.0
    else:
        fit = max(0.0, 1 - float((taxable - remaining) / taxable)) if taxable else 0.0
    return {"po_id": po["po_id"], "line_score": round(line_score, 3), "amount_fit": round(fit, 3),
            "remaining": str(remaining), "score": round(0.7 * line_score + 0.3 * fit, 3)}


# ------------------------------------------------------------------ agent
def _agent_infer(ctx: RunContext, candidates: list[dict]) -> tuple[dict | None, list[dict]]:
    """LLM tool loop. Isolated so tests can mock it. Returns (answer, trace)."""
    vid = ctx.vendor["vendor_id"]
    handlers = {
        "list_open_pos": lambda vendor_id=vid: [
            {k: p[k] for k in ("po_id", "po_date", "total_taxable", "description")} for p in db.list_open_pos(vendor_id)],
        "get_po_lines": lambda po_id: db.get_po_lines(po_id),
        "get_billed_to_date": lambda po_id: {"po_id": po_id, "billed_to_date": str(db.get_billed_to_date(po_id))},
    }
    x = ctx.extracted
    invoice_view = {
        "vendor_id": vid, "vendor_name": ctx.vendor["name"], "invoice_number": x.invoice_number,
        "invoice_date": x.invoice_date, "taxable_value": str(x.taxable_value),
        "line_items": [li.model_dump(mode="json") for li in x.line_items],
        "printed_po_reference": x.po_reference,
    }
    user = f"Invoice (no usable PO reference):\n{db.dumps(invoice_view)}\nVendor id for tools: {vid}"
    answer, trace = llm.run_tool_loop(prompts.PO_AGENT_SYSTEM, user, prompts.PO_AGENT_TOOLS,
                                      handlers, config.PO_AGENT_MAX_TURNS)
    return answer, trace


# ------------------------------------------------------------------ stage
def _attach(ctx: RunContext, po: dict) -> None:
    ctx.po = po
    ctx.po_lines = db.get_po_lines(po["po_id"])
    ctx.billed_to_date = db.get_billed_to_date(po["po_id"])
    ctx.line_matches = match_lines(ctx.extracted.line_items, ctx.po_lines)
    if po["status"] != "OPEN":
        ctx.add_flag("PO_CLOSED", "PO match", f"{po['po_id']} is {po['status']}")


def po_match(ctx: RunContext) -> StepResult:
    if not ctx.vendor:
        return StepResult("SKIP", "Skipped: vendor unknown", {})
    x = ctx.extracted

    # 1. Explicit reference
    if x.po_reference:
        po = db.get_po(x.po_reference)
        if po and po["vendor_id"] == ctx.vendor["vendor_id"]:
            ctx.match_method, ctx.match_confidence = "EXPLICIT", 1.0
            _attach(ctx, po)
            return _result(ctx, f"{po['po_id']} (printed on invoice)")
        note = "not found" if not po else f"belongs to {po['vendor_id']}"
    else:
        note = "absent"

    # 2. Inference
    candidates = db.list_open_pos(ctx.vendor["vendor_id"])
    if not candidates:
        ctx.add_flag("PO_NOT_FOUND", "PO match", f"PO reference {note}; vendor has no open POs")
        return StepResult("WARN", f"No PO: reference {note}, vendor has no open POs", {"reference": x.po_reference})

    scores = {p["po_id"]: score_po(ctx, p) for p in candidates}
    agent_answer, agent_error = None, None
    try:
        agent_answer, ctx.agent_trace = _agent_infer(ctx, candidates)
    except Exception as e:
        agent_error = str(e)[:200]

    if agent_answer and agent_answer.get("po_id") in scores:
        chosen = agent_answer["po_id"]
        agent_conf = float(agent_answer.get("confidence") or 0)
        ctx.match_method = "AGENT"
        ctx.match_confidence = round(min(agent_conf, scores[chosen]["score"]), 2)
        rationale = agent_answer.get("rationale")
    else:   # agent failed or picked nothing valid -> pure deterministic
        chosen = max(scores, key=lambda k: scores[k]["score"])
        ctx.match_method = "DETERMINISTIC"
        ctx.match_confidence = scores[chosen]["score"]
        rationale = "Agent unavailable; best deterministic score." if agent_error else (
            (agent_answer or {}).get("rationale") or "Agent found no fit; best deterministic score.")

    po = db.get_po(chosen)
    _attach(ctx, po)
    ev = {"inferred_po": chosen, "confidence": ctx.match_confidence, "scores": scores, "rationale": rationale}
    if ctx.match_confidence >= config.PO_INFER_ACCEPT_CONF:
        ctx.add_flag("PO_INFERRED", "PO match",
                     f"No PO on invoice; matched to {chosen} by {ctx.match_method.lower()} "
                     f"({ctx.match_confidence:.0%} confidence)", **ev)
    else:
        ctx.add_flag("PO_INFERRED_LOW_CONF", "PO match",
                     f"Best guess {chosen} only {ctx.match_confidence:.0%} confident", **ev)
    res = _result(ctx, f"{chosen} inferred by {ctx.match_method.lower()} · confidence {ctx.match_confidence:.0%}")
    res.detail.update(reference=f"{x.po_reference} ({note})" if x.po_reference else "absent",
                      candidate_scores=scores, rationale=rationale, agent_trace=ctx.agent_trace,
                      agent_error=agent_error)
    res.status = "WARN"
    return res


def _result(ctx: RunContext, headline: str) -> StepResult:
    unmatched = [m for m in ctx.line_matches if not m["matched"]]
    return StepResult("WARN" if unmatched else "OK",
                      f"{headline} · {len(ctx.line_matches) - len(unmatched)}/{len(ctx.line_matches)} lines matched",
                      {"po_id": ctx.po["po_id"], "method": ctx.match_method, "confidence": ctx.match_confidence,
                       "po_total": str(ctx.po["total_taxable"]), "billed_to_date": str(ctx.billed_to_date),
                       "line_matches": ctx.line_matches})
