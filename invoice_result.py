"""Colab result boundary: spatial extraction and concise, reviewable fields."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from local_ai_parser import parse_invoice_hybrid


def extract_result(payload, filename, language="eng+ara"):
    parsed = parse_invoice_hybrid(payload.get("pages", []), filename, language, mode="fast")
    data, quality = parsed["data"], parsed["quality"]
    def select(source, keys):
        return {key: None if source.get(key) == "" else source.get(key) for key in keys}
    notes = []
    missing = quality.get("missing_fields", [])
    if missing:
        notes.append("Missing or unreadable: " + ", ".join(dict.fromkeys(missing)))
    low = [entry.get("field", "text") for entry in quality.get("low_confidence_fields", [])]
    if low:
        notes.append("Check against PDF: " + ", ".join(dict.fromkeys(low)))
    notes.extend(quality.get("review_reasons", []))
    issues = quality.get("evidence_issues", [])
    if issues:
        notes.append("Unverified fields: " + ", ".join(dict.fromkeys(issue.get("field", "text") for issue in issues)))
    checks = data.get("validation", {})
    if any(checks.get(key) is False for key in ("items_calculation_valid", "subtotal_valid", "vat_valid", "net_amount_valid")):
        notes.append("Item arithmetic or totals could not be fully verified.")
    if quality.get("targeted_ocr_errors"):
        notes.append("Some focused OCR retries failed; check missing values.")
    needs_review = quality.get("needs_review", True) or bool(notes)
    if needs_review and not notes:
        notes.append("Check extracted values against the PDF.")
    return {
        "status": "needs_review" if needs_review else "extracted",
        "invoice_number": data.get("invoice", {}).get("invoice_number"),
        "invoice_date": data.get("invoice", {}).get("date"),
        "supplier": select(data.get("supplier", {}), ("name_ar", "name_en", "vat_number")),
        "customer": select(data.get("customer", {}), ("name", "vat_number")),
        "items": [select(item, ("item_code", "description", "quantity", "unit_price", "amount", "vat_amount", "gross_amount"))
                  for item in data.get("items", [])],
        "totals": select(data.get("totals", {}), ("subtotal", "discount", "vat_rate", "vat_amount", "net_amount", "currency")),
        "review_notes": list(dict.fromkeys(notes)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--filename", default="invoice.pdf")
    parser.add_argument("--language", default="eng+ara")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = extract_result(payload, args.filename, args.language)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
