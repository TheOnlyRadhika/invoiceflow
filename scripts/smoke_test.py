"""Run every sample and assert the expected decision.

    python scripts/smoke_test.py          # real Groq calls (needs .env)
    python scripts/smoke_test.py --fresh  # real Groq, ignore cached extractions
    python scripts/smoke_test.py --mock   # no network: fake extraction from known sample data
"""
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MOCK = "--mock" in sys.argv
if MOCK:   # isolated throwaway DB so mock runs never touch your real one
    os.environ["INVOICEFLOW_DB"] = str(Path(tempfile.mkdtemp()) / "mock.db")

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from core import config, db  # noqa: E402
from core.pipeline.orchestrator import run_pipeline  # noqa: E402

EXPECTED = [
    ("01_happy_acme.pdf", "APPROVE", []),
    ("02_split_po_overrun.pdf", "HOLD", ["PO_OVERRUN", "UNMATCHED_LINE"]),
    ("03_scanned_duplicate.pdf", "REJECT", ["DUPLICATE_EXACT"]),
    ("04_missing_po_sharma.pdf", "APPROVE_WITH_NOTE", ["PO_INFERRED"]),
    ("05_bank_change_acme.pdf", "HUMAN_REVIEW", ["BANK_DETAILS_CHANGED"]),
    ("06_blocked_vendor.pdf", "REJECT", ["VENDOR_BLOCKED"]),
]


def install_mocks():
    """Replace every LLM call with deterministic fakes built from generate_invoices.INVOICES."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from generate_invoices import INVOICES, totals
    from core.pipeline import actions, explain, extract, po_match

    def fake_extract(ctx, recheck=None):
        inv = INVOICES[ctx.filename]
        taxable, cgst, sgst, igst, total = totals(inv)
        conf = 0.8 if inv.get("scanned") else 0.97
        data = {
            "invoice_number": inv["number"], "invoice_date": inv["date"], "due_date": inv["due"],
            "vendor_name": inv["vendor"].upper() if inv.get("scanned") else inv["vendor"],
            "vendor_gstin": inv["gstin"], "buyer_gstin": config.BUYER_GSTIN, "po_reference": inv["po"],
            "line_items": [{"description": d, "hsn_sac": h, "quantity": q, "unit_price": p,
                            "amount": str(Decimal(q) * Decimal(p))} for d, h, q, p in inv["lines"]],
            "taxable_value": str(taxable), "cgst": str(cgst) if cgst else None,
            "sgst": str(sgst) if sgst else None, "igst": str(igst) if igst else None,
            "total_amount": str(total), "bank_account_number": inv["bank"][2], "bank_ifsc": inv["bank"][1],
            "field_confidence": {f: conf for f in config.CRITICAL_FIELDS},
            "extraction_notes": inv.get("bank_note") or inv.get("stamp"),
        }
        return data, {"model": "mock", "attempts": 1, "latency_ms": 1}

    def fake_agent(ctx, candidates):
        raise RuntimeError("mock: agent offline")       # exercises deterministic fallback

    def boom(*a, **k):
        raise RuntimeError("mock: LLM offline")         # exercises template fallbacks

    extract._llm_extract = fake_extract
    po_match._agent_infer = fake_agent
    explain._llm_explain = boom
    actions._llm_draft = boom


def main():
    if MOCK:
        install_mocks()
    db.init_db()
    db.reset_and_seed()
    if "--fresh" in sys.argv:
        db.clear_extraction_cache()
    failures = 0
    for fname, want, want_codes in EXPECTED:
        out = []
        events = list(run_pipeline(fname, (config.SAMPLES_DIR / fname).read_bytes(), ctx_out=out))
        d = out[0].decision
        fails = [e for e in events if e.status == "FAIL"]
        ok = d.status == want and all(c in d.reason_codes for c in want_codes) and not fails
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {fname:<28} got {d.status:<18} {d.reason_codes}")
        if not ok:
            print(f"      expected {want} with {want_codes}")
            for e in events:
                if e.status in ("FAIL", "WARN"):
                    print(f"      {e.status} {e.stage}: {e.summary}")
    print(f"\n{len(EXPECTED) - failures}/{len(EXPECTED)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
