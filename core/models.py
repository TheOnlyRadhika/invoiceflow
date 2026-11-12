"""Schemas shared across the pipeline (spec section 8)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from core.money import to_decimal

Status = Literal["RUNNING", "OK", "WARN", "FAIL", "SKIP"]
DecisionStatus = Literal["APPROVE", "APPROVE_WITH_NOTE", "HOLD", "HUMAN_REVIEW", "REJECT"]


# ---------------------------------------------------------------- LLM output
class LineItem(BaseModel):
    description: str | None = None
    hsn_sac: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def _money(cls, v):
        return to_decimal(v)

    @field_validator("hsn_sac", mode="before")
    @classmethod
    def _str(cls, v):
        return None if v is None else str(v)


class ExtractedInvoice(BaseModel):
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    buyer_gstin: str | None = None
    po_reference: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    taxable_value: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str | None = "INR"
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)
    extraction_notes: str | None = None

    @field_validator("taxable_value", "cgst", "sgst", "igst", "total_amount", mode="before")
    @classmethod
    def _money(cls, v):
        return to_decimal(v)

    @field_validator("invoice_number", "invoice_date", "due_date", "vendor_name",
                     "vendor_gstin", "buyer_gstin", "po_reference",
                     "bank_account_number", "bank_ifsc", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return None if s.lower() in ("", "null", "none", "n/a", "na", "-") else s

    @field_validator("field_confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        if not isinstance(v, dict):
            return {}
        out = {}
        for k, x in v.items():
            try:
                out[k] = max(0.0, min(1.0, float(x)))
            except (TypeError, ValueError):
                continue
        return out


# ---------------------------------------------------------------- pipeline
class StepEvent(BaseModel):
    run_id: str
    seq: int
    stage: str
    status: Status
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class Decision(BaseModel):
    status: DecisionStatus
    reason_codes: list[str] = Field(default_factory=list)
    headline: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    approvable_amount: Decimal | None = None


@dataclass
class Flag:
    code: str                      # must be a key of config.REASON_CODES
    stage: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    status: Status
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunContext:
    run_id: str
    filename: str
    file_bytes: bytes
    started_at: str
    file_hash: str | None = None
    page_count: int = 0
    text: str = ""
    is_scanned: bool = False
    page_images: list[bytes] = field(default_factory=list)   # PNG bytes
    extracted: ExtractedInvoice | None = None
    extraction_issues: list[str] = field(default_factory=list)   # numbers that still don't reconcile
    invoice_number_norm: str | None = None
    effective_conf: dict[str, float] = field(default_factory=dict)
    vendor: dict | None = None
    po: dict | None = None
    po_lines: list[dict] = field(default_factory=list)
    billed_to_date: Decimal | None = None
    line_matches: list[dict] = field(default_factory=list)
    match_method: str | None = None          # EXPLICIT | AGENT | DETERMINISTIC
    match_confidence: float | None = None
    agent_trace: list[dict] = field(default_factory=list)
    duplicate_of: dict | None = None
    flags: list[Flag] = field(default_factory=list)
    decision: Decision | None = None
    explanation: str | None = None
    action: dict | None = None

    @classmethod
    def new(cls, filename: str, file_bytes: bytes) -> "RunContext":
        return cls(
            run_id="RUN-" + uuid.uuid4().hex[:8].upper(),
            filename=filename,
            file_bytes=file_bytes,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )

    def add_flag(self, code: str, stage: str, message: str, **evidence) -> None:
        self.flags.append(Flag(code, stage, message, evidence))

    def has_flag(self, code: str) -> bool:
        return any(f.code == code for f in self.flags)
