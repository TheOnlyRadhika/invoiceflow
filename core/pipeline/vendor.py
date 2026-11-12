"""Stage 5 · Vendor check: find vendor in master, check approval and bank details."""
from core import config, db, fuzzy
from core.models import RunContext, StepResult
from core.pipeline.normalize import digits


def match_vendor(gstin: str | None, name: str | None) -> tuple[dict | None, str, float]:
    if gstin:
        v = db.get_vendor_by_gstin(gstin)
        if v:
            return v, "GSTIN", 100.0
    if name:
        best, score = None, 0.0
        for v in db.list_vendors():
            s = fuzzy.ratio(name, v["name"])
            if s > score:
                best, score = v, s
        if best and score >= config.VENDOR_NAME_FUZZY_MIN:
            return best, "NAME_FUZZY", round(score, 1)
        return None, "NONE", round(score, 1)
    return None, "NONE", 0.0


def bank_changed(vendor: dict, account: str | None, ifsc: str | None) -> bool:
    if not account and not ifsc:
        return False                          # nothing printed: nothing to compare
    acct_diff = bool(account) and digits(account) != digits(vendor["bank_account"])
    ifsc_diff = bool(ifsc) and ifsc.upper() != (vendor["bank_ifsc"] or "").upper()
    return acct_diff or ifsc_diff


def vendor_check(ctx: RunContext) -> StepResult:
    x = ctx.extracted
    v, method, score = match_vendor(x.vendor_gstin, x.vendor_name)
    if not v:
        ctx.add_flag("VENDOR_UNKNOWN", "Vendor check",
                     f"No vendor matches GSTIN {x.vendor_gstin} / name '{x.vendor_name}'", best_score=score)
        return StepResult("WARN", "Vendor not in master data", {"method": method, "best_name_score": score})

    ctx.vendor = v
    detail = {"vendor_id": v["vendor_id"], "vendor_name": v["name"], "matched_by": method,
              "score": score, "status": v["status"]}
    issues = []
    if v["status"] != "APPROVED":
        ctx.add_flag("VENDOR_BLOCKED", "Vendor check", f"{v['name']} is {v['status']}", status=v["status"])
        issues.append(f"vendor {v['status']}")

    if bank_changed(v, x.bank_account_number, x.bank_ifsc):
        on_file = {"account": v["bank_account"], "ifsc": v["bank_ifsc"]}
        on_inv = {"account": x.bank_account_number, "ifsc": x.bank_ifsc}
        ctx.add_flag("BANK_DETAILS_CHANGED", "Vendor check",
                     f"Invoice asks for payment to A/c {x.bank_account_number} ({x.bank_ifsc}); "
                     f"master has A/c {v['bank_account']} ({v['bank_ifsc']})",
                     on_file=on_file, on_invoice=on_inv, verify_phone=v["contact_phone"])
        detail.update(bank_on_file=on_file, bank_on_invoice=on_inv)
        issues.append("bank details changed")
    else:
        detail["bank"] = "matches master" if x.bank_account_number else "not printed on invoice"

    status = "WARN" if issues else "OK"
    summary = f"{v['name']} ({v['vendor_id']}) via {method}" + (f" · ⚠ {', '.join(issues)}" if issues else " · approved, bank OK")
    return StepResult(status, summary, detail)
