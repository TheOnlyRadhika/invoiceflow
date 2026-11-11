"""Tonight's smoke test for your Groq key.

Run:  python scripts/check_groq.py
Does: 1) lists models your key can use  2) text JSON call  3) vision JSON call
      on the scanned sample. Tells you what to put in GROQ_VISION_MODEL if the
      default is gone.
"""
import base64
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from core import config  # noqa: E402

try:
    from groq import Groq
except ImportError:
    sys.exit("pip install -r requirements.txt first")

if not config.GROQ_API_KEY:
    sys.exit("No GROQ_API_KEY. Put it in .env  (GROQ_API_KEY=gsk_...)")

client = Groq(api_key=config.GROQ_API_KEY)

print("== 1. Models available to your key ==")
models = sorted(m.id for m in client.models.list().data)
for m in models:
    print("  ", m)
for label, name in (("TEXT", config.TEXT_MODEL), ("VISION", config.VISION_MODEL)):
    print(f"{label} model '{name}':", "FOUND" if name in models else "NOT FOUND <- set GROQ_%s_MODEL" % label)

print("\n== 2. Text JSON call ==")
t = time.time()
r = client.chat.completions.create(
    model=config.TEXT_MODEL, temperature=0,
    response_format={"type": "json_object"},
    messages=[{"role": "user", "content":
               'Return JSON {"invoice_number": ..., "total": ...} from: '
               '"Invoice No: ACME/INV/0057  Total Amount Due Rs. 4,13,000.00"'}],
)
print(f"  {time.time()-t:.2f}s ->", r.choices[0].message.content)

print("\n== 3. Vision JSON call on scanned sample ==")
pdf = config.SAMPLES_DIR / "03_scanned_duplicate.pdf"
if not pdf.exists():
    sys.exit("  run python scripts/generate_invoices.py first")
import pypdfium2 as pdfium  # noqa: E402
img = pdfium.PdfDocument(pdf.read_bytes())[0].render(scale=config.RENDER_DPI / 72).to_pil()
buf = io.BytesIO(); img.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()
t = time.time()
try:
    r = client.chat.completions.create(
        model=config.VISION_MODEL, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": [
            {"type": "text", "text": 'Return JSON {"invoice_number": ..., "vendor_name": ..., "total_amount": ...} from this invoice image.'},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
    )
    print(f"  {time.time()-t:.2f}s ->", r.choices[0].message.content)
    print("\nALL GOOD. Expected invoice_number BCS-2026-1102, total 2,36,000.")
except Exception as e:
    print("  VISION FAILED:", e)
    vision_guess = [m for m in models if any(k in m.lower() for k in ("vision", "scout", "maverick", "qwen", "vl", "muse"))]
    print("  Candidates to try as GROQ_VISION_MODEL:", json.dumps(vision_guess, indent=2))
