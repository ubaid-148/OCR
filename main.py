"""CLI for converting PaddleOCR evidence into structured invoice JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from label_value_pairing import pair_labels
from pdf_fallback import cross_check
from table_extractor import extract_table
from validator import validate


def _boxes(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    return [dict(box, page=page.get("page", index + 1)) for index, page in enumerate(payload.get("pages", [])) for box in page.get("words", [])]


def _field(pairings: dict[str, dict[str, Any]], name: str) -> Any:
    return pairings.get(name, {}).get("text")


def build_document(payload: Any, pdf_path: str | None = None) -> dict[str, Any]:
    boxes = _boxes(payload)
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
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, help="Optional source PDF for low-confidence cross-checks")
    args = parser.parse_args()
    result = build_document(json.loads(args.input.read_text(encoding="utf-8")), str(args.pdf) if args.pdf else None)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())