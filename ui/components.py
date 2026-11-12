"""Shared Streamlit building blocks: decision card, stage details, styling.
Used by the live run page AND the dashboard drill-down, so both look identical."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core import config, db
from core.money import format_inr

STATUS_COLOR = {"APPROVE": "#15803d", "APPROVE_WITH_NOTE": "#4d7c0f", "HOLD": "#c2410c",
                "HUMAN_REVIEW": "#6d28d9", "REJECT": "#b91c1c"}
STATUS_TINT = {"APPROVE": "#f0fdf4", "APPROVE_WITH_NOTE": "#f7fee7", "HOLD": "#fff7ed",
               "HUMAN_REVIEW": "#f5f3ff", "REJECT": "#fef2f2"}
# Streamlit markdown badge colours (work inside status/expander labels)
STATUS_BADGE = {"APPROVE": "green", "APPROVE_WITH_NOTE": "green", "HOLD": "orange",
                "HUMAN_REVIEW": "violet", "REJECT": "red"}
STEP_BADGE = {"OK": ":green-badge[Passed]", "WARN": ":orange-badge[Flagged]", "FAIL": ":red-badge[Failed]",
              "SKIP": ":gray-badge[Skipped]", "RUNNING": ":blue-badge[Running]"}
AI_BADGE = ":violet-badge[AI]"


def step_label(seq: int, stage: str, status: str, summary: str, ms: int | None = None) -> str:
    """Coloured, scannable label used for live steps and stored timelines."""
    ai = f" {AI_BADGE}" if stage in AI_STAGES else ""
    tail = f"  ·  {ms} ms" if ms is not None else ""
    return f"{STEP_BADGE.get(status, status)}  **{seq:02d} {stage}**{ai}  —  {summary}{tail}"


def decision_badge(status: str) -> str:
    return f":{STATUS_BADGE.get(status, 'gray')}-badge[{STATUS_LABEL.get(status, status)}]"
STATUS_LABEL = {"APPROVE": "APPROVED", "APPROVE_WITH_NOTE": "APPROVED WITH NOTE", "HOLD": "ON HOLD",
                "HUMAN_REVIEW": "HUMAN REVIEW", "REJECT": "REJECTED"}
STEP_ICON = {"OK": "Passed", "WARN": "Flagged", "FAIL": "Failed", "SKIP": "Skipped", "RUNNING": "Running"}
AI_STAGES = {"Extract", "PO match", "Explain", "Action"}

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1360px;}
h1 {font-weight: 600 !important; letter-spacing: -0.3px;}
h4 {font-weight: 600 !important; font-size: 0.95rem !important; text-transform: uppercase;
    letter-spacing: .6px; color: #475569;}
.pill {display:inline-block;padding:3px 8px;border-radius:3px;color:#fff;font-weight:600;
       font-size:0.7rem;letter-spacing:.8px;text-transform:uppercase}
.chip {display:inline-block;padding:1px 6px;margin:2px 4px 2px 0;border:1px solid #e2e8f0;border-radius:3px;
       color:#334155;font-size:0.72rem;font-family:ui-monospace,monospace;background:#f8fafc}
.dcard {border:1px solid #e2e8f0;border-left-width:5px;border-radius:6px;padding:16px 18px;background:#fff}
.dcard h3 {margin:10px 0 6px 0;font-size:1rem;font-weight:600;line-height:1.4;color:#0f172a}
.kv {display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #f1f5f9;font-size:0.86rem}
.kv span:first-child {color:#64748b}
.muted {color:#64748b;font-size:0.82rem}
.ai {color:#64748b;font-size:0.72rem;font-weight:500;margin-left:6px;text-transform:uppercase;letter-spacing:.5px}
.flow span {display:inline-block;padding:2px 8px;margin:2px 0;border-radius:4px;font-size:.76rem;
           background:#f1f5f9;color:#334155;border:1px solid #e2e8f0}
.flow span.a {background:#f5f3ff;color:#5b21b6;border-color:#ddd6fe}
.flow span.d {background:#fff7ed;color:#9a3412;border-color:#fed7aa;font-weight:600}
.flow i {color:#94a3b8;font-style:normal;margin:0 3px}
.legend span {display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
</style>
"""


def page_setup(title: str) -> None:
    st.set_page_config(page_title=f"{title} · InvoiceFlow", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    db.init_db()
    with st.sidebar:
        st.markdown("**InvoiceFlow**")
        st.caption("Accounts payable invoice processing")
        st.markdown(f"<span class='muted'>Text model: <code>{config.TEXT_MODEL}</code><br>"
                    f"Vision model: <code>{config.VISION_MODEL}</code></span>", unsafe_allow_html=True)
        st.divider()
        if st.button("Reset demo data", width="stretch",
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
    st.markdown("<div class='flow'>" + "<i>→</i>".join(parts) + "</div>"
                "<div class='muted legend'>"
                "<span style='background:#8b5cf6'></span>uses a language model"
                "<span style='background:#f97316'></span>makes the decision (rules only)"
                "<span style='background:#cbd5e1'></span>deterministic code</div>",
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

    color, tint = STATUS_COLOR.get(status, "#64748b"), STATUS_TINT.get(status, "#fff")
    st.markdown(f"<div class='dcard' style='border-left-color:{color};background:{tint}'>"
                f"{pill(status)}<h3>{esc(run.get('headline'))}</h3>"
                f"<div>{chips}</div><div style='margin-top:10px'>{kv}</div></div>", unsafe_allow_html=True)

    if run.get("review_status") and run["review_status"] not in ("NONE", None):
        st.caption(f"Review: **{run['review_status']}**" + (f" · “{run['review_note']}”" if run.get("review_note") else ""))

    exp_step = next((s for s in run.get("steps", []) if s["stage"] == "Explain"), None)
    by_ai = bool(exp_step and (exp_step.get("detail") or {}).get("source") == "llm")
    st.markdown(f"**Explanation**  {AI_BADGE if by_ai else ':gray-badge[Template]'}")
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
                st.markdown("**Self-check** <span class='ai'>model re-read</span>", unsafe_allow_html=True)
                (st.success if "corrected" in sc.get("outcome", "") else st.warning)(
                    f"{sc.get('outcome')}. First read issues: " + "; ".join(sc.get("first_read_issues", [])))
            if x.get("extraction_notes"):
                st.warning(f"Model noticed: {x['extraction_notes']}")
        elif stage == "Validate":
            df = pd.DataFrame([{"check": c["check"], "result": "Pass" if c["ok"] else "Fail", "detail": c["detail"]}
                               for c in d.get("checks", [])])
            st.dataframe(df.style.map(lambda v: "color:#15803d;font-weight:600" if v == "Pass" else
                                      "color:#b91c1c;font-weight:600" if v == "Fail" else "", subset=["result"]),
                         hide_index=True, width="stretch")
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
                st.markdown("**Agent trace** <span class='ai'>tool calls</span>", unsafe_allow_html=True)
                for t in d["agent_trace"]:
                    st.markdown(f"Turn {t['turn']}: `{t['tool']}({esc(t.get('args'))})`")
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
        with st.expander(step_label(s["seq"], s["stage"], s["status"], s["summary"], s["duration_ms"])):
            stage_detail(s["stage"], s.get("detail") or {})
