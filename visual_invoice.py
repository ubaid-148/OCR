"""Image-first invoice extraction for Colab; never trains on unverified PDFs."""
from __future__ import annotations

import base64
import io
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from invoice_evidence import audit_ai
from layout_invoice import geometry, header_hint
from local_ai_parser import _validate, parse_invoice_hybrid
from ollama_http import request_json


TEXT_FIELDS = {
    "supplier": ("name_ar", "name_en", "branch", "vat_number", "cr_number", "building_no",
                 "street", "area", "post_code", "additional_no", "short_address", "country", "city"),
    "invoice": ("invoice_number", "date", "date_of_supply", "hijri_date", "time", "ref_no",
                "payment_method", "page"),
    "customer": ("customer_code", "name", "name_ar", "name_en", "vat_number", "cr_number",
                 "building_no", "street", "area", "post_code", "additional_no", "short_address",
                 "country", "city", "address"),
}
ITEM_TEXT = ("item_code", "description", "description_ar", "description_en", "unit", "tax_code")
ITEM_NUMBERS = ("quantity", "unit_price", "discount", "amount", "tax_rate", "vat_amount", "gross_amount")
TOTAL_NUMBERS = ("subtotal", "discount", "other_charges", "taxable_amount", "vat_rate", "vat_amount", "net_amount")
VAT_NUMBERS = ("before_tax", "tax_amount", "inc_tax")


def _object_schema(text_fields=(), number_fields=()):
    properties = {key: {"type": ["string", "null"]} for key in text_fields}
    properties.update({key: {"type": ["number", "null"]} for key in number_fields})
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


FULL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "document_type": {"type": ["string", "null"]},
        "document_type_ar": {"type": ["string", "null"]},
        "handwritten_notes": {"type": "array", "items": {"type": "string"}},
        "supplier": _object_schema(TEXT_FIELDS["supplier"]),
        "invoice": _object_schema(TEXT_FIELDS["invoice"]),
        "customer": _object_schema(TEXT_FIELDS["customer"]),
        "items": {"type": "array", "items": _object_schema(ITEM_TEXT, ITEM_NUMBERS)},
        "amount_in_words_ar": {"type": ["string", "null"]},
        "vat_summary": _object_schema(("tax_code",), VAT_NUMBERS),
        "totals": _object_schema(("currency",), TOTAL_NUMBERS),
        "other_fields": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"label": {"type": "string"}, "value": {"type": "string"},
                           "page": {"type": "integer"}},
            "required": ["label", "value", "page"]}},
    },
}
FULL_SCHEMA["required"] = list(FULL_SCHEMA["properties"])


def _text(value):
    return str(value).strip() if value is not None and str(value).strip() else None


