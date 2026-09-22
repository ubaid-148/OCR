"""Colab result boundary: spatial extraction and concise, reviewable fields."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from local_ai_parser import parse_invoice_hybrid


def extract_result(payload, filename, language="eng+ara", *, pdf_path=None, mode="fast", details=None):
    if mode not in {'fast','auto'}:
        raise ValueError('Extraction mode must be fast or auto')
    if mode=='auto':
        if pdf_path is None:
            raise ValueError('General layout extraction requires the original PDF')
        import os
        if os.environ.get('USE_LOCAL_AI','true').lower() in {'false','0','no'}:
            raise RuntimeError('General layout extraction requires vision setup. Rerun Prepare OCR.')
        from visual_invoice import parse_invoice_visual
        parsed=parse_invoice_visual(pdf_path,payload.get('pages',[]),filename,language,mode='auto')
    else:
        parsed = parse_invoice_hybrid(payload.get("pages", []), filename, language, mode="fast")
    if details is not None:
        details.update(parsed)
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
    optional_fields = {
        "invoice.date_of_supply": data.get("invoice", {}).get("date_of_supply"),
        "invoice.payment_method": data.get("invoice", {}).get("payment_method"),
        "supplier.commercial_registration": data.get("supplier", {}).get("commercial_registration"),
        "customer.commercial_registration": data.get("customer", {}).get("commercial_registration"),
        "customer.customer_code": data.get("customer", {}).get("customer_code"),
        "customer.address": data.get("customer", {}).get("address"),
    }
    missing_optional = [field for field, value in optional_fields.items() if value in (None, "", [])]
    if missing_optional:
        notes.append("Optional printed fields not included: " + ", ".join(missing_optional))
    needs_review = quality.get("needs_review", True) or bool(notes)
    if needs_review and not notes:
        notes.append("Check extracted values against the PDF.")
    return {
        "status": "needs_review" if needs_review else "extracted",
        "invoice_number": data.get("invoice", {}).get("invoice_number"),
        "invoice_date": data.get("invoice", {}).get("date"),
        "supplier": select(data.get("supplier", {}), ("name_ar", "name_en", "vat_number")),
        "customer": select(data.get("customer", {}), ("name", "vat_number")),
        "items": [select(item, ("item_code", "description", "quantity", "unit_price", "amount", "vat_amount", "gross_amount",
                    "printed_amount", "printed_vat_amount", "amount_source"))
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
    parser.add_argument('--mode',choices=('fast','auto'),default='fast')
    parser.add_argument('--pdf',type=Path)
    parser.add_argument('--details-output',type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    details={}
    result = extract_result(payload, args.filename, args.language,pdf_path=args.pdf,mode=args.mode,details=details)
    if args.details_output:
        args.details_output.write_text(json.dumps(details,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
