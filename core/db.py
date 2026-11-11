"""SQLite layer. The ONLY file that contains SQL (spec section 7)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator

from core import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY, name TEXT, gstin TEXT, state_code TEXT,
    bank_account TEXT, bank_ifsc TEXT, status TEXT,
    contact_email TEXT, contact_phone TEXT
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY, vendor_id TEXT, po_date TEXT, status TEXT,
    total_taxable REAL, description TEXT
);
CREATE TABLE IF NOT EXISTS po_lines (
    po_id TEXT, line_no INTEGER, description TEXT,
    quantity REAL, unit_price REAL, amount REAL
);
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, vendor_id TEXT,
    invoice_number TEXT, invoice_number_norm TEXT, invoice_date TEXT, po_id TEXT,
    taxable_value REAL, total_amount REAL, file_hash TEXT, status TEXT,
    source TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, filename TEXT, file_hash TEXT, started_at TEXT,
    duration_ms INTEGER, decision TEXT, reason_codes TEXT, headline TEXT,
    extracted TEXT, evidence TEXT, explanation TEXT, action TEXT,
    vendor_id TEXT, vendor_name TEXT, invoice_number TEXT, total_amount REAL,
    po_id TEXT, is_scanned INTEGER,
    review_status TEXT DEFAULT 'NONE', review_note TEXT, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS run_steps (
    run_id TEXT, seq INTEGER, stage TEXT, status TEXT,
    duration_ms INTEGER, summary TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS extraction_cache (
    file_hash TEXT PRIMARY KEY, extracted TEXT, created_at TEXT
);
"""

TABLES = ["vendors", "purchase_orders", "po_lines", "invoices", "runs",
          "run_steps", "extraction_cache"]


# ------------------------------------------------------------------ helpers
def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, bytes):
        return f"<{len(o)} bytes>"
    return str(o)


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False)


def loads(s: str | None, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


def norm_invoice_no(s: str | None) -> str | None:
    """'BCS/2026/1102' and 'bcs-2026-1102' -> 'BCS20261102'."""
    if not s:
        return None
    out = "".join(ch for ch in s.upper() if ch.isalnum())
    return out or None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ setup
def init_db() -> None:
    """Create tables; seed if empty (Streamlit Cloud disk is ephemeral)."""
    with connect() as c:
        c.executescript(SCHEMA)
        empty = c.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0
    if empty:
        reset_and_seed()


def reset_and_seed() -> None:
    """Wipe everything and load data/seed/*.json (Golden rule 7)."""
    vendors = json.loads((config.SEED_DIR / "vendors.json").read_text())
    pos = json.loads((config.SEED_DIR / "purchase_orders.json").read_text())
    history = json.loads((config.SEED_DIR / "invoice_history.json").read_text())
    with connect() as c:
        c.executescript(SCHEMA)
        for t in TABLES:
            if t != "extraction_cache":          # keep cache: saves Groq calls
                c.execute(f"DELETE FROM {t}")
        for v in vendors:
            c.execute("""INSERT INTO vendors VALUES (:vendor_id,:name,:gstin,:state_code,
                         :bank_account,:bank_ifsc,:status,:contact_email,:contact_phone)""", v)
        for p in pos:
            total = sum(l["quantity"] * l["unit_price"] for l in p["lines"])
            c.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?)",
                      (p["po_id"], p["vendor_id"], p["po_date"], p["status"], total, p["description"]))
            for l in p["lines"]:
                c.execute("INSERT INTO po_lines VALUES (?,?,?,?,?,?)",
                          (p["po_id"], l["line_no"], l["description"], l["quantity"],
                           l["unit_price"], l["quantity"] * l["unit_price"]))
        for h in history:
            c.execute("""INSERT INTO invoices (run_id, vendor_id, invoice_number, invoice_number_norm,
                         invoice_date, po_id, taxable_value, total_amount, file_hash, status, source, created_at)
                         VALUES (NULL,?,?,?,?,?,?,?,NULL,?, 'SEED', ?)""",
                      (h["vendor_id"], h["invoice_number"], norm_invoice_no(h["invoice_number"]),
                       h["invoice_date"], h["po_id"], h["taxable_value"], h["total_amount"],
                       h["status"], now_iso()))


# ------------------------------------------------------------------ vendors
def get_vendor(vendor_id: str) -> dict | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)))


def get_vendor_by_gstin(gstin: str) -> dict | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM vendors WHERE UPPER(gstin)=UPPER(?)", (gstin,)))


def list_vendors() -> list[dict]:
    with connect() as c:
        return _rows(c.execute("SELECT * FROM vendors ORDER BY vendor_id"))


# ------------------------------------------------------------------ POs
def get_po(po_id: str) -> dict | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM purchase_orders WHERE UPPER(po_id)=UPPER(?)", (po_id,)))


def get_po_lines(po_id: str) -> list[dict]:
    with connect() as c:
        return _rows(c.execute("SELECT * FROM po_lines WHERE po_id=? ORDER BY line_no", (po_id,)))


