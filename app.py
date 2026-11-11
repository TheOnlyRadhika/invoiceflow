"""TEMPORARY hello app for tonight's deploy test. Replaced by the real
Process Invoice page tomorrow. Proves: Streamlit Cloud runs, secrets load,
SQLite seeds, Groq answers from the cloud."""
import time

import streamlit as st

from core import config, db

st.set_page_config(page_title="InvoiceFlow Agent", page_icon="🧾", layout="wide")
db.init_db()

st.title("🧾 InvoiceFlow Agent")
st.caption("Deploy check: infrastructure only")

c1, c2, c3 = st.columns(3)
c1.metric("Vendors seeded", len(db.list_vendors()))
c2.metric("POs seeded", len(db.list_pos()))
c3.metric("Groq key loaded", "yes" if config.GROQ_API_KEY else "NO")

if st.button("Ping Groq"):
    from groq import Groq
    t = time.time()
    r = Groq(api_key=config.GROQ_API_KEY).chat.completions.create(
        model=config.TEXT_MODEL, messages=[{"role": "user", "content": "Reply with: pong"}])
    st.success(f"{r.choices[0].message.content}  ({time.time()-t:.2f}s, {config.TEXT_MODEL})")
