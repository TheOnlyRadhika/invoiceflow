"""Stage 1 · Ingest: hash the file, read text, decide digital vs scanned."""
import hashlib
import io

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from core import config
from core.models import RunContext, StepResult

IMAGE_EXT = (".png", ".jpg", ".jpeg")


def render_pages(pdf_bytes: bytes, max_pages: int = config.MAX_VISION_PAGES,
                 dpi: int = config.RENDER_DPI) -> list[bytes]:
    """PDF pages -> PNG bytes. Also used by the UI for previews."""
    doc = pdfium.PdfDocument(pdf_bytes)
    out = []
    for i in range(min(len(doc), max_pages)):
        img = doc[i].render(scale=dpi / 72).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _image_to_png(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((2000, 2000))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ingest(ctx: RunContext) -> StepResult:
    ctx.file_hash = hashlib.sha256(ctx.file_bytes).hexdigest()
    name = ctx.filename.lower()

    if name.endswith(IMAGE_EXT):
        ctx.is_scanned, ctx.page_count = True, 1
        ctx.page_images = [_image_to_png(ctx.file_bytes)]
        return StepResult("OK", "Image upload → treated as scanned (vision path)",
                          {"file_hash": ctx.file_hash[:16], "pages": 1, "path": "vision"})

    if not ctx.file_bytes.startswith(b"%PDF"):
        return StepResult("FAIL", "Unreadable file: not a PDF or image", {"file_hash": ctx.file_hash[:16]})

    with pdfplumber.open(io.BytesIO(ctx.file_bytes)) as pdf:
        ctx.page_count = len(pdf.pages)
        ctx.text = "\n\n".join((p.extract_text() or "") for p in pdf.pages[: config.MAX_VISION_PAGES])

    chars = len(ctx.text.strip())
    ctx.is_scanned = chars < config.SCANNED_TEXT_MIN_CHARS
    if ctx.is_scanned:
        ctx.page_images = render_pages(ctx.file_bytes)

    detail = {"file_hash": ctx.file_hash[:16], "pages": ctx.page_count, "text_chars": chars,
              "path": "vision" if ctx.is_scanned else "text"}
    status = "WARN" if ctx.page_count > config.MAX_VISION_PAGES else "OK"
    if status == "WARN":
        detail["note"] = f"Only first {config.MAX_VISION_PAGES} pages processed"
    kind = "Scanned PDF (no text layer) → vision model" if ctx.is_scanned else f"Digital PDF, {chars} chars of text"
    return StepResult(status, f"{kind} · {ctx.page_count} page(s)", detail)
