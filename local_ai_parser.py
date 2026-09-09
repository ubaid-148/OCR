from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from invoice_formatter import parse_invoice


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier": {"type": "object", "properties": {
            "name_ar": {"type": ["string", "null"]}, "name_en": {"type": ["string", "null"]},
            "vat_number": {"type": ["string", "null"]}}, "required": ["name_ar", "name_en", "vat_number"]},
        "invoice": {"type": "object", "properties": {
            "invoice_number": {"type": ["string", "null"]}, "date": {"type": ["string", "null"]},
            "hijri_date": {"type": ["string", "null"]}, "time": {"type": ["string", "null"]},
            "payment_method": {"type": ["string", "null"]}},
            "required": ["invoice_number", "date", "hijri_date", "time", "payment_method"]},
        "customer": {"type": "object", "properties": {
            "name": {"type": ["string", "null"]}, "vat_number": {"type": ["string", "null"]},
            "address": {"type": ["string", "null"]}}, "required": ["name", "vat_number", "address"]},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "line_no": {"type": "integer"}, "item_code": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]}, "quantity": {"type": ["number", "null"]},
            "unit_price": {"type": ["number", "null"]}, "amount": {"type": ["number", "null"]}},
            "required": ["line_no", "item_code", "description", "quantity", "unit_price", "amount"]}},
        "totals": {"type": "object", "properties": {
            "subtotal": {"type": ["number", "null"]}, "discount": {"type": ["number", "null"]},
            "vat_rate": {"type": ["number", "null"]}, "vat_amount": {"type": ["number", "null"]},
            "net_amount": {"type": ["number", "null"]}, "currency": {"type": ["string", "null"]}},
            "required": ["subtotal", "discount", "vat_rate", "vat_amount", "net_amount", "currency"]},
    },
    "required": ["supplier", "invoice", "customer", "items", "totals"],
}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None


