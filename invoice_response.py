"""Fixed, JSON-safe public response boundary for invoice extraction."""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import Any

from visual_invoice import ITEM_NUMBERS, ITEM_TEXT, TEXT_FIELDS, TOTAL_NUMBERS, VAT_NUMBERS


SCHEMA_VERSION = "1.2"
PARTY_FIELDS = {
    "supplier": (*(key for key in TEXT_FIELDS["supplier"] if key not in {"address", "business_type"}),
                 "commercial_registration", "address", "business_type"),
    "customer": (*TEXT_FIELDS["customer"], "commercial_registration"),
}
PUBLIC_TOTAL_FIELDS = (*TOTAL_NUMBERS, "currency", "amount_in_words")
TOP_LEVEL_KEYS = (
    "schema_version", "status", "pipeline_version", "parser", "local_ai_status",
    "local_ai_error", "data", "field_reviews", "review_notes", "ocr_device", "page_orientations",
    "timings_seconds", "unmapped_text", "error",
)


def _json_safe(value: Any) -> Any:
    """Convert arbitrary pipeline output into a finite JSON value."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    return str(value)


def _review(field: Any, reason: Any, **details: Any) -> dict[str, Any]:
    result = {
        "field": str(field or "document"),
        "needs_review": True,
        "reason": str(reason or "Source evidence is insufficient."),
    }
    result.update({key: _json_safe(value) for key, value in details.items() if value is not None})
    return result


def _empty_data() -> dict[str, Any]:
    def values(keys):
        return {key: None for key in keys}
    return {
        "source_filename": None, "document_type": None, "document_type_ar": None,
        "amount_in_words_ar": None, "handwritten_notes": None,
        "supplier": values(PARTY_FIELDS["supplier"]),
        "invoice": values(TEXT_FIELDS["invoice"]),
        "customer": values(PARTY_FIELDS["customer"]),
        "items": [],
        "vat_summary": values((*VAT_NUMBERS, "tax_code")),
        "totals": values(PUBLIC_TOTAL_FIELDS),
        "other_fields": None,
        "bank_details": None,
        "validation": {},
    }


def _validation_reviews(data: dict[str, Any]) -> list[dict[str, Any]]:
    validation = data.get("validation") if isinstance(data.get("validation"), dict) else {}
    reviews = []
    for warning in validation.get("warnings") or []:
        if isinstance(warning, dict):
            reviews.append(_review(warning.get("field"), warning.get("reason") or
                                   "Financial calculation does not match.",
                                   actual=warning.get("actual"), expected=warning.get("expected"),
                                   status=warning.get("status"), missing_operands=warning.get("missing_operands")))
        else:
            reviews.append(_review("document.validation", warning))
    for index, check in enumerate(validation.get("item_checks") or []):
        if isinstance(check, dict) and check.get("valid") is False:
            reviews.append(_review(f"items[{index}]", "Line-item arithmetic does not reconcile."))
    failed_checks = {
        "items_calculation_valid": "items",
        "subtotal_valid": "totals.subtotal",
        "taxable_amount_valid": "totals.taxable_amount",
        "vat_valid": "totals.vat_amount",
        "net_amount_valid": "totals.net_amount",
        "line_vat_sum_matches": "totals.vat_amount",
    }
    for check, field in failed_checks.items():
        if validation.get(check) is False:
            reviews.append(_review(field, f"Financial check failed: {check}."))
    return reviews


def _field_reviews(data: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = _validation_reviews(data)
    for field in quality.get("missing_fields") or []:
        reviews.append(_review(field, "Value is missing or could not be established."))
    for item in quality.get("low_confidence_fields") or []:
        item = item if isinstance(item, dict) else {"field": item}
        reviews.append(_review(item.get("field"), item.get("reason") or
                               "OCR confidence is below the acceptance threshold.",
                               confidence=item.get("confidence")))
    for issue in quality.get("evidence_issues") or []:
        issue = issue if isinstance(issue, dict) else {"reason": issue}
        reviews.append(_review(issue.get("field"), issue.get("reason"),
                               confidence=issue.get("confidence")))
    for issue in quality.get("orientation_issues") or []:
        if not isinstance(issue, dict):
            continue
        fields = issue.get("affected_fields") or [f"pages[{issue.get('page', '?')}]" ]
        for field in fields:
            reviews.append(_review(field, issue.get("reason") or
                                   "Page orientation could not be established confidently."))
    for reason in quality.get("review_reasons") or []:
        reason = str(reason)
        match = re.search(r"(?:supplier|customer|invoice|totals|vat_summary)\.[A-Za-z_]+|"
                          r"items\[\d+\](?:\.[A-Za-z_]+)?", reason)
        reviews.append(_review(match.group(0) if match else "document", reason))
    if quality.get("needs_review") and not reviews:
        reviews.append(_review("document", "Extraction or validation could not establish every value confidently."))
    unique = []
    seen = set()
    for review in reviews:
        marker = json.dumps(review, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if marker not in seen:
            seen.add(marker)
            unique.append(review)
    return unique


def validate_response_schema(response: Any) -> dict[str, Any]:
    """Validate the fixed public envelope without an optional dependency."""
    if not isinstance(response, dict) or tuple(response) != TOP_LEVEL_KEYS:
        raise ValueError("Invoice response has an invalid top-level schema")
    if response["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Invoice response has an unsupported schema version")
    if response["status"] not in {"extracted", "needs_review", "error"}:
        raise ValueError("Invoice response has an invalid status")
    if not isinstance(response["data"], dict):
        raise ValueError("Invoice response data must be an object")
    required_data = _empty_data()
    if tuple(response["data"]) != tuple(required_data):
        raise ValueError("Invoice response data has an invalid schema")
    for section in ("supplier", "invoice", "customer", "vat_summary", "totals", "validation"):
        if not isinstance(response["data"][section], dict):
            raise ValueError(f"Invoice response data.{section} must be an object")
    if not isinstance(response["data"]["items"], list):
        raise ValueError("Invoice response data.items must be an array")
    item_keys = ("line_no", *ITEM_TEXT, *ITEM_NUMBERS)
    if not all(isinstance(item, dict) and tuple(item) == item_keys
               for item in response["data"]["items"]):
        raise ValueError("Invoice response items must be objects")
    expected_sections = {
        "supplier": PARTY_FIELDS["supplier"], "invoice": TEXT_FIELDS["invoice"],
        "customer": PARTY_FIELDS["customer"], "vat_summary": (*VAT_NUMBERS, "tax_code"),
        "totals": PUBLIC_TOTAL_FIELDS,
    }
    for section, keys in expected_sections.items():
        if tuple(response["data"][section]) != tuple(keys):
            raise ValueError(f"Invoice response data.{section} has an invalid schema")
    def text_or_null(value):
        return value is None or isinstance(value, str)
    def number_or_null(value):
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
    for key in ("source_filename", "document_type", "document_type_ar", "amount_in_words_ar"):
        if not text_or_null(response["data"][key]):
            raise ValueError(f"Invoice response data.{key} must be text or null")
    notes = response["data"]["handwritten_notes"]
    if notes is not None and (not isinstance(notes, list) or not all(isinstance(item, str) for item in notes)):
        raise ValueError("Invoice response handwritten_notes must be text array or null")
    for section in ("supplier", "invoice", "customer"):
        if not all(text_or_null(value) for value in response["data"][section].values()):
            raise ValueError(f"Invoice response data.{section} fields must be text or null")
    for item in response["data"]["items"]:
        if not (item["line_no"] is None or
                (isinstance(item["line_no"], (str, int, float)) and not isinstance(item["line_no"], bool))):
            raise ValueError("Invoice response item line_no is invalid")
        if not all(text_or_null(item[key]) for key in ITEM_TEXT):
            raise ValueError("Invoice response item text field is invalid")
        if not all(number_or_null(item[key]) for key in ITEM_NUMBERS):
            raise ValueError("Invoice response item numeric field is invalid")
    if not all(number_or_null(response["data"]["totals"][key]) for key in TOTAL_NUMBERS):
        raise ValueError("Invoice response total numeric field is invalid")
    if not text_or_null(response["data"]["totals"]["currency"]):
        raise ValueError("Invoice response currency is invalid")
    if not all(number_or_null(response["data"]["vat_summary"][key]) for key in VAT_NUMBERS):
        raise ValueError("Invoice response VAT numeric field is invalid")
    if not text_or_null(response["data"]["vat_summary"]["tax_code"]):
        raise ValueError("Invoice response VAT tax code is invalid")
    if not isinstance(response["field_reviews"], list) or not all(
            isinstance(item, dict) and item.get("needs_review") is True and
            isinstance(item.get("field"), str) and isinstance(item.get("reason"), str)
            for item in response["field_reviews"]):
        raise ValueError("Invoice response field_reviews are invalid")
    if not isinstance(response["review_notes"], list) or not all(
            isinstance(item, str) for item in response["review_notes"]):
        raise ValueError("Invoice response review_notes are invalid")
    if not isinstance(response["page_orientations"], list) or not isinstance(response["timings_seconds"], dict):
        raise ValueError("Invoice response diagnostics are invalid")
    if not isinstance(response["unmapped_text"], list) or not all(
            isinstance(entry, dict) and isinstance(entry.get("page"), int) and
            isinstance(entry.get("text"), str) for entry in response["unmapped_text"]):
        raise ValueError("Invoice response unmapped_text must contain page/text entries")
    error = response["error"]
    if response["status"] == "error":
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise ValueError("Error response must include an error object")
    elif error is not None:
        raise ValueError("Successful response error must be null")
    json.dumps(response, ensure_ascii=False, allow_nan=False)
    return response


def clean_invoice_response(payload: Any) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}

    def select(value, keys):
        value = value if isinstance(value, dict) else {}
        return {key: _json_safe(value.get(key)) for key in keys}

    result = _empty_data()
    result.update(select(data, ("source_filename", "document_type", "document_type_ar",
                                "amount_in_words_ar")))
    result["handwritten_notes"] = _json_safe(data.get("handwritten_notes"))
    result["supplier"] = select(data.get("supplier"), PARTY_FIELDS["supplier"])
    result["invoice"] = select(data.get("invoice"), TEXT_FIELDS["invoice"])
    result["customer"] = select(data.get("customer"), PARTY_FIELDS["customer"])
    for party in ("supplier", "customer"):
        result[party]["commercial_registration"] = (result[party].get("commercial_registration")
                                                       or result[party].get("cr_number"))
    result["items"] = [select(item, ("line_no", *ITEM_TEXT, *ITEM_NUMBERS))
                       for item in data.get("items", []) if isinstance(item, dict)]
    result["vat_summary"] = select(data.get("vat_summary"), (*VAT_NUMBERS, "tax_code"))
    result["totals"] = select(data.get("totals"), PUBLIC_TOTAL_FIELDS)
    result["bank_details"] = _json_safe(data.get("bank_details"))
    result["other_fields"] = _json_safe(data.get("other_fields"))
    result["validation"] = _json_safe(data.get("validation") if isinstance(data.get("validation"), dict) else {})

    field_reviews = _field_reviews(data, quality)
    reasons = [str(reason) for reason in quality.get("review_reasons") or []]
    if quality.get("local_ai_status") == "failed" and quality.get("local_ai_error"):
        reasons.append(f"Local AI failed: {quality['local_ai_error']}")
    needs_review = bool(quality.get("needs_review", True) or field_reviews)
    if needs_review and not reasons:
        reasons.append("Check the flagged fields and totals against the PDF.")
    response = {
        "schema_version": SCHEMA_VERSION,
        "status": "needs_review" if needs_review else "extracted",
        "pipeline_version": str(payload.get("pipeline_version", "unknown")),
        "parser": str(quality.get("parser", "unknown")),
        "local_ai_status": str(quality.get("local_ai_status", "not_reported")),
        "local_ai_error": (str(quality["local_ai_error"])[:2000]
                           if quality.get("local_ai_error") else None),
        "data": result,
        "field_reviews": field_reviews,
        "review_notes": list(dict.fromkeys(reasons)),
        "ocr_device": str(payload.get("ocr_device", "unknown")),
        "page_orientations": _json_safe(payload.get("page_orientations") or []),
        "timings_seconds": _json_safe(payload.get("timings_seconds") or {}),
        "unmapped_text": _json_safe(payload.get("unmapped_text", [])),
        "error": None,
    }
    return validate_response_schema(response)


def error_invoice_response(code: Any, message: Any, partial_payload: Any = None) -> dict[str, Any]:
    """Return the same schema on every failure, retaining safe partial data."""
    try:
        response = clean_invoice_response(partial_payload or {
            "data": {}, "quality": {"needs_review": True, "parser": "failed"}})
    except Exception:
        response = {
            "schema_version": SCHEMA_VERSION, "status": "needs_review",
            "pipeline_version": "unknown", "parser": "failed",
            "local_ai_status": "not_reported", "local_ai_error": None, "data": _empty_data(),
            "field_reviews": [], "review_notes": [], "ocr_device": "unknown",
            "page_orientations": [], "timings_seconds": {}, "unmapped_text": [], "error": None,
        }
    response["status"] = "error"
    response["error"] = {"code": _json_safe(code), "message": str(message)[:2000]}
    response["review_notes"] = list(dict.fromkeys([
        *response["review_notes"], "Processing failed; any partial values require manual review."]))
    return validate_response_schema(response)


def response_json_bytes(payload: Any) -> bytes:
    """Serialize finite JSON; replace an invalid payload with a valid error envelope."""
    try:
        safe = _json_safe(payload)
        return json.dumps(safe, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except Exception as error:
        fallback = error_invoice_response(500, f"Response serialization failed: {error}")
        return json.dumps(fallback, ensure_ascii=False, allow_nan=False).encode("utf-8")
