"""Attachments pasted or dropped into the chat: PDF, images, text/CSV.

The text is extracted server-side and handed to the planner as extra context, so a visitor can paste
a KPI screenshot or an invoice PDF and ask "which category is this and how did it do last quarter?".

- PDF:   pypdf text extraction (first N pages); scanned PDFs yield little text and say so
- image: a vision-capable model when a key is configured (the chat model, via an image part);
         without a key the image is described as unreadable rather than guessed
- text/CSV/markdown/JSON: taken as is, truncated

Nothing is stored beyond the request; the extracted text travels with the question and is logged
with the run like any other prompt context.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

MAX_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 6
MAX_CHARS = 6000
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_TYPES = {"text/plain", "text/csv", "text/markdown", "application/json"}


@dataclass
class Extracted:
    kind: str  # pdf | image | text
    filename: str
    chars: int
    text: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "filename": self.filename, "chars": self.chars, "text": self.text, "note": self.note}


def extract(filename: str, content_type: str, data: bytes, llm: Any | None = None) -> Extracted:
    if len(data) > MAX_BYTES:
        raise ValueError(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    ct = (content_type or "").split(";")[0].strip().lower()
    name = filename or "attachment"
    if ct == "application/pdf" or name.lower().endswith(".pdf"):
        return _pdf(name, data)
    if ct in IMAGE_TYPES or name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return _image(name, ct or "image/png", data, llm)
    if ct in TEXT_TYPES or name.lower().endswith((".txt", ".csv", ".md", ".json")):
        text = data.decode("utf-8", errors="replace")
        return Extracted("text", name, len(text), _clip(text))
    raise ValueError(f"unsupported attachment type {ct or name}")


def _pdf(name: str, data: bytes) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # pragma: no cover - odd encodings
            pages.append("")
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    note = ""
    if len(reader.pages) > MAX_PDF_PAGES:
        note = f"first {MAX_PDF_PAGES} of {len(reader.pages)} pages read"
    if len(text) < 40:
        note = (note + "; " if note else "") + "little or no text layer (scanned PDF?) - try an image of the page"
    return Extracted("pdf", name, len(text), _clip(text), note)


def _image(name: str, ct: str, data: bytes, llm: Any | None) -> Extracted:
    if llm is None or getattr(llm, "name", "") != "openai":
        return Extracted(
            "image",
            name,
            0,
            "",
            "image reading needs a vision-capable model key; none is configured, so the image was not read",
        )
    try:
        text = describe_image(llm, ct, data)
    except Exception as exc:
        return Extracted("image", name, 0, "", f"image could not be read: {type(exc).__name__}")
    return Extracted("image", name, len(text), _clip(text))


def describe_image(llm: Any, content_type: str, data: bytes) -> str:
    """Ask the chat model to transcribe what matters for a data question: numbers, labels, periods, categories."""
    from langchain_core.messages import HumanMessage

    b64 = base64.b64encode(data).decode()
    msg = HumanMessage(
        content=[
            {
                "type": "text",
                "text": "Transcribe this image for a data analyst: every number with its label, any table as rows, "
                "periods/dates, product categories, states or regions, and any question or KPI title. Plain text, no commentary.",
            },
            {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}},
        ]
    )
    out = llm.client.invoke([msg])
    return str(out.content)


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n[truncated]"


def context_block(attachments: list[dict[str, Any]]) -> str:
    """What the planner and SQL agent see."""
    parts = []
    for a in attachments:
        if a.get("text"):
            parts.append(f"ATTACHMENT {a.get('filename', '')} ({a.get('kind', '')}):\n{a['text']}")
        elif a.get("note"):
            parts.append(f"ATTACHMENT {a.get('filename', '')}: {a['note']}")
    return "\n\n".join(parts)
