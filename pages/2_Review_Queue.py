"""Page 3 · Review Queue: humans resolve HOLD and HUMAN_REVIEW items. Every override is logged."""
import streamlit as st

from core import db
from core.money import format_inr
from core.pipeline.orchestrator import apply_review
from ui.components import decision_card, page_setup, pill

page_setup("Review Queue")
st.title("Review queue")
st.caption("The system never pays a held or flagged invoice on its own. A person decides, with a note for the audit trail.")

queue = db.review_queue()
if not queue:
    st.success("Nothing waiting for review.")
else:
    for r in queue:
        title = f"{r['vendor_name'] or 'Unknown vendor'} · {r['invoice_number'] or r['filename']} · {format_inr(r['total_amount'])}"
        with st.expander(f"{r['decision']} · {title}", expanded=len(queue) <= 2):
            run = db.get_run(r["run_id"])
            decision_card(run)
            note = st.text_area("Reviewer note (required)", key=f"note_{r['run_id']}",
                                placeholder="e.g. Called vendor on number on file; bank change confirmed with cancelled cheque.")
            b1, b2, b3 = st.columns(3)
            choice = None
            if b1.button("✅ Approve (override)", key=f"a_{r['run_id']}", width="stretch"):
                choice = "OVERRIDDEN_APPROVE"
            if b2.button("⏸ Keep on hold", key=f"h_{r['run_id']}", width="stretch"):
                choice = "CONFIRMED"
            if b3.button("⛔ Reject", key=f"r_{r['run_id']}", width="stretch"):
                choice = "OVERRIDDEN_REJECT"
            if choice:
                if not note.strip():
                    st.error("Add a note first.")
                else:
                    apply_review(r["run_id"], choice, note.strip())
                    st.toast(f"{r['run_id']} → {choice}")
                    st.rerun()

st.divider()
st.markdown("### Review log")
done = [r for r in db.list_runs() if r["review_status"] not in ("NONE", "PENDING", None)]
if not done:
    st.caption("No reviews yet.")
for r in done:
    st.markdown(f"{pill(r['decision'])} → **{r['review_status']}** · {r['vendor_name']} · {r['invoice_number']} · "
                f"{r['reviewed_at']} · “{r['review_note']}”", unsafe_allow_html=True)
