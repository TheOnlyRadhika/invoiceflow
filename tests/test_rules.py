"""Unit + robustness tests. No network needed.

    python -m pytest tests -q        (or)        python tests/test_rules.py
"""
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["INVOICEFLOW_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

from core import config, db                                    # noqa: E402
from core.models import Flag                                   # noqa: E402
from core.money import format_inr, to_decimal                  # noqa: E402
from core.pipeline import extract                              # noqa: E402
from core.pipeline.normalize import norm_date, norm_po         # noqa: E402
from core.pipeline.orchestrator import run_pipeline            # noqa: E402
from core.pipeline.rules import decide, line_variance_flags, po_capacity  # noqa: E402
import smoke_test                                              # noqa: E402

smoke_test.install_mocks()
_real_fake = extract._llm_extract


def F(code):
    return Flag(code, "test", code)


def run(fname, data_override=None, file_bytes=None):
    """Run a sample through the pipeline with optional tweaks to the fake extraction."""
    def fake(ctx, recheck=None):
        data, meta = _real_fake(type("C", (), {"filename": fname})())
        if data_override:
            data.update(data_override)
        return data, meta
    extract._llm_extract = fake
    out = []
    raw = file_bytes or (config.SAMPLES_DIR / fname).read_bytes()
    if data_override:          # change the bytes so the extraction cache/hash doesn't collide
        raw = raw + repr(sorted(data_override.items())).encode()
    list(run_pipeline(fname, raw, ctx_out=out))
    extract._llm_extract = _real_fake
    return out[0]


# ------------------------------------------------------------------ pure rules
def test_no_flags_approves():
    assert decide([]).status == "APPROVE"


def test_precedence_most_severe_wins():
    d = decide([F("PO_INFERRED"), F("PO_OVERRUN"), F("BANK_DETAILS_CHANGED")])
    assert d.status == "HUMAN_REVIEW"
    assert d.reason_codes[0] == "BANK_DETAILS_CHANGED"
    assert decide([F("PO_INFERRED"), F("DUPLICATE_EXACT")]).status == "REJECT"


def test_every_code_maps_to_valid_decision():
    for code, (status, _) in config.REASON_CODES.items():
        assert status in config.DECISION_PRECEDENCE, code


def test_po_capacity_overrun_and_tolerance():
    c = po_capacity(600000, 400000, 240000)
    assert c["overrun"] and c["remaining_before"] == Decimal("200000.00") and c["overrun_pct"] == Decimal("6.67")
    assert not po_capacity(600000, 400000, 205000)["overrun"]       # +0.83% within 1% tolerance
    assert po_capacity(600000, 400000, 207000)["overrun"]           # +1.17%


def test_price_variance_bands():
    m = lambda v: {"invoice_line": 1, "description": "x", "matched": True, "unit_price": "1",
                   "po_unit_price": "1", "price_variance_pct": v}
    assert line_variance_flags([m("0.5")]) == []
    assert line_variance_flags([m("2.0")])[0].code == "PRICE_VARIANCE_MINOR"
    assert line_variance_flags([m("5.0")])[0].code == "PRICE_VARIANCE_HIGH"
    assert line_variance_flags([m("-10")]) == []                    # under-billing is fine


def test_helpers():
    assert format_inr(413000) == "₹4,13,000.00"
    assert to_decimal("Rs. 2,83,200.00") == Decimal("283200.00")
    assert norm_date("08-11-2026") == "2026-11-08" and norm_date("8 Nov 2026") == "2026-11-08"
    assert norm_po("po 2026/101") == "PO-2026-0101"


# ------------------------------------------------------------------ pipeline robustness
def test_same_file_twice_is_duplicate():
    db.reset_and_seed()
    assert run("01_happy_acme.pdf").decision.status == "APPROVE"
    second = run("01_happy_acme.pdf")
    assert second.decision.status == "REJECT" and "DUPLICATE_EXACT" in second.decision.reason_codes


def test_missing_invoice_number_goes_to_human():
    db.reset_and_seed()
    c = run("04_missing_po_sharma.pdf", {"invoice_number": None})
    assert c.decision.status == "HUMAN_REVIEW" and "MISSING_CRITICAL_FIELD" in c.decision.reason_codes


def test_wrong_gst_type_holds():
    db.reset_and_seed()   # Acme is intra-state; charging IGST is wrong
    c = run("01_happy_acme.pdf", {"cgst": None, "sgst": None, "igst": "63000"})
    assert "TAX_TYPE_MISMATCH" in c.decision.reason_codes and c.decision.status == "HOLD"


def test_total_not_adding_up_holds():
    db.reset_and_seed()
    c = run("01_happy_acme.pdf", {"total_amount": "415000"})
    assert "ARITHMETIC_MISMATCH" in c.decision.reason_codes


def test_price_creep_holds():
    db.reset_and_seed()   # chairs billed at 4,200 vs PO 4,000 (+5%)
    lines = [{"description": "Ergonomic office chair", "quantity": 50, "unit_price": 4200, "amount": 210000},
             {"description": "Office desk 120cm", "quantity": 20, "unit_price": 7500, "amount": 150000}]
    c = run("01_happy_acme.pdf", {"line_items": lines, "taxable_value": "360000",
                                  "cgst": "32400", "sgst": "32400", "total_amount": "424800"})
    assert "PRICE_VARIANCE_HIGH" in c.decision.reason_codes and c.decision.status == "HOLD"


def test_low_confidence_scan_goes_to_human():
    db.reset_and_seed()
    c = run("02_split_po_overrun.pdf", {"field_confidence": {"total_amount": 0.4}})
    assert "LOW_CONFIDENCE" in c.decision.reason_codes and c.decision.status == "HUMAN_REVIEW"


def test_extraction_failure_never_crashes():
    db.reset_and_seed()
    def broken(ctx):
        raise RuntimeError("model down")
    extract._llm_extract = broken
    out = []
    events = list(run_pipeline("x.pdf", (config.SAMPLES_DIR / "06_blocked_vendor.pdf").read_bytes() + b"x", ctx_out=out))
    extract._llm_extract = _real_fake
    assert out[0].decision.status == "HUMAN_REVIEW"
    assert "EXTRACTION_FAILED" in out[0].decision.reason_codes
    assert [e.stage for e in events if e.status != "RUNNING"][-1] == "Action"


def test_garbage_file_handled():
    db.reset_and_seed()
    out = []
    list(run_pipeline("notes.pdf", b"hello this is not a pdf", ctx_out=out))
    assert out[0].decision.status == "HUMAN_REVIEW"


def test_unknown_vendor():
    db.reset_and_seed()
    c = run("06_blocked_vendor.pdf", {"vendor_gstin": "07AAACX9999X1Z9", "vendor_name": "Xyz Traders"})
    assert "VENDOR_UNKNOWN" in c.decision.reason_codes and c.decision.status == "HUMAN_REVIEW"


def test_review_override_counts_toward_po_billing():
    from core.pipeline.orchestrator import apply_review
    db.reset_and_seed()
    c = run("05_bank_change_acme.pdf")
    assert db.get_billed_to_date("PO-2026-0104") == 0
    apply_review(c.run_id, "OVERRIDDEN_APPROVE", "Called vendor on file number; change confirmed")
    assert db.get_billed_to_date("PO-2026-0104") == Decimal("30000.00")
    assert db.get_run(c.run_id)["review_status"] == "OVERRIDDEN_APPROVE"


def test_self_check_corrects_scan_misread():
    """Vision reads 2,00,000 as 20,00,000; GST math exposes it; re-read fixes it."""
    db.reset_and_seed()
    calls = []
    def flaky(ctx, recheck=None):
        data, meta = _real_fake(ctx)
        calls.append(bool(recheck))
        if not recheck:   # first read: extra zero everywhere except tax
            data.update(taxable_value="2000000", total_amount="2036000",
                        line_items=[{"description": "Managed cloud hosting - November 2026", "quantity": 1,
                                     "unit_price": 2000000, "amount": 2000000}])
        return data, meta
    extract._llm_extract = flaky
    out = []
    events = list(run_pipeline("03_scanned_duplicate.pdf",
                               (config.SAMPLES_DIR / "03_scanned_duplicate.pdf").read_bytes() + b"sc", ctx_out=out))
    extract._llm_extract = _real_fake
    ex = next(e for e in events if e.stage == "Extract" and e.status != "RUNNING")
    assert calls == [False, True]
    assert ex.detail["self_check"]["outcome"] == "corrected on re-read"
    assert out[0].decision.reason_codes == ["DUPLICATE_EXACT"]


def test_unresolved_misread_skips_money_rules():
    """Re-read repeats the same misread: no bogus PO/GST/price flags, just LOW_CONFIDENCE."""
    db.reset_and_seed()
    def stubborn(ctx, recheck=None):
        data, meta = _real_fake(ctx)
        data.update(taxable_value="2000000", total_amount="2036000",
                    line_items=[{"description": "Managed cloud hosting - November 2026", "quantity": 1,
                                 "unit_price": 2000000, "amount": 2000000}])
        return data, meta
    extract._llm_extract = stubborn
    out = []
    list(run_pipeline("03_scanned_duplicate.pdf",
                      (config.SAMPLES_DIR / "03_scanned_duplicate.pdf").read_bytes() + b"st", ctx_out=out))
    extract._llm_extract = _real_fake
    assert out[0].decision.status == "REJECT"
    assert out[0].decision.reason_codes == ["DUPLICATE_EXACT", "LOW_CONFIDENCE"]


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__)
        except Exception as e:
            failed += 1; print("FAIL", fn.__name__, "->", repr(e))
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
