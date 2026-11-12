"""Page 4 · Purchase Orders: billed vs remaining per PO, linked invoices. Makes split-PO billing visible."""
import pandas as pd
import streamlit as st

from core import db
from core.money import format_inr
from ui.components import page_setup

page_setup("Purchase Orders")
st.title("Purchase orders")
st.caption("“Billed” counts only approved or paid invoices. Held invoices show below but don't consume the PO.")

for po in db.list_pos():
    total = float(po["total_taxable"])
    billed = float(db.get_billed_to_date(po["po_id"]))
    invs = db.invoices_for_po(po["po_id"])
    pending = sum(float(i["taxable_value"] or 0) for i in invs if i["status"] in ("HOLD", "HUMAN_REVIEW"))
    with st.container(border=True):
        c1, c2 = st.columns([2, 3])
        with c1:
            st.markdown(f"**{po['po_id']}** · {po['vendor_name']}  \n<span style='color:#56607a'>{po['description']} · "
                        f"{po['status']}</span>", unsafe_allow_html=True)
        with c2:
            pct = billed / total if total else 0
            st.progress(min(pct, 1.0), text=f"Billed {format_inr(billed)} of {format_inr(total)} · "
                                            f"remaining {format_inr(max(total - billed, 0))}")
            if pending:
                over = billed + pending > total
                (st.error if over else st.warning)(
                    f"{format_inr(pending)} waiting on hold/review"
                    + (f": would take the PO to {format_inr(billed + pending)} (over by {format_inr(billed + pending - total)})" if over else ""))
        if invs:
            st.dataframe(pd.DataFrame([{"Invoice": i["invoice_number"], "Date": i["invoice_date"],
                                        "Taxable": format_inr(i["taxable_value"]), "Total": format_inr(i["total_amount"]),
                                        "Status": i["status"], "Source": i["source"]} for i in invs]),
                         hide_index=True, width="stretch")