def list_open_pos(vendor_id: str) -> list[dict]:
    with connect() as c:
        return _rows(c.execute(
            "SELECT * FROM purchase_orders WHERE vendor_id=? AND status='OPEN' ORDER BY po_id",
            (vendor_id,)))


def list_pos() -> list[dict]:
    with connect() as c:
        return _rows(c.execute("""
            SELECT p.*, v.name AS vendor_name FROM purchase_orders p
            LEFT JOIN vendors v ON v.vendor_id = p.vendor_id ORDER BY p.po_id"""))


def get_billed_to_date(po_id: str) -> Decimal:
    q = f"SELECT COALESCE(SUM(taxable_value),0) FROM invoices WHERE po_id=? AND status IN ({','.join('?'*len(config.BILLED_STATUSES))})"
    with connect() as c:
        v = c.execute(q, (po_id, *config.BILLED_STATUSES)).fetchone()[0]
    return Decimal(str(v)).quantize(Decimal("0.01"))


def invoices_for_po(po_id: str) -> list[dict]:
    with connect() as c:
        return _rows(c.execute("SELECT * FROM invoices WHERE po_id=? ORDER BY invoice_date", (po_id,)))


# ------------------------------------------------------------------ invoices / duplicates
def find_invoice_by_hash(file_hash: str) -> dict | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM invoices WHERE file_hash=? LIMIT 1", (file_hash,)))


def find_invoice_by_number(vendor_id: str, invoice_number_norm: str) -> dict | None:
    with connect() as c:
        return _row(c.execute(
            "SELECT * FROM invoices WHERE vendor_id=? AND invoice_number_norm=? LIMIT 1",
            (vendor_id, invoice_number_norm)))


def invoices_for_vendor(vendor_id: str) -> list[dict]:
    with connect() as c:
        return _rows(c.execute("SELECT * FROM invoices WHERE vendor_id=?", (vendor_id,)))


def insert_invoice(**kw) -> None:
    kw.setdefault("created_at", now_iso())
    kw.setdefault("source", "RUN")
    cols = ",".join(kw)
    with connect() as c:
        c.execute(f"INSERT INTO invoices ({cols}) VALUES ({','.join('?'*len(kw))})",
                  tuple(float(v) if isinstance(v, Decimal) else v for v in kw.values()))


def update_invoice_status_for_run(run_id: str, status: str) -> None:
    with connect() as c:
        c.execute("UPDATE invoices SET status=? WHERE run_id=?", (status, run_id))


# ------------------------------------------------------------------ extraction cache
def get_cached_extraction(file_hash: str) -> dict | None:
    with connect() as c:
        r = _row(c.execute("SELECT extracted FROM extraction_cache WHERE file_hash=?", (file_hash,)))
    return loads(r["extracted"]) if r else None


def put_cached_extraction(file_hash: str, extracted: dict) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO extraction_cache VALUES (?,?,?)",
                  (file_hash, dumps(extracted), now_iso()))


# ------------------------------------------------------------------ runs
def insert_run(run_id: str, filename: str, started_at: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO runs (run_id, filename, started_at) VALUES (?,?,?)",
                  (run_id, filename, started_at))


def finalize_run(run_id: str, **fields) -> None:
    """fields: any runs column. dict/list values are JSON-encoded."""
    enc = {k: (dumps(v) if isinstance(v, (dict, list)) else
               float(v) if isinstance(v, Decimal) else v) for k, v in fields.items()}
    sets = ",".join(f"{k}=?" for k in enc)
    with connect() as c:
        c.execute(f"UPDATE runs SET {sets} WHERE run_id=?", (*enc.values(), run_id))


def insert_step(run_id: str, seq: int, stage: str, status: str,
                duration_ms: int, summary: str, detail: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO run_steps VALUES (?,?,?,?,?,?,?)",
                  (run_id, seq, stage, status, duration_ms, summary, dumps(detail)))


def list_runs() -> list[dict]:
    with connect() as c:
        rows = _rows(c.execute("SELECT * FROM runs WHERE decision IS NOT NULL ORDER BY started_at DESC"))
    for r in rows:
        r["reason_codes"] = loads(r["reason_codes"], [])
    return rows


def get_run(run_id: str) -> dict | None:
    with connect() as c:
        r = _row(c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)))
        steps = _rows(c.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY seq", (run_id,)))
    if not r:
        return None
    for k, d in (("reason_codes", []), ("extracted", {}), ("evidence", {}), ("action", {})):
        r[k] = loads(r[k], d)
    for s in steps:
        s["detail"] = loads(s["detail"], {})
    r["steps"] = steps
    return r


def update_review(run_id: str, review_status: str, note: str) -> None:
    with connect() as c:
        c.execute("UPDATE runs SET review_status=?, review_note=?, reviewed_at=? WHERE run_id=?",
                  (review_status, note, now_iso(), run_id))


def review_queue() -> list[dict]:
    with connect() as c:
        rows = _rows(c.execute("""SELECT * FROM runs WHERE review_status='PENDING'
                                  ORDER BY started_at DESC"""))
    for r in rows:
        r["reason_codes"] = loads(r["reason_codes"], [])
        r["evidence"] = loads(r["evidence"], {})
        r["action"] = loads(r["action"], {})
    return rows
