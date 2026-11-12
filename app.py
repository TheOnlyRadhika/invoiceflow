"""Page 1 · Process Invoice: upload or pick a sample → watch each stage run live → decision card."""
import time

import streamlit as st

from core import config, db
from core.pipeline.ingest import render_pages
from core.pipeline.orchestrator import STAGE_NAMES, run_pipeline
from ui.components import decision_card, flow_strip, page_setup, stage_detail, step_label

page_setup("Process Invoice")

SAMPLES = {
    "01 — Acme Office Supplies: standard invoice": "01_happy_acme.pdf",
    "02 — Bharat Cloud: third invoice on split PO": "02_split_po_overrun.pdf",
    "03 — Bharat Cloud: scanned resubmission": "03_scanned_duplicate.pdf",
    "04 — Sharma Logistics: no PO reference": "04_missing_po_sharma.pdf",
    "05 — Acme Office Supplies: new bank details": "05_bank_change_acme.pdf",
    "06 — Nimbus Print Works: blocked vendor": "06_blocked_vendor.pdf",
}

st.title("Invoice processing")
flow_strip()
st.write("")

c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    upload = st.file_uploader("Upload a vendor invoice", type=["pdf", "png", "jpg", "jpeg"])
with c2:
    sample = st.selectbox("Or select a sample invoice", ["—"] + list(SAMPLES.keys()))
with c3:
    st.write("")
    st.write("")
    run_clicked = st.button("Process invoice", type="primary", width="stretch")

if upload is not None:
    filename, data = upload.name, upload.getvalue()
elif sample != "—":
    filename = SAMPLES[sample]
    data = (config.SAMPLES_DIR / filename).read_bytes()
else:
    filename, data = None, None

left, right = st.columns([3, 2], gap="large")

# ---------------------------------------------------------------- preview
with right:
    if data:
        st.markdown(f"**Document** · `{filename}`")
        try:
            if filename.lower().endswith(".pdf"):
                st.image(render_pages(data, max_pages=1, dpi=90)[0], width="stretch")
            else:
                st.image(data, width="stretch")
        except Exception:
            st.caption("Preview unavailable")
    decision_slot = st.container()

# ---------------------------------------------------------------- live run
if run_clicked:
    if not data:
        st.warning("Upload a file or choose a sample first.")
        st.stop()
    with left:
        st.markdown("#### Processing steps")
        progress = st.progress(0.0, text="Starting…")
        boxes: dict[int, object] = {}
        out: list = []
        for ev in run_pipeline(filename, data, ctx_out=out):
            if ev.status == "RUNNING":
                boxes[ev.seq] = st.status(step_label(ev.seq, ev.stage, "RUNNING", "running…"),
                                          state="running", expanded=False)
                progress.progress((ev.seq - 1) / len(STAGE_NAMES), text=f"{ev.stage}…")
                continue
            box = boxes[ev.seq]
            state = "error" if ev.status == "FAIL" else "complete"
            box.update(label=step_label(ev.seq, ev.stage, ev.status, ev.summary, ev.duration_ms),
                       state=state, expanded=False)
            with box:
                stage_detail(ev.stage, ev.detail)
            time.sleep(0.15)        # UI pacing only, so viewers can follow the stages
        progress.progress(1.0, text="Done")
    st.session_state["last_run_id"] = out[0].run_id

# ---------------------------------------------------------------- decision
run_id = st.session_state.get("last_run_id")
if run_id:
    run = db.get_run(run_id)
    if run and run.get("decision"):
        with decision_slot:
            st.markdown("#### Decision")
            decision_card(run)
        if not run_clicked:
            with left:
                st.markdown(f"#### Last run · {run_id}")
                from ui.components import stored_timeline
                stored_timeline(run)
elif not run_clicked:
    with left:
        st.caption("Select a sample or upload a PDF, then choose Process invoice. Each step appears here as it runs.")
