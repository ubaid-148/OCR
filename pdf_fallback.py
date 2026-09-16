"""Optional source-PDF cross-checks for low-confidence OCR evidence."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def cross_check(pdf_path: str | Path, fields: dict[str, dict[str, Any]], threshold: float = 85.0) -> dict[str, dict[str, Any]]:
    """Extract text from the matching page/region without overwriting OCR values."""
    try:
        import pdfplumber
    except ImportError:
        return {name: {"status": "needs_review", "warning": "Install pdfplumber to enable PDF fallback"}
                for name, evidence in fields.items() if float(evidence.get("confidence") or 0) < threshold}
    result: dict[str, dict[str, Any]] = {}
    with pdfplumber.open(str(pdf_path)) as document:
        for name, evidence in fields.items():
            if float(evidence.get("confidence") or 0) >= threshold:
                continue
            bbox = evidence.get("bbox") or {}
            page_number = int(evidence.get("page") or 1)
            if page_number > len(document.pages):
                result[name] = {"status": "needs_review", "warning": "PDF page not found"}
                continue
            x0 = float(bbox.get("left", 0)); top = float(bbox.get("top", 0))
            x1 = x0 + float(bbox.get("width", 0)); bottom = top + float(bbox.get("height", 0))
            text = document.pages[page_number - 1].crop((x0, top, x1, bottom)).extract_text() or ""
            result[name] = {"status": "cross_checked", "pdf_text": text.strip(), "ocr_text": evidence.get("text"), "needs_review": True}
    return result