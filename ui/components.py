"""Shared Streamlit building blocks: decision card, stage details, styling.
Used by the live run page AND the dashboard drill-down, so both look identical."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core import config, db
from core.money import format_inr

STATUS_COLOR = {"APPROVE": "#16a34a", "APPROVE_WITH_NOTE": "#65a30d", "HOLD": "#d97706",
                "HUMAN_REVIEW": "#7c3aed", "REJECT": "#dc2626"}
STATUS_LABEL = {"APPROVE": "APPROVED", "APPROVE_WITH_NOTE": "APPROVED WITH NOTE", "HOLD": "ON HOLD",
                "HUMAN_REVIEW": "HUMAN REVIEW", "REJECT": "REJECTED"}
STEP_ICON = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIP": "⏭️", "RUNNING": "⏳"}
AI_STAGES = {"Extract", "PO match", "Explain", "Action"}

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1400px;}
.pill {display:inline-block;padding:3px 10px;border-radius:12px;color:#fff;font-weight:700;font-size:0.78rem;letter-spacing:.3px}
.chip {display:inline-block;padding:2px 8px;margin:2px 4px 2px 0;border-radius:6px;background:#eef1f8;
       color:#1d2433;font-size:0.75rem;font-family:monospace}
.dcard {border:1px solid #e3e7f0;border-radius:12px;padding:16px 18px;background:#fff}
.dcard h3 {margin:6px 0 4px 0;font-size:1.05rem;line-height:1.35}
.kv {display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed #eef1f8;font-size:0.88rem}
.kv span:first-child {color:#56607a}
.muted {color:#56607a;font-size:0.85rem}
.ai {display:inline-block;padding:0 6px;border-radius:4px;background:#ede9fe;color:#6d28d9;font-size:0.7rem;font-weight:700;margin-left:4px}
.flow span {display:inline-block;padding:3px 8px;margin:2px;border-radius:6px;background:#14213d;color:#fff;font-size:.75rem}
.flow span.a {background:#7c3aed}
.flow span.d {background:#c2410c}
</style>
"""


