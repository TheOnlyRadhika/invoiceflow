"""Run one invoice through the pipeline from the terminal.

    python scripts/run_cli.py samples/01_happy_acme.pdf
    python scripts/run_cli.py samples/04_missing_po_sharma.pdf --verbose
    python scripts/run_cli.py --reset            # restore seed data
    python scripts/run_cli.py --clear-cache      # force fresh Groq extraction
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from core import db  # noqa: E402
from core.pipeline.orchestrator import run_pipeline  # noqa: E402

ICON = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌", "SKIP": "⏭️ "}


def run(path: str, verbose: bool = False) -> str:
    p = Path(path)
    out = []
    print(f"\n━━━ {p.name} ━━━")
    for ev in run_pipeline(p.name, p.read_bytes(), ctx_out=out):
        if ev.status == "RUNNING":
            continue
        print(f"{ICON.get(ev.status, '•')} {ev.seq:>2}. {ev.stage:<17} {ev.duration_ms:>5}ms  {ev.summary}")
        if verbose and ev.detail:
            print("      " + json.dumps(ev.detail, default=str)[:600])
    ctx = out[0]
    d = ctx.decision
    print(f"\n  DECISION: {d.status}   codes={d.reason_codes}")
    print(f"  {d.headline}")
    print(f"  {ctx.explanation}\n")
    return d.status


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--clear-cache" in sys.argv:
        db.init_db()
        db.clear_extraction_cache()
        print("Extraction cache cleared.")
    if "--reset" in sys.argv:
        db.reset_and_seed()
        print("Seed data restored.")
    for a in args:
        run(a, verbose="--verbose" in sys.argv)
