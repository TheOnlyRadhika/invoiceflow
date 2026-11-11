"""Central settings. Every threshold and model name lives here (Golden rule 6)."""
import os
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEED_DIR = DATA_DIR / "seed"
SAMPLES_DIR = ROOT / "samples"
DB_PATH = Path(os.environ.get("INVOICEFLOW_DB", DATA_DIR / "invoiceflow.db"))


def _secret(name: str, default: str | None = None) -> str | None:
    """Env var first, then Streamlit secrets (works locally and on Streamlit Cloud)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


# ---- LLM (Groq) ---------------------------------------------------------
# Groq renames models often. Run `python scripts/check_groq.py` to list what
# your key can use, then override via env/secrets if a default is gone.
GROQ_API_KEY = _secret("GROQ_API_KEY")
TEXT_MODEL = _secret("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = _secret("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
LLM_TEMPERATURE = 0.0
LLM_TIMEOUT_S = 60
MAX_VISION_PAGES = 3          # Groq vision limit per request
RENDER_DPI = 150

# ---- Buyer --------------------------------------------------------------
BUYER_NAME = "Zenith Retail Pvt Ltd"
BUYER_GSTIN = "29AABCZ1234F1Z5"
BUYER_STATE = "29"            # Karnataka
GST_RATE = Decimal("0.18")

# ---- Rules thresholds ---------------------------------------------------
ROUNDING_TOLERANCE_INR = Decimal("1.00")
PRICE_VARIANCE_OK_PCT = Decimal("1.0")      # <=1%  pass
PRICE_VARIANCE_NOTE_PCT = Decimal("3.0")    # 1-3%  APPROVE_WITH_NOTE, >3% HOLD
PO_OVERRUN_TOLERANCE_PCT = Decimal("1.0")
MIN_FIELD_CONFIDENCE = 0.70
VENDOR_NAME_FUZZY_MIN = 88
LINE_MATCH_FUZZY_MIN = 70
PO_INFER_ACCEPT_CONF = 0.80
PO_AGENT_MAX_TURNS = 4
DUP_DATE_WINDOW_DAYS = 7
SCANNED_TEXT_MIN_CHARS = 50
GSTIN_REGEX = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"

CRITICAL_FIELDS = ["invoice_number", "invoice_date", "vendor_gstin",
                   "taxable_value", "total_amount"]

# Statuses that count toward "billed to date" on a PO
BILLED_STATUSES = ("APPROVE", "APPROVE_WITH_NOTE", "PAID")

# ---- Decisions ----------------------------------------------------------
DECISION_PRECEDENCE = ["REJECT", "HUMAN_REVIEW", "HOLD", "APPROVE_WITH_NOTE", "APPROVE"]

REASON_CODES = {
    # code: (decision, short label)
    "VENDOR_BLOCKED": ("REJECT", "Vendor is not approved"),
    "DUPLICATE_EXACT": ("REJECT", "Invoice already processed"),
    "BANK_DETAILS_CHANGED": ("HUMAN_REVIEW", "Bank details differ from vendor master"),
    "DUPLICATE_POSSIBLE": ("HUMAN_REVIEW", "Looks like a possible duplicate"),
    "MISSING_CRITICAL_FIELD": ("HUMAN_REVIEW", "Critical field missing"),
    "LOW_CONFIDENCE": ("HUMAN_REVIEW", "Low extraction confidence"),
    "VENDOR_UNKNOWN": ("HUMAN_REVIEW", "Vendor not found in master"),
    "PO_NOT_FOUND": ("HUMAN_REVIEW", "No matching PO"),
    "PO_INFERRED_LOW_CONF": ("HUMAN_REVIEW", "PO guess not confident enough"),
    "EXTRACTION_FAILED": ("HUMAN_REVIEW", "Could not read the invoice"),
    "STAGE_FAILED": ("HUMAN_REVIEW", "A processing step failed"),
    "PO_OVERRUN": ("HOLD", "Cumulative billing exceeds PO"),
    "PRICE_VARIANCE_HIGH": ("HOLD", "Unit price differs from PO by >3%"),
    "UNMATCHED_LINE": ("HOLD", "Line item not on PO"),
    "ARITHMETIC_MISMATCH": ("HOLD", "Totals don't add up"),
    "TAX_TYPE_MISMATCH": ("HOLD", "Wrong GST type or rate"),
    "PO_CLOSED": ("HOLD", "PO is closed"),
    "PRICE_VARIANCE_MINOR": ("APPROVE_WITH_NOTE", "Small price variance (1-3%)"),
    "PO_INFERRED": ("APPROVE_WITH_NOTE", "PO inferred by agent"),
}
