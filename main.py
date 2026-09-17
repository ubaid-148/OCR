"""CLI for converting PaddleOCR evidence into structured invoice JSON."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from label_value_pairing import pair_labels
from pdf_fallback import cross_check
from table_extractor import extract_table
from validator import validate
from canonical_schema import to_canonical
from llm_extractor import extract_with_ollama
from bbox_grouping import box_geometry
from layout_invoice import parse_layout
from local_ai_parser import parse_invoice_hybrid


CANONICAL_KEYS = ("document_type", "invoice_number", "invoice_serial", "invoice_date", "date_of_supply",
                  "reference_no", "payment_method", "seller", "customer", "items", "totals", "vat_summary",
                  "currency", "page_info", "validation")


def _boxes(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    return [dict(box, page=page.get("page", index + 1)) for index, page in enumerate(payload.get("pages", [])) for box in page.get("words", [])]


def _field(pairings: dict[str, dict[str, Any]], name: str) -> Any:
    return pairings.get(name, {}).get("text")


def build_document(payload: Any, pdf_path: str | None = None) -> dict[str, Any]:
    boxes = _boxes(payload)
    # Use the same page-local table/field parser as the PDF result flow. The
    # generic nearest-label path used to turn footer text into invoice items.
    source_pages = {page.get("page", i + 1): page for i, page in
                    enumerate(payload.get("pages", []))} if isinstance(payload, dict) else {}
    pages_by_number = {page: [] for page in source_pages}
    for box in boxes:
        left, top, width, height = box_geometry(box)
        page = box.get("page", 1)
        pages_by_number.setdefault(page, []).append(dict(
            box, left=left, top=top, width=width, height=height))
    pages = [dict(source_pages.get(page, {}), page=page, words=words)
             for page, words in sorted(pages_by_number.items())]
    language = payload.get("language", "eng+ara") if isinstance(payload, dict) else "eng+ara"
    filename = Path(pdf_path).name if pdf_path else "invoice.pdf"
    if parse_layout(pages, filename, language) is not None:
        parsed = parse_invoice_hybrid(pages, filename, language, mode="fast")
        data, quality = parsed["data"], parsed["quality"]
        invoice = data.get("invoice", {})
        document = dict(data,
            invoice_number=invoice.get("invoice_number"),
            invoice_date=invoice.get("date"),
            date_of_supply=invoice.get("date_of_supply"),
            payment_method=invoice.get("payment_method"),
            currency=data.get("totals", {}).get("currency"),
            customer=dict(data.get("customer", {}),
                          name_ar=data.get("customer", {}).get("name") or None),
            totals=dict(data.get("totals", {}),
                        total_vat_rate_percent=data.get("totals", {}).get("vat_rate")),
            page_info={"page": 1, "total_pages": len(pages)})
        warnings = list(quality.get("review_reasons", []))
        warnings.extend("needs_review: low OCR confidence in " + entry["field"]
                        for entry in quality.get("low_confidence_fields", []))
        warnings.extend("needs_review: " + str(issue)
                        for issue in quality.get("evidence_issues", []))
        warnings.extend("needs_review: OCR retry failed: " + str(error)
                        for error in quality.get("targeted_ocr_errors", []))
        document["validation"] = {"needs_review": quality.get("needs_review", True),
                                  "warnings": warnings}
        return to_canonical(document)
    pairings = pair_labels(boxes)
    items, table_meta = extract_table(boxes)
    fields = ("name_ar", "name_en", "tax_number", "commercial_registration", "building_no", "street", "district", "postal_code", "additional_no", "short_address", "city", "country")
    seller, customer = ({key: None for key in fields} for _ in range(2))
    seller["tax_number"] = _field(pairings, "tax_number")
    totals = {key: None for key in ("total_excluding_vat", "discount", "other_charges", "total_taxable_amount_excluding_vat", "total_vat", "total_vat_rate_percent", "total_amount_including_vat", "amount_in_words_ar")}
    totals["total_excluding_vat"] = _field(pairings, "total_excluding_vat")
    totals["total_taxable_amount_excluding_vat"] = _field(pairings, "total_taxable_amount_excluding_vat")
    totals["total_vat"] = _field(pairings, "total_vat")
    totals["total_amount_including_vat"] = _field(pairings, "total_amount_including_vat")
    document = {"document_type": "tax_invoice", "invoice_number": _field(pairings, "invoice_number"), "invoice_serial": _field(pairings, "invoice_number"), "invoice_date": _field(pairings, "invoice_date"), "date_of_supply": _field(pairings, "date_of_supply"), "reference_no": _field(pairings, "reference_no"), "payment_method": _field(pairings, "payment_method"), "seller": seller, "customer": customer, "items": items, "totals": {key: None for key in ("total_excluding_vat", "discount", "other_charges", "total_taxable_amount_excluding_vat", "total_vat", "total_vat_rate_percent", "total_amount_including_vat", "amount_in_words_ar")}, "vat_summary": {"tax_code": None, "before_tax": None, "tax_amount": None, "including_tax": None}, "currency": None, "validation": {}, "_evidence": {"pairings": pairings, "table": table_meta}}
    document["totals"] = totals
    document["currency"] = _field(pairings, "currency")
    document["validation"] = validate(document)
    if any(entry.get("needs_review") for entry in pairings.values()):
        document["validation"]["needs_review"] = True
    if pdf_path:
        document["_pdf_cross_checks"] = cross_check(pdf_path, pairings)
        if document["_pdf_cross_checks"]:
            document["validation"]["needs_review"] = True
            document["validation"].setdefault("warnings", []).append(
                "needs_review: low-confidence fields were cross-checked against the source PDF"
            )
    return to_canonical(document)


def _flat_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            result.update(_flat_paths(child, f"{prefix}.{key}" if prefix else key))
        return result
    if isinstance(value, list):
        result = {}
        for index, child in enumerate(value):
            result.update(_flat_paths(child, f"{prefix}[{index}]"))
        return result
    return {prefix: value}


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    tokens = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cursor: Any = document
    for index, token in enumerate(tokens):
        if token.startswith("["):
            item_index = int(token[1:-1])
            while len(cursor) <= item_index:
                cursor.append({})
            cursor = cursor[item_index]
            continue
        if index == len(tokens) - 1:
            cursor[token] = value
        else:
            next_token = tokens[index + 1]
            if next_token.startswith("["):
                cursor = cursor.setdefault(token, [])
            else:
                cursor = cursor.setdefault(token, {})


def _same_value(left: Any, right: Any, path: str = "") -> bool:
    if left is None or right is None:
        return left is right
    # Identifiers are text: leading zeroes and large integer IDs are significant.
    numeric_fields = {"quantity", "unit_price", "discount", "taxable_amount",
                      "tax_rate_percent", "tax_amount", "item_subtotal_including_vat",
                      "total_excluding_vat", "other_charges", "total_vat",
                      "total_taxable_amount_excluding_vat", "total_vat_rate_percent",
                      "total_amount_including_vat", "customer_balance", "before_tax", "including_tax"}
    if path.rsplit(".", 1)[-1] not in numeric_fields:
        return str(left).strip() == str(right).strip()
    try:
        return abs(float(left) - float(right)) <= 0.01
    except (TypeError, ValueError):
        return str(left).strip().casefold() == str(right).strip().casefold()


def merge_drafts(rule_based: dict[str, Any], llm_draft: dict[str, Any] | None,
                 warnings: list[str] | None = None) -> dict[str, Any]:
    merged = json.loads(json.dumps(rule_based, ensure_ascii=False))
    validation = rule_based.get("validation", {})
    review = list(validation.get("warnings", [])) + list(warnings or [])
    if validation.get("needs_review") or validation.get("passed") is False:
        review.append("needs_review: rule-based validation requires review")
    if llm_draft is None:
        merged["validation"] = {"passed": not review, "warnings": list(dict.fromkeys(review))}
        return merged
    # Never join table rows by position alone. Missing, repeated or reordered
    # anchors make correspondence ambiguous, even if the arithmetic looks valid.
    rule_items, llm_items = rule_based.get("items", []), llm_draft.get("items", [])
    def anchors(items, key):
        return [str(item.get(key)).strip() if item.get(key) is not None else "" for item in items]
    aligned = False
    if rule_items and len(rule_items) == len(llm_items):
        rule_ids, llm_ids = anchors(rule_items, "item_id"), anchors(llm_items, "item_id")
        if all(rule_ids) and all(llm_ids):
            aligned = rule_ids == llm_ids and len(set(rule_ids)) == len(rule_ids)
        elif not any(rule_ids) and not any(llm_ids):
            names = anchors(rule_items, "item_name")
            aligned = all(names) and names == anchors(llm_items, "item_name") and len(set(names)) == len(names)
    if not aligned and (rule_items or llm_items):
        review.append("needs_review: AI item rows could not be matched uniquely in order; retained rule-based items")
    rule_values, llm_values = _flat_paths(rule_based), _flat_paths(llm_draft)
    for path, llm_value in llm_values.items():
        if path.startswith("validation") or path.startswith("page_info"):
            continue
        if path.startswith("items[") and not aligned:
            continue
        rule_value = rule_values.get(path)
        if rule_value is None and llm_value is not None:
            _set_path(merged, path, llm_value)
            review.append(f"filled_by_llm: {path} — not confirmed by rule-based extraction, verify manually")
        elif rule_value is not None and llm_value is not None and not _same_value(rule_value, llm_value, path):
            review.append(f"field '{path}' mismatch: rule_based={rule_value!r}, llm={llm_value!r} — using rule_based")
    review.append("needs_review: AI draft requires source verification; agreement is not proof of correctness")
    merged["validation"] = {"passed": False, "warnings": list(dict.fromkeys(review))}
    return merged


def _assert_clean_schema(result: dict[str, Any]) -> None:
    if tuple(result) != CANONICAL_KEYS:
        raise RuntimeError(f"Final output schema mismatch: expected {CANONICAL_KEYS}, got {tuple(result)}")
    def check_keys(value: Any) -> None:
        # Review messages and printed text may legitimately mention confidence
        # or bbox. Only actual evidence keys constitute a schema leak.
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"bbox", "confidence"}:
                    raise RuntimeError("Raw OCR evidence leaked into clean output")
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    check_keys(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, help="Optional source PDF for low-confidence cross-checks")
    parser.add_argument("--model", help="Optional local Ollama model name")
    parser.add_argument("--no-llm", action="store_true", help="Use rules-only extraction")
    parser.add_argument("--debug-dir", type=Path, help="Write intermediate OCR/draft files here")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rule_based = build_document(payload, str(args.pdf) if args.pdf else None)
    warnings = []
    llm_draft = None
    if args.model and not args.no_llm:
        try:
            llm_draft = to_canonical(extract_with_ollama(payload, args.model))
        except (RuntimeError, ValueError, json.JSONDecodeError) as error:
            warnings.append(f"llm_unavailable: extraction used rule-based pipeline only ({error})")
    result = to_canonical(merge_drafts(rule_based, llm_draft, warnings))
    canonical_warnings = list(result.get("validation", {}).get("warnings", []))
    arithmetic_validation = validate(result)
    result["validation"] = arithmetic_validation
    # Canonical warnings are strings; structured arithmetic reviews remain in
    # field_reviews. Null operands now reach this path even without a mismatch.
    arithmetic_warnings = [str(warning) for warning in arithmetic_validation["warnings"]]
    result["validation"]["warnings"] = list(dict.fromkeys(canonical_warnings + arithmetic_warnings))
    result["validation"]["passed"] = not result["validation"]["warnings"]
    if warnings:
        result["validation"]["warnings"] = list(dict.fromkeys(warnings + result["validation"].get("warnings", [])))
        result["validation"]["passed"] = False
    _assert_clean_schema(result)
    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)
        (args.debug_dir / "raw_ocr.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.debug_dir / "rule_based_draft.json").write_text(json.dumps(rule_based, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.debug_dir / "llm_draft.json").write_text(json.dumps(llm_draft, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
