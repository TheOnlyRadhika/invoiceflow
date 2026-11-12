# InvoiceFlow Agent

Vendor invoice PDF in → explained AP decision out.
**AI reads and reasons. Rules decide. Humans override.**

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # paste your Groq key
python scripts/generate_invoices.py                    # sample PDFs (already committed)
python scripts/check_groq.py                           # verify key + models
python scripts/smoke_test.py                           # all 6 samples, real Groq
python scripts/run_cli.py samples/04_missing_po_sharma.pdf --verbose
streamlit run app.py
```

## Tests (no network)
```bash
python tests/test_rules.py               # 16 unit + robustness tests
python scripts/smoke_test.py --mock      # 6 samples with fake LLM
```

## Deploy (Streamlit Community Cloud)
share.streamlit.io → New app → this repo → `app.py` → Advanced settings → Secrets:
```toml
GROQ_API_KEY = "gsk_..."
```

_Full spec: InvoiceFlow_Direction.pdf. Assumptions: ASSUMPTIONS.md._
