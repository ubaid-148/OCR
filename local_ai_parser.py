from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

from invoice_formatter import parse_invoice
from layout_invoice import parse_layout, table
from invoice_evidence import audit_ai
from document_regions import invoice_words


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value)) if value is not None else None
        return result if result is not None and result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _validate(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if any(not isinstance(data.get(key), dict) for key in ("supplier", "invoice", "customer", "totals")):
        raise ValueError("Invoice sections must be objects")
    tolerance = Decimal("0.02")
    items = data.get("items") if isinstance(data.get("items"), list) else []
    item_checks = []
    amounts: list[Decimal] = []
    line_taxes: list[Decimal | None] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Invoice items must be objects")
        qty, price, amount = (_decimal(item.get(key)) for key in ("quantity", "unit_price", "amount"))
        line_discount = _decimal(item.get('discount')) or Decimal('0')
        gross, line_vat = _decimal(item.get('gross_amount')), _decimal(item.get('vat_amount'))
        direct_amount=(qty is not None and price is not None and amount is not None and
                       abs(qty * price - line_discount - amount) <= tolerance)
        direct_gross=(gross is None or line_vat is None or amount is None or
                      abs(amount + line_vat - gross) <= tolerance)
        direct=direct_amount and direct_gross
        # Some invoices label a column "taxable amount" but print per-unit
        # taxable/VAT values while the gross column is the extended line total.
        # Preserve those printed fields and validate their relationship instead
        # of moving a nearby VAT value into quantity or fabricating an amount.
        per_unit=(not direct and qty is not None and price is not None and amount is not None and
                  gross is not None and line_vat is not None and
                  abs(price-amount)<=tolerance and
                  abs(qty*(amount+line_vat)-line_discount-gross)<=tolerance)
        valid=direct or per_unit
        calculation_mode='per_unit_printed_columns' if per_unit else 'extended_line' if direct else None
        effective_amount=(qty*amount-line_discount if per_unit else amount)
        effective_vat=(qty*line_vat if per_unit and line_vat is not None else line_vat)
        rate = _decimal(item.get("tax_rate"))
        rate_valid = None
        if rate is not None and amount is not None and line_vat is not None:
            expected_line_tax = (amount * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            rate_valid = abs(expected_line_tax - line_vat) <= tolerance
            valid = valid and rate_valid
        item_checks.append({"line_no": item.get("line_no", index + 1), "valid": valid,
                            "tax_rate_valid": rate_valid, "calculation_mode": calculation_mode})
        if amount is not None:
            amounts.append(effective_amount)
        line_taxes.append(effective_vat)
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    subtotal, discount, vat, net = (_decimal(totals.get(key)) for key in ("subtotal", "discount", "vat_amount", "net_amount"))
    item_sum = sum(amounts, Decimal("0"))
    subtotal_valid = bool(items) and subtotal is not None and len(amounts) == len(items) and abs(item_sum - subtotal) <= tolerance
    vat_rate = _decimal(totals.get("vat_rate"))
    other_charges = _decimal(totals.get("other_charges")) or Decimal("0")
    taxable = _decimal(totals.get("taxable_amount"))
    expected_taxable = subtotal - (discount or Decimal("0")) if subtotal is not None else None
    taxable_valid = (abs(taxable - expected_taxable) <= tolerance
                     if taxable is not None and expected_taxable is not None else None)
    tax_base = taxable if taxable is not None else expected_taxable
    vat_expected = (tax_base * vat_rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if tax_base is not None and vat_rate is not None else None
    vat_valid = vat_expected is not None and vat is not None and abs(vat_expected - vat) <= tolerance
    if vat_rate is None and items and all(v is not None for v in line_taxes) and all(
            check.get("tax_rate_valid") is True for check in item_checks):
        # Mixed rates have no single document rate; validate the printed line
        # taxes instead of assuming the default local VAT percentage.
        vat_expected = sum(line_taxes, Decimal("0"))
        vat_valid = vat is not None and abs(vat_expected - vat) <= tolerance
    net_expected = tax_base + vat + other_charges if tax_base is not None and vat is not None else None
    net_valid = net_expected is not None and net is not None and abs(net_expected - net) <= tolerance
    validation = {
        "item_checks": item_checks, "items_sum": float(item_sum) if items else None,
        "items_calculation_valid": bool(items) and all(check["valid"] for check in item_checks),
        "subtotal_valid": subtotal_valid, "taxable_amount_valid": taxable_valid,
        "vat_expected": float(vat_expected) if vat_expected is not None else None, "vat_valid": vat_valid,
        "net_expected": float(net_expected) if net_expected is not None else None, "net_amount_valid": net_valid,
    }
    tax_rounding_issue = bool(items) and all(v is not None for v in line_taxes) and vat is not None and abs(sum(line_taxes, Decimal('0'))-vat)>Decimal('.005')
    validation['line_vat_sum_matches'] = not tax_rounding_issue if items and all(v is not None for v in line_taxes) and vat is not None else None
    required = {
        "supplier.name": data["supplier"].get("name_ar") or data["supplier"].get("name_en"),
        "supplier.vat_number": data.get("supplier", {}).get("vat_number"),
        "invoice.invoice_number": data.get("invoice", {}).get("invoice_number"),
        "invoice.date": data.get("invoice", {}).get("date"),
        "customer.vat_number": data.get("customer", {}).get("vat_number"),
        "customer.name": data["customer"].get("name"),
        "items": items, "totals.subtotal": totals.get("subtotal"),
        "totals.vat_amount": totals.get("vat_amount"), "totals.net_amount": totals.get("net_amount"),
    }
    missing = [key for key, value in required.items() if value in (None, "", [])]
    for index, item in enumerate(items):
        for key in ("description", "quantity", "unit_price", "amount"):
            if item.get(key) in (None, ""):
                missing.append(f"items[{index}].{key}")
    calculations_valid = all((validation["items_calculation_valid"], subtotal_valid, vat_valid, net_valid))
    needs_review = bool(missing or not calculations_valid or tax_rounding_issue or taxable_valid is False)
    quality = {
        "overall_status": "checks_passed" if not needs_review else "needs_review",
        "needs_review": needs_review, "missing_fields": missing,
        "low_confidence_fields": [], "parser": "spatial_fast",
    }
    return validation, quality


def _result_score(result):
    """Prefer checked, complete candidates; do not replace useful rows with blanks."""
    quality = result["quality"]
    checks = result["data"].get("validation", {})
    valid_rows = sum(bool(item.get("valid")) for item in checks.get("item_checks", []))
    return (not quality["needs_review"],
            sum(checks.get(key) is True for key in ("subtotal_valid", "vat_valid", "net_amount_valid")),
            valid_rows, -len(quality.get("missing_fields", [])),
            -len(quality.get("evidence_issues", [])))


def _parse_invoice_hybrid(pages: list[dict[str, Any]], source_filename: str, language: str, mode: str = "auto") -> dict[str, Any]:
    # Legacy geometry is single-page only; never mix coordinates across pages.
    fallback = parse_invoice(pages[:1], source_filename, language)
    checks, quality = _validate(fallback["data"])
    quality["low_confidence_fields"] = fallback["quality"].get("low_confidence_fields", [])
    if quality["low_confidence_fields"]:
        quality.update(needs_review=True, overall_status="needs_review")
    quality["parser"] = "spatial_legacy"
    quality.pop("model", None)
    fallback["data"]["validation"] = checks
    fallback["quality"] = quality
    if len(pages) > 1:
        quality.update(needs_review=True, overall_status="needs_review")
        quality["missing_fields"].append("document.remaining_pages")
    layout = parse_layout(pages, source_filename, language)
    if layout is not None and len(layout["items"]) >= len(fallback["data"]["items"]):
        validation, quality = _validate(layout)
        layout["validation"] = validation
        quality["parser"] = "spatial_layout"
        quality.pop("model", None)
        evidence = dict(layout.get('field_evidence', {}))
        for i,item in enumerate(layout['items']):
            for key,value in item.get('field_evidence',{}).items():
                evidence[f'items[{i}].{key}']=value
        # Layout evidence is the actual selected box, not an occurrence elsewhere.
        issues=[]
        if not evidence:
            issues,evidence=audit_ai(layout,pages)
        validation, quality = _validate(layout)
        layout["validation"] = validation
        quality.update(parser="spatial_layout", evidence_issues=issues, field_evidence=evidence)
        quality.pop("model", None)
        if issues:
            quality.update(needs_review=True, overall_status="needs_review")
        low = [dict(field=key,**ev) for key,value in evidence.items()
               for ev in (value if isinstance(value,list) else [value])
               if ev.get('source')!='native_text' and float(ev.get('confidence') or 0)<80]
        quality["low_confidence_fields"] = low
        if low:
            quality.update(needs_review=True, overall_status="needs_review")
        uncovered = [p.get("page", i+1) for i, p in enumerate(pages)
                     if len(pages) > 1 and not table(p.get("words", []))[0]]
        if uncovered:
            quality.update(needs_review=True, overall_status="needs_review")
            quality["unresolved_pages"] = uncovered
            quality.setdefault("review_reasons", []).append(
                "No item table was established on pages: " + ", ".join(map(str, uncovered)) +
                ". These may be attachments or unparsed continuation pages; check the source.")
        candidate = {"data": layout, "quality": quality}
        if _result_score(candidate) >= _result_score(fallback):
            fallback = candidate
    # The web app's Accuracy mode performs its own original-page vision pass.
    # This module supplies the deterministic spatial draft for both modes.
    fallback["quality"]["parser"] = "spatial_fast"
    fallback["quality"]["local_ai_status"] = "disabled"
    if fallback["quality"]["needs_review"]:
        fallback["quality"]["review_message"] = (
            "Invoice fields are incomplete or failed validation. Compare raw OCR "
            "with the PDF; Accuracy mode can independently read the page image."
        )
    return fallback


def parse_invoice_hybrid(pages, source_filename, language, mode='auto'):
    from invoice_details import add_printed_details
    clean=[];receipts=[]
    for i,page in enumerate(pages):
        words,region=invoice_words(page)
        clean.append(dict(page,words=words,receipt_region=region))
        if region:receipts.append(dict(page=page.get('page',i+1),bbox=region))
    result=_parse_invoice_hybrid(clean,source_filename,language,mode)
    add_printed_details(result['data'],clean)
    for item in result['data'].get('items',[]):
        for key in ('vat_amount','discount','gross_amount','amount_source','printed_amount','printed_vat_amount'):
            item.setdefault(key,None)
        item.setdefault('field_evidence',{})
    quality=result['quality']
    # A printed serial and a handwritten reference may share the same header
    # row. Keep the selected value, but expose the competing source readings.
    identifiers=set()
    for page in pages:
        for retry in page.get('targeted_ocr',{}).get('alternatives',[]):
            if retry.get('kind') != 'invoice_identifier':
                continue
            for word in retry.get('words',[]):
                value=str(word.get('text','')).strip(' .:#')
                if float(word.get('confidence') or 0)>=85 and re.fullmatch(r'\d{3,14}',value):
                    identifiers.add(value)
    if len(identifiers)>1:
        quality.update(needs_review=True,overall_status='needs_review')
        quality.setdefault('review_reasons',[]).append(
            'Multiple invoice identifiers were read: '+', '.join(sorted(identifiers))+
            '. Verify the printed invoice number against handwritten references.')
    if receipts:
        quality.update(needs_review=True,overall_status='needs_review',receipt_regions=receipts)
        quality.setdefault('review_reasons',[]).append('Payment receipt detected; its text is excluded. Missing header fields may be covered and require the unobstructed invoice.')
    if result['data'].get('validation',{}).get('line_vat_sum_matches') is False:
        quality.setdefault('review_reasons',[]).append('Printed line VAT sum differs from document VAT; source amounts are preserved.')
    if any(page.get('targeted_ocr_error') for page in pages):
        quality.update(needs_review=True,overall_status='needs_review')
        quality['targeted_ocr_errors']=[page['targeted_ocr_error'] for page in pages if page.get('targeted_ocr_error')]
    if any(a['kind'] in {'supplier_name','supplier_name_ar','customer_name_ar','description','description_ar'}
           for page in pages for a in page.get('targeted_ocr',{}).get('accepted',[])):
        quality.update(needs_review=True,overall_status='needs_review')
        quality.setdefault('review_reasons',[]).append('Check names and item descriptions recovered by targeted OCR against the PDF.')
    from label_value_pairing import apply_invoice_number_candidates
    apply_invoice_number_candidates(result, pages)
    from mapping_coverage import attach_mapping_coverage
    return attach_mapping_coverage(result, pages)