def _validate(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    tolerance = Decimal("0.02")
    items = data.get("items") if isinstance(data.get("items"), list) else []
    item_checks = []
    amounts: list[Decimal] = []
    for index, item in enumerate(items):
        qty, price, amount = (_decimal(item.get(key)) for key in ("quantity", "unit_price", "amount"))
        valid = qty is not None and price is not None and amount is not None and abs(qty * price - amount) <= tolerance
        item_checks.append({"line_no": item.get("line_no", index + 1), "valid": valid})
        if amount is not None:
            amounts.append(amount)
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    subtotal, discount, vat, net = (_decimal(totals.get(key)) for key in ("subtotal", "discount", "vat_amount", "net_amount"))
    item_sum = sum(amounts, Decimal("0"))
    subtotal_valid = bool(items) and subtotal is not None and len(amounts) == len(items) and abs(item_sum - subtotal) <= tolerance
    vat_rate = _decimal(totals.get("vat_rate")) or Decimal("15")
    vat_expected = (subtotal * vat_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if subtotal is not None else None
    vat_valid = vat_expected is not None and vat is not None and abs(vat_expected - vat) <= tolerance
    net_expected = subtotal - (discount or Decimal("0")) + vat if subtotal is not None and vat is not None else None
    net_valid = net_expected is not None and net is not None and abs(net_expected - net) <= tolerance
    validation = {
        "item_checks": item_checks, "items_sum": float(item_sum) if items else None,
        "items_calculation_valid": bool(items) and all(check["valid"] for check in item_checks),
        "subtotal_valid": subtotal_valid,
        "vat_expected": float(vat_expected) if vat_expected is not None else None, "vat_valid": vat_valid,
        "net_expected": float(net_expected) if net_expected is not None else None, "net_amount_valid": net_valid,
    }
    required = {
        "supplier.vat_number": data.get("supplier", {}).get("vat_number"),
        "invoice.invoice_number": data.get("invoice", {}).get("invoice_number"),
        "invoice.date": data.get("invoice", {}).get("date"),
        "customer.vat_number": data.get("customer", {}).get("vat_number"),
        "items": items, "totals.subtotal": totals.get("subtotal"),
        "totals.vat_amount": totals.get("vat_amount"), "totals.net_amount": totals.get("net_amount"),
    }
    missing = [key for key, value in required.items() if value in (None, "", [])]
    calculations_valid = all((validation["items_calculation_valid"], subtotal_valid, vat_valid, net_valid))
    needs_review = bool(missing or not calculations_valid)
    quality = {
        "overall_status": "verified" if not needs_review else "needs_review",
        "needs_review": needs_review, "missing_fields": missing,
        "low_confidence_fields": [], "parser": "local_ai",
        "model": OLLAMA_MODEL,
    }
    return validation, quality


def _compact_ocr(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for page in pages:
        width_px = float(page.get("width", 1)) * float(page.get("render_dpi", 200)) / 72
        height_px = float(page.get("height", 1)) * float(page.get("render_dpi", 200)) / 72
        compact.append({
            "page": page.get("page"),
            "words": [{
                "text": word.get("text", ""),
                "confidence": word.get("confidence", 0),
                "x": round(float(word.get("left", 0)) / width_px, 4),
                "y": round(float(word.get("top", 0)) / height_px, 4),
                "w": round(float(word.get("width", 0)) / width_px, 4),
                "h": round(float(word.get("height", 0)) / height_px, 4),
            } for word in page.get("words", [])],
        })
    return compact


def _ask_ollama(pages: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = (
        "Extract this invoice into the required JSON schema. OCR boxes use normalized x/y/w/h coordinates. "
        "Understand English and Arabic label aliases and table geometry. Preserve leading zeros in codes and VAT numbers. "
        "Never invent a value: return null when it is absent or uncertain. Distinguish supplier VAT from customer VAT by labels and position. "
        "Use arithmetic only to disambiguate OCR candidates, not to fabricate missing values. OCR:\n"
        + json.dumps(_compact_ocr(pages), ensure_ascii=False, separators=(",", ":"))
    )
    body = json.dumps({
        "model": OLLAMA_MODEL, "stream": False, "format": INVOICE_SCHEMA,
        "options": {"temperature": 0},
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": "You are a careful bilingual invoice document-understanding parser. Return only schema-valid JSON."},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "60"))) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result.get("message", {}).get("content")
    parsed = json.loads(content) if isinstance(content, str) else content
    if not isinstance(parsed, dict):
        raise ValueError("Local AI returned an invalid invoice object")
    return parsed


def parse_invoice_hybrid(pages: list[dict[str, Any]], source_filename: str, language: str, mode: str = "auto") -> dict[str, Any]:
    fallback = parse_invoice(pages, source_filename, language)
    if mode == "fast" or os.environ.get("USE_LOCAL_AI", "true").lower() in {"false", "0", "no"}:
        fallback["quality"]["parser"] = "spatial_fast"
        fallback["quality"]["local_ai_status"] = "disabled"
        if fallback["quality"]["needs_review"]:
            fallback["quality"]["review_message"] = (
                "Invoice fields are incomplete or failed validation. AI parsing is disabled. "
                "Enable USE_LOCAL_AI in Colab cell 3, rerun cells 3 and 4, and select Balanced. "
                "Compare raw_ocr pages with the source PDF before using these values."
            )
        return fallback
    if not fallback["quality"]["needs_review"] and fallback["data"]["invoice"].get("date"):
        fallback["quality"]["parser"] = "spatial_verified"
        fallback["quality"]["local_ai_status"] = "skipped_verified"
        return fallback
    try:
        data = _ask_ollama(pages)
        data.update({
            "document_type": "invoice", "document_language": language.split("+"),
            "source_filename": source_filename,
        })
        validation, quality = _validate(data)
        data["validation"] = validation
        ai_result = {"data": data, "quality": quality}
        # Never replace a locally verified result with AI output that fails
        # arithmetic or required-field validation. This guard is important for
        # small CPU-friendly models, which can understand layout but still swap
        # nearby numbers.
        if quality["needs_review"] and not fallback["quality"]["needs_review"]:
            fallback["quality"]["parser"] = "spatial_verified_after_local_ai_review"
            fallback["quality"]["local_ai_status"] = "rejected_by_validation"
            return fallback
        return ai_result
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as error:
        fallback["quality"]["parser"] = "spatial_fallback"
        fallback["quality"]["local_ai_error"] = str(error)[:300]
        return fallback
