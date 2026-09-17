"""Arithmetic validation for extracted invoice values."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


TOLERANCE = Decimal("0.05")


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value)) if value is not None else None
        return number if number is not None and number.is_finite() else None
    except Exception:
        return None


def _close(left: Decimal | None, right: Decimal | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= TOLERANCE


def validate(document: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    def mismatch(field, actual, expected, reason):
        warnings.append({"field": field, "expected": expected, "actual": actual,
                         "needs_review": True, "reason": reason})

    def resolved(field, operands):
        missing = [path for path, value in operands.items() if value is None]
        if missing:
            warnings.append({"field": field, "status": "unresolved",
                             "missing_operands": missing, "needs_review": True,
                             "reason": "Arithmetic check is unresolved because an operand is missing."})
        return not missing

    def optional(container, key):
        # Preserve the absent optional-charge convention, but an explicit null
        # means unknown, not zero. Required operands never use this default.
        return _decimal(container[key]) if key in container else Decimal("0")

    def complete_sum(values):
        return sum(values, Decimal("0")) if values and all(v is not None for v in values) else None

    items = document.get("items", [])
    for index, item in enumerate(items):
        q, price = (_decimal(item.get(key)) for key in ("quantity", "unit_price"))
        discount = optional(item, "discount")
        taxable, rate, tax, gross = (_decimal(item.get(key)) for key in ("taxable_amount", "tax_rate_percent", "tax_amount", "item_subtotal_including_vat"))
        extended_base = q * price - discount if all(v is not None for v in (q, price, discount)) else None
        # Some ZATCA templates print taxable amount per unit while extending
        # the including-VAT amount by quantity. Accept that explicit convention
        # without rewriting the printed source value.
        expected_taxable = extended_base
        expected_tax = taxable * rate / Decimal("100") if taxable is not None and rate is not None else None
        expected_gross = taxable + tax if taxable is not None and tax is not None else None
        gross_candidates = [expected_gross]
        if q is not None and taxable is not None and tax is not None:
            gross_candidates.append(q * taxable + tax)
            gross_candidates.append(q * (taxable + tax))
        if expected_taxable is not None and taxable is not None and not _close(taxable, expected_taxable):
            if _close(taxable, price):
                expected_taxable = taxable
                expected_tax = taxable * rate / Decimal("100") if rate is not None else None
        for field, actual, expected in (("taxable_amount", taxable, expected_taxable), ("tax_amount", tax, expected_tax)):
            inputs = ({"quantity": q, "unit_price": price, "discount": discount, "taxable_amount": taxable}
                      if field == "taxable_amount" else {"taxable_amount": taxable, "tax_rate_percent": rate, "tax_amount": tax})
            if not resolved(f"items[{index}].{field}", {f"items[{index}].{key}": value for key, value in inputs.items()}):
                continue
            if actual is not None and expected is not None and not _close(actual, expected):
                mismatch(f"items[{index}].{field}", float(actual), float(expected),
                         "Line-item arithmetic does not match the printed quantity, price, discount, or VAT rate.")
        gross_resolved = resolved(f"items[{index}].item_subtotal_including_vat",
                                  {f"items[{index}].{key}": value for key, value in
                                   (("taxable_amount", taxable), ("tax_amount", tax), ("item_subtotal_including_vat", gross))})
        if gross_resolved and not any(_close(gross, candidate) for candidate in gross_candidates if candidate is not None):
            if resolved(f"items[{index}].item_subtotal_including_vat", {f"items[{index}].quantity": q}):
                mismatch(f"items[{index}].item_subtotal_including_vat", float(gross),
                         [float(candidate) for candidate in gross_candidates if candidate is not None],
                         "Line total including VAT does not reconcile with its taxable amount and tax.")
        if item.get("needs_review"):
            warnings.append({"field": f"items[{index}]", "reason": "low OCR confidence", "needs_review": True})
    totals = document.get("totals", {})
    for total_key, item_key in (("total_amount_including_vat", "item_subtotal_including_vat"), ("total_taxable_amount_excluding_vat", "taxable_amount"), ("total_vat", "tax_amount")):
        operands = {f"items[{i}].{item_key}": _decimal(item.get(item_key)) for i, item in enumerate(items)}
        operands[f"totals.{total_key}"] = _decimal(totals.get(total_key))
        if not items:
            operands["items"] = None
        if not resolved(f"totals.{total_key}", operands):
            continue
        expected = sum((_decimal(item.get(item_key)) for item in items), Decimal("0"))
        if total_key == "total_vat":
            extended_tax = complete_sum([_decimal(item.get("quantity")) * _decimal(item.get("tax_amount"))
                                         if _decimal(item.get("quantity")) is not None else None for item in items])
            if _close(_decimal(totals.get(total_key)), extended_tax):
                expected = extended_tax
        if total_key in {"total_taxable_amount_excluding_vat", "total_amount_including_vat"}:
            extended = complete_sum([_decimal(item.get("quantity")) * _decimal(item.get(item_key))
                                     if _decimal(item.get("quantity")) is not None else None for item in items])
            if total_key == "total_taxable_amount_excluding_vat" and not _close(_decimal(totals.get(total_key)), expected):
                expected = extended
            if total_key == "total_amount_including_vat":
                extended_gross = complete_sum([
                    _decimal(item.get("quantity")) * _decimal(item.get("taxable_amount")) + _decimal(item.get("tax_amount"))
                    if all(_decimal(item.get(k)) is not None for k in ("quantity", "taxable_amount", "tax_amount"))
                    else None for item in items])
                if not _close(_decimal(totals.get(total_key)), expected):
                    expected = extended_gross
        actual = _decimal(totals.get(total_key))
        if expected is None:
            resolved(f"totals.{total_key}", {f"items[{i}].{key}": _decimal(item.get(key))
                     for i, item in enumerate(items) for key in ("quantity", "taxable_amount", "tax_amount")})
            continue
        if actual is not None and not _close(actual, expected):
            if total_key == "total_vat" and extended_tax is None:
                resolved(f"totals.{total_key}", {f"items[{i}].quantity": _decimal(item.get("quantity"))
                                               for i, item in enumerate(items)})
                continue
            mismatch(f"totals.{total_key}", float(actual), float(expected),
                     "Document total does not reconcile with the extracted line items.")

    line_bases = [_decimal(item.get("quantity")) * _decimal(item.get("unit_price"))
                  if all(_decimal(item.get(k)) is not None for k in ("quantity", "unit_price")) else None for item in items]
    line_discounts = [optional(item, "discount") for item in items]
    extra_checks = (
        ("total_excluding_vat", complete_sum(line_bases),
         "Total excluding VAT does not match quantity × unit-price values."),
        ("discount", complete_sum(line_discounts),
         "Document discount does not match the extracted line discounts."),
    )
    for key, expected, reason in extra_checks:
        if key not in totals:
            continue
        actual = _decimal(totals.get(key))
        inputs = {f"totals.{key}": actual}
        for i, item in enumerate(items):
            for operand in (("quantity", "unit_price") if key == "total_excluding_vat" else ("discount",)):
                inputs[f"items[{i}].{operand}"] = optional(item, operand) if operand == "discount" else _decimal(item.get(operand))
        if not items:
            inputs["items"] = None
        if not resolved(f"totals.{key}", inputs):
            continue
        if actual is not None and items and not _close(actual, expected):
            mismatch(f"totals.{key}", float(actual), float(expected), reason)

    taxable_total = _decimal(totals.get("total_taxable_amount_excluding_vat"))
    total_vat = _decimal(totals.get("total_vat"))
    rate = _decimal(totals.get("total_vat_rate_percent"))
    if "total_vat_rate_percent" in totals:
        resolved("totals.total_vat_rate_percent", {"totals.total_vat_rate_percent": rate,
                 "totals.total_taxable_amount_excluding_vat": taxable_total, "totals.total_vat": total_vat})
    if rate is not None and taxable_total not in (None, Decimal("0")) and total_vat is not None:
        expected_rate = total_vat * Decimal("100") / taxable_total
        if not _close(rate, expected_rate):
            mismatch("totals.total_vat_rate_percent", float(rate), float(expected_rate),
                     "Overall VAT rate does not reconcile with taxable total and VAT amount.")
    other_charges = optional(totals, "other_charges")
    grand_total = _decimal(totals.get("total_amount_including_vat"))
    if resolved("totals.total_amount_including_vat", {
            "totals.total_amount_including_vat": grand_total, "totals.total_taxable_amount_excluding_vat": taxable_total,
            "totals.total_vat": total_vat, "totals.other_charges": other_charges}):
        # The taxable total is already after discounts on ZATCA invoices.
        expected_grand = taxable_total + total_vat + other_charges
        if not _close(grand_total, expected_grand):
            mismatch("totals.total_amount_including_vat", float(grand_total), float(expected_grand),
                     "Grand total does not reconcile with taxable total, discount, charges, and VAT.")
    return {"passed": not warnings, "needs_review": bool(warnings),
            "warnings": warnings, "field_reviews": list(warnings)}
