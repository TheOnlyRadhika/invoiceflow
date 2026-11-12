"""Page 2 · Dashboard: KPIs, decision mix, run history, drill-down into any run."""
import pandas as pd
import streamlit as st

from core import db
from core.money import format_inr
from ui.components import STATUS_LABEL, decision_card, page_setup, stored_timeline

page_setup("Dashboard")
st.title("Dashboard")

runs = db.list_runs()
if not runs:
    st.info("No runs yet. Process an invoice first.")
    st.stop()

df = pd.DataFrame(runs)
df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce").fillna(0)
n = len(df)
auto = df["decision"].isin(["APPROVE", "APPROVE_WITH_NOTE"]).sum()
held = df[df["decision"].isin(["HOLD", "HUMAN_REVIEW"])]["total_amount"].sum()
pending = (df["review_status"] == "PENDING").sum()

k = st.columns(6)
k[0].metric("Invoices processed", n)
k[1].metric("Auto-approved", f"{auto / n:.0%}")
k[2].metric("Value processed", format_inr(df["total_amount"].sum()).split(".")[0])
k[3].metric("Value on hold / review", format_inr(held).split(".")[0])
k[4].metric("Pending reviews", int(pending))
k[5].metric("Avg processing time", f"{df['duration_ms'].mean() / 1000:.1f}s")

c1, c2 = st.columns([1, 2])
with c1:
    st.markdown("**Decision mix**")
    mix = df["decision"].map(STATUS_LABEL).value_counts()
    st.bar_chart(mix, horizontal=True, height=220)
with c2:
    st.markdown("**Run history**")
    f1, f2 = st.columns(2)
    dec_f = f1.multiselect("Decision", sorted(df["decision"].unique()))
    ven_f = f2.multiselect("Vendor", sorted(df["vendor_name"].dropna().unique()))
    view = df.copy()
    if dec_f:
        view = view[view["decision"].isin(dec_f)]
    if ven_f:
        view = view[view["vendor_name"].isin(ven_f)]
    table = pd.DataFrame({
        "Run": view["run_id"], "Time": view["started_at"].str.replace("T", " "), "File": view["filename"],
        "Vendor": view["vendor_name"], "Invoice": view["invoice_number"],
        "Total": view["total_amount"].map(lambda v: format_inr(v)),
        "Decision": view["decision"].map(STATUS_LABEL),
        "Reasons": view["reason_codes"].map(lambda c: ", ".join(c) if c else "—"),
        "Review": view["review_status"], "Secs": (view["duration_ms"] / 1000).round(1),
    })
    st.dataframe(table, hide_index=True, width="stretch", height=260)

st.divider()
st.markdown("### Run detail")
if view.empty:
    st.info("No runs match the filters.")
    st.stop()
choice = st.selectbox("Open a run", view["run_id"] + " · " + view["filename"].fillna("") + " · " +
                      view["decision"].map(STATUS_LABEL))
run = db.get_run(choice.split(" · ")[0])
left, right = st.columns([3, 2], gap="large")
with right:
    decision_card(run)
with left:
    st.markdown("**Stage timeline**")
    stored_timeline(run)