def _number(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(str(value).replace(",", ""))
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def normalize_full(raw: dict[str, Any], filename: str, language: str,
                   page_number: int = 1) -> dict[str, Any]:
    """Keep all supported printed fields; missing fields stay null, never inferred."""
    if not isinstance(raw, dict):
        raise ValueError("Vision response is not a JSON object")
    data: dict[str, Any] = {
        "source_filename": filename, "document_language": language.split("+"),
        "document_type": _text(raw.get("document_type")),
        "document_type_ar": _text(raw.get("document_type_ar")),
        "handwritten_notes": [_text(v) for v in raw.get("handwritten_notes", []) if _text(v)]
        if isinstance(raw.get("handwritten_notes"), list) else [],
        "amount_in_words_ar": _text(raw.get("amount_in_words_ar")),
    }
    for section, keys in TEXT_FIELDS.items():
        source = raw.get(section) if isinstance(raw.get(section), dict) else {}
        data[section] = {key: _text(source.get(key)) for key in keys}
    if not data["customer"]["name"]:
        data["customer"]["name"] = data["customer"]["name_ar"] or data["customer"]["name_en"]
    raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
    data["items"] = []
    for index, source in enumerate(raw_items):
        if not isinstance(source, dict):
            continue
        item = {key: _text(source.get(key)) for key in ITEM_TEXT}
        item.update({key: _number(source.get(key)) for key in ITEM_NUMBERS})
        item["description"] = item["description"] or item["description_ar"] or item["description_en"]
        item["line_no"] = index + 1
        data["items"].append(item)
    vat = raw.get("vat_summary") if isinstance(raw.get("vat_summary"), dict) else {}
    data["vat_summary"] = {key: _number(vat.get(key)) for key in VAT_NUMBERS}
    data["vat_summary"]["tax_code"] = _text(vat.get("tax_code"))
    totals = raw.get("totals") if isinstance(raw.get("totals"), dict) else {}
    data["totals"] = {key: _number(totals.get(key)) for key in TOTAL_NUMBERS}
    data["totals"]["currency"] = _text(totals.get("currency"))
    data["other_fields"] = []
    if isinstance(raw.get("other_fields"), list):
        for field in raw["other_fields"]:
            if isinstance(field, dict) and _text(field.get("label")) and _text(field.get("value")):
                data["other_fields"].append({"label": _text(field["label"]),
                                             "value": _text(field["value"]),
                                             "page": page_number})
    return data


def _table_crop(page, ocr_page: dict[str, Any] | None):
    """Use OCR only to locate a sharper *source image* crop, never as its text."""
    if not ocr_page:
        return None
    words = ocr_page.get("words") or []
    header_y = header_hint(words)
    if header_y is None:
        return None
    glyph_height, center_y = geometry(words)
    coordinate_dpi = float(ocr_page.get("render_dpi") or 200)
    footer_y = min((center_y(word) for word in words
                    if center_y(word) > header_y + 4 * glyph_height and
                    re.search(r"(?i)subtotal|grand total|total excluding|total including|"
                              r"total before|total amount|الإجمالي|المجموع",
                              str(word.get("text", "")))), default=None)
    source_height = float(page.get_height()) * coordinate_dpi / 72
    top = max(0, header_y - 3 * glyph_height)
    bottom = min(source_height, (footer_y + 2 * glyph_height) if footer_y else source_height * .88)
    if bottom - top < 8 * glyph_height:
        return None
    crop_dpi = int(os.environ.get("VISION_TABLE_DPI", "300"))
    if not 200 <= crop_dpi <= 400:
        raise ValueError("VISION_TABLE_DPI must be between 200 and 400")
    high = page.render(scale=crop_dpi / 72).to_pil().convert("RGB")
    ratio = crop_dpi / coordinate_dpi
    crop = high.crop((0, int(top * ratio), high.width,
                      min(high.height, int(bottom * ratio))))
    crop.thumbnail((2800, 1800))
    buffer = io.BytesIO()
    crop.save(buffer, "JPEG", quality=90, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def render_pages(pdf_path: str | Path, ocr_pages: list[dict[str, Any]] | None = None):
    """Render *every* page in memory, without silently dropping later pages."""
    import pypdfium2 as pdfium

    dpi = int(os.environ.get("VISION_RENDER_DPI", "200"))
    if not 120 <= dpi <= 300:
        raise ValueError("VISION_RENDER_DPI must be between 120 and 300")
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            page = document.get_page(index)
            try:
                bitmap = page.render(scale=dpi / 72)
                image = bitmap.to_pil().convert("RGB")
                # Large engineering drawings are bounded per image, not skipped.
                image.thumbnail((2400, 3000))
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=88, optimize=True)
                images = [base64.b64encode(buffer.getvalue()).decode("ascii")]
                try:
                    crop = _table_crop(page, ocr_pages[index] if ocr_pages and index < len(ocr_pages) else None)
                except (KeyError, ValueError, TypeError, IndexError):
                    crop = None  # Full original page remains available.
                if crop:
                    images.append(crop)
                yield index + 1, len(document), images
            finally:
                page.close()
    finally:
        document.close()


def ask_visual(images: list[str] | str, page_number: int, page_count: int) -> dict[str, Any]:
    model = os.environ.get("OLLAMA_MODEL", "qwen3-vl:4b")
    images = [images] if isinstance(images, str) else images
    prompt = (
        f"Read the ORIGINAL invoice image (page {page_number} of {page_count}) and return JSON. "
        "Image 1 is the full page; if there is an image 2, it is a sharper crop of "
        "the SAME page's item table, not a second invoice. "
        "The image, not inferred arithmetic, is the source of truth. Extract every visible field and "
        "EVERY item row in printed top-to-bottom order. Keep Arabic and English names/descriptions "
        "separately when printed; do not invent translations or expand unreadable text. "
        "Distinguish supplier and customer sections/addresses/VAT IDs. Keep invoice date and date of "
        "supply separate. Preserve codes as strings, printed quantity, unit price, taxable amount, "
        "VAT and gross in their own columns. On some invoices taxable/VAT are PER UNIT while gross "
        "is an extended line total: transcribe each printed value without recalculating it. "
        "Use ISO YYYY-MM-DD for unambiguous Gregorian dates; otherwise retain printed text. "
        "Put other labelled fields not covered by the schema in other_fields. "
        "For fields not visible on THIS page use null, for absent rows use an empty array. "
        "Do not put a label, code or address in a name field."
    )
    body = {
        "model": model, "stream": False, "format": FULL_SCHEMA, "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": int(os.environ.get("OLLAMA_NUM_CTX", "16384")),
                    "num_predict": int(os.environ.get("OLLAMA_NUM_PREDICT", "4096"))},
        "messages": [{"role": "user", "content": prompt, "images": images}],
    }
    response = request_json(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat"),
                            body, timeout=float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180")))
    if response.get("error"):
        raise ValueError(f"Ollama vision error: {response['error']}")
    if response.get("done") is False or response.get("done_reason") == "length":
        raise ValueError(f"Vision output for page {page_number} was truncated")
    content = response.get("message", {}).get("content")
    parsed = json.loads(content) if isinstance(content, str) else content
    if not isinstance(parsed, dict):
        raise ValueError(f"Vision output for page {page_number} was not an object")
    return parsed


def merge_pages(parts: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Combine page-local reads; conflicts remain visible for human review."""
    if not parts:
        raise ValueError("PDF contains no pages")
    merged = deepcopy(parts[0])
    conflicts: list[str] = []
    for page_no, part in enumerate(parts[1:], 2):
        for key in ("document_type", "document_type_ar", "amount_in_words_ar"):
            if not merged.get(key):
                merged[key] = part.get(key)
            elif part.get(key) and merged[key] != part[key]:
                conflicts.append(f"Conflicting {key} on page {page_no}")
        for section in (*TEXT_FIELDS, "totals", "vat_summary"):
            for key, value in part[section].items():
                if merged[section].get(key) is None:
                    merged[section][key] = value
                elif value is not None and merged[section][key] != value:
                    conflicts.append(f"Conflicting {section}.{key} on page {page_no}")
        merged["items"].extend(part["items"])
        merged["handwritten_notes"].extend(part["handwritten_notes"])
        merged["other_fields"].extend(part["other_fields"])
    for index, item in enumerate(merged["items"], 1):
        item["line_no"] = index
    return merged, conflicts


def _enrich_spatial_header(fallback: dict[str, Any], visual_data: dict[str, Any]) -> int:
    """Retain non-table visual fields even if the visual item grid is rejected."""
    spatial = fallback["data"]
    added = 0
    for key in ("document_type", "document_type_ar", "amount_in_words_ar"):
        if not spatial.get(key) and visual_data.get(key):
            spatial[key] = visual_data[key]
            added += 1
    for section, keys in TEXT_FIELDS.items():
        spatial_section = spatial.setdefault(section, {})
        for key in keys:
            if not spatial_section.get(key) and visual_data[section].get(key):
                spatial_section[key] = visual_data[section][key]
                added += 1
    for key in ("handwritten_notes", "other_fields"):
        if not spatial.get(key) and visual_data.get(key):
            spatial[key] = visual_data[key]
            added += 1
    return added


def parse_invoice_visual(pdf_path: str | Path, pages: list[dict[str, Any]],
                         filename: str, language: str, mode: str = "auto",
                         progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    # The spatial parser remains a cheap fallback; the old text-only LLM is not
    # used for image-first mode because its column swaps were unsafe.
    spatial_started = perf_counter()
    fallback = parse_invoice_hybrid(pages, filename, language, mode="fast")
    stage_timings = {"spatial_parser": round(perf_counter() - spatial_started, 3)}
    fallback["stage_timings"] = stage_timings
    if mode == "fast" or os.environ.get("USE_LOCAL_AI", "true").lower() in {"false", "0", "no"}:
        return fallback
    try:
        parts = []
        render_started = perf_counter()
        render_seconds = 0.0
        ai_seconds = 0.0
        for number, count, images in render_pages(pdf_path, pages):
            render_seconds += perf_counter() - render_started
            if progress:
                progress(f"Reading original page {number} of {count} with vision AI")
            ai_started = perf_counter()
            parts.append(normalize_full(ask_visual(images, number, count), filename, language, number))
            ai_seconds += perf_counter() - ai_started
            render_started = perf_counter()
        stage_timings.update(visual_render=round(render_seconds, 3),
                             visual_ai=round(ai_seconds, 3))
        data, conflicts = merge_pages(parts)
        validation, quality = _validate(data)
        # Audit a copy: Paddle may miss a correct image reading. Keep such a
        # value visible, but never silently claim it is source-verified.
        issues, evidence = audit_ai(deepcopy(data), pages)
        quality.update(parser="visual_ai", model=os.environ.get("OLLAMA_MODEL", "qwen3-vl:4b"),
                       local_ai_status="vision_evidence_reviewed", evidence_issues=issues,
                       field_evidence=evidence, review_reasons=conflicts,
                       visual_pages=len(parts))
        data["validation"] = validation
        if validation.get("line_vat_sum_matches") is False:
            quality["review_reasons"].append(
                "Printed line VAT sum differs from document VAT; source amounts are preserved.")
        if issues or conflicts:
            quality.update(needs_review=True, overall_status="needs_review")
        visual = {"data": data, "quality": quality, "stage_timings": stage_timings}
        # A visually plausible but column-shifted table must not supersede an
        # independently parsed one. Keep its candidate in detailed debug JSON.
        critical = any(issue.get("field") == "items.row_order" for issue in issues)
        financial = all(validation.get(key) is True for key in (
            "items_calculation_valid", "subtotal_valid", "net_amount_valid"))
        if data["totals"].get("vat_rate") is not None:
            financial = financial and validation.get("vat_valid") is True
        enough_rows = len(data["items"]) >= len(fallback["data"].get("items", []))
        fallback_checks = fallback["data"].get("validation", {})
        fallback_financial = all(fallback_checks.get(key) is True for key in (
            "items_calculation_valid", "subtotal_valid", "net_amount_valid"))
        if critical or not enough_rows or (not financial and fallback_financial):
            added = _enrich_spatial_header(fallback, data)
            if added:
                fallback_checks, refreshed = _validate(fallback["data"])
                fallback["data"]["validation"] = fallback_checks
                fallback["quality"]["missing_fields"] = refreshed["missing_fields"]
            fallback["quality"].update(parser="spatial_after_visual_review",
                                       local_ai_status="rejected_unsafe_vision_result",
                                       needs_review=True, overall_status="needs_review")
            fallback["quality"].setdefault("review_reasons", []).append(
                "Vision result failed item order/row count, or spatial extraction had stronger arithmetic evidence.")
            if added:
                fallback["quality"]["review_reasons"].append(
                    "Missing header fields were filled from the unverified image reading; check them against the PDF.")
            fallback["visual_candidate"] = visual
            return fallback
        if not financial:
            quality.update(needs_review=True, overall_status="needs_review")
            quality.setdefault("review_reasons", []).append(
                "Visual line arithmetic or totals did not fully reconcile; inspect the original PDF.")
        return visual
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as error:
        fallback["quality"].update(parser="spatial_fallback", local_ai_status="failed",
                                   local_ai_error=str(error)[:2000], needs_review=True,
                                   overall_status="needs_review")
        fallback["quality"].setdefault("review_reasons", []).append(
            "Original-page vision extraction failed; spatial OCR fallback needs manual review.")
        return fallback