def page_setup(title: str) -> None:
    st.set_page_config(page_title=f"{title} · InvoiceFlow", page_icon="🧾", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    db.init_db()
    with st.sidebar:
        st.markdown("### 🧾 InvoiceFlow Agent")
        st.caption("AI reads and reasons. Rules decide. Humans override.")
        st.markdown(f"<span class='muted'>Text model: <code>{config.TEXT_MODEL}</code><br>"
                    f"Vision model: <code>{config.VISION_MODEL}</code></span>", unsafe_allow_html=True)
        st.divider()
        if st.button("↺ Reset demo data", width="stretch",
                     help="Restore seed vendors, POs and invoice history. Clears all runs."):
            db.reset_and_seed()
            st.session_state.pop("last_run_id", None)
            st.toast("Demo data restored")
            st.rerun()


def pill(status: str) -> str:
    return (f"<span class='pill' style='background:{STATUS_COLOR.get(status, '#64748b')}'>"
            f"{STATUS_LABEL.get(status, status)}</span>")


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def flow_strip() -> None:
    parts = []
    for name in ["Ingest", "Extract", "Normalize", "Validate", "Vendor check", "PO match",
                 "Duplicate check", "Rules & decision", "Explain", "Action"]:
        cls = "a" if name in AI_STAGES else "d" if name == "Rules & decision" else ""
        parts.append(f"<span class='{cls}'>{name}</span>")
    st.markdown("<div class='flow'>" + " → ".join(parts) + "</div>"
                "<div class='muted'>Purple = AI involved · Orange = rules decide · Navy = plain code</div>",
                unsafe_allow_html=True)


# ------------------------------------------------------------------ decision card
def decision_card(run: dict) -> None:
    """run = db.get_run(run_id)"""
    status = run["decision"]
    ev = run.get("evidence") or {}
    inv = ev.get("invoice") or {}
    po = ev.get("po") or {}
    cap = ev.get("capacity") or {}
    chips = "".join(f"<span class='chip'>{esc(c)}</span>" for c in run.get("reason_codes") or []) or \
        "<span class='chip'>no exceptions</span>"

    rows = [("Vendor", run.get("vendor_name")), ("Invoice", run.get("invoice_number")),
            ("Invoice total", format_inr(inv.get("total")) if inv.get("total") else "—"),
            ("PO", f"{po.get('po_id')} · {str(po.get('method', '')).lower()}" if po else "—")]
    if cap:
        rows += [("PO value", format_inr(cap.get("po_total"))),
                 ("Billed before this", format_inr(cap.get("billed_to_date"))),
                 ("After this invoice", format_inr(cap.get("after_this_invoice")))]
    if ev.get("approvable_amount"):
        rows.append(("Approvable now", format_inr(ev["approvable_amount"])))
    if run.get("duration_ms"):
        rows.append(("Processing time", f"{run['duration_ms'] / 1000:.1f}s"))
    kv = "".join(f"<div class='kv'><span>{esc(k)}</span><span><b>{esc(v)}</b></span></div>" for k, v in rows)

    st.markdown(f"<div class='dcard'>{pill(status)}<h3>{esc(run.get('headline'))}</h3>"
                f"<div>{chips}</div><div style='margin-top:10px'>{kv}</div></div>", unsafe_allow_html=True)

    if run.get("review_status") and run["review_status"] not in ("NONE", None):
        st.caption(f"Review: **{run['review_status']}**" + (f" · “{run['review_note']}”" if run.get("review_note") else ""))

    st.markdown("**Why** <span class='ai'>AI-written</span>", unsafe_allow_html=True)
    st.write(run.get("explanation") or "—")
    action_block(run.get("action") or {})


def action_block(action: dict) -> None:
    kind = action.get("kind")
    if not kind:
        return
    st.markdown("**Next action**")
    if kind == "LEDGER_POST":
        l = action["ledger"]
        st.success(f"Posted to ledger (mock) · {l['gl_code']} · payable {format_inr(l['payable'])} "
                   f"· due {l['payment_due']}")
        if l.get("note"):
            st.caption(f"Audit note: {l['note']}")
    elif kind == "INTERNAL_FRAUD_ALERT":
        a = action["alert"]
        st.error(f"**{a['subject']}**  \nTo: {a['to']} · Priority {a['priority']}")
        st.code(a["body"], language=None)
    elif kind in ("VENDOR_EMAIL_DRAFT", "VENDOR_NOTICE_DRAFT"):
        e = action["email"]
        label = "Vendor email draft (on hold)" if kind == "VENDOR_EMAIL_DRAFT" else "Vendor notice draft"
        st.warning(f"{label} → {action.get('to') or 'vendor'}")
        st.code(f"Subject: {e['subject']}\n\n{e['body']}", language=None)
    elif kind == "REVIEW_QUEUE":
        st.info(action.get("note", "Queued for human review"))


# ------------------------------------------------------------------ stage details
def stage_detail(stage: str, d: dict) -> None:
    """Human-friendly rendering of each stage's detail dict."""
    if not d:
        st.caption("No details.")
        return
    try:
        if stage == "Extract" and d.get("extracted"):
            x = d["extracted"]
            st.caption(f"Path: {d.get('path')} · model: {d.get('model')} · {d.get('latency_ms') or 0} ms · "
                       f"attempts: {d.get('attempts')}")
            conf = x.get("field_confidence") or {}
            fields = ["invoice_number", "invoice_date", "vendor_name", "vendor_gstin", "po_reference",
                      "taxable_value", "cgst", "sgst", "igst", "total_amount", "bank_account_number", "bank_ifsc"]
            st.dataframe(pd.DataFrame([{"field": f, "value": x.get(f), "confidence": conf.get(f)} for f in fields]),
                         hide_index=True, width="stretch")
            if x.get("line_items"):
                st.dataframe(pd.DataFrame(x["line_items"]), hide_index=True, width="stretch")
            sc = d.get("self_check")
            if sc:
                st.markdown("**Self-check** <span class='ai'>AI re-read</span>", unsafe_allow_html=True)
                (st.success if "corrected" in sc.get("outcome", "") else st.warning)(
                    f"{sc.get('outcome')}. First read issues: " + "; ".join(sc.get("first_read_issues", [])))
            if x.get("extraction_notes"):
                st.warning(f"Model noticed: {x['extraction_notes']}")
        elif stage == "Validate":
            st.dataframe(pd.DataFrame([{"check": c["check"], "result": "✅" if c["ok"] else "❌", "detail": c["detail"]}
                                       for c in d.get("checks", [])]), hide_index=True, width="stretch")
        elif stage == "PO match":
            st.caption(f"Method: {d.get('method')} · confidence {d.get('confidence')} · PO value "
                       f"{format_inr(d.get('po_total'))} · billed to date {format_inr(d.get('billed_to_date'))}")
            if d.get("line_matches"):
                st.dataframe(pd.DataFrame(d["line_matches"])[["invoice_line", "description", "matched", "po_description",
                                                               "unit_price", "po_unit_price", "price_variance_pct",
                                                               "desc_similarity"]],
                             hide_index=True, width="stretch")
            if d.get("candidate_scores"):
                st.markdown("**Candidate POs (deterministic verification)**")
                st.dataframe(pd.DataFrame(d["candidate_scores"].values()), hide_index=True, width="stretch")
            if d.get("rationale"):
                st.info(f"Rationale: {d['rationale']}")
            if d.get("agent_trace"):
                st.markdown("**Agent trace** <span class='ai'>AI tool calls</span>", unsafe_allow_html=True)
                for t in d["agent_trace"]:
                    st.markdown(f"`turn {t['turn']}` → **{t['tool']}**({esc(t.get('args'))})")
                    st.json(t.get("result"), expanded=False)
            if d.get("agent_error"):
                st.caption(f"Agent fallback reason: {d['agent_error']}")
        elif stage == "Rules & decision":
            if d.get("rules_fired"):
                st.dataframe(pd.DataFrame(d["rules_fired"]), hide_index=True, width="stretch")
            else:
                st.success("No rule fired → APPROVE")
        elif stage == "Explain":
            st.caption(f"Source: {d.get('source')}")
            st.write(d.get("text"))
        elif stage == "Action":
            action_block(d)
        else:
            st.json(d, expanded=True)
    except Exception:              # never let a display bug hide the data
        st.json(d, expanded=False)


def stored_timeline(run: dict) -> None:
    for s in run.get("steps", []):
        icon = STEP_ICON.get(s["status"], "•")
        ai = " 🤖" if s["stage"] in AI_STAGES else ""
        with st.expander(f"{icon} {s['seq']}. {s['stage']}{ai} · {s['summary']} · {s['duration_ms']} ms"):
            stage_detail(s["stage"], s.get("detail") or {})
