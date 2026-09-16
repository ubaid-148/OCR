"""Arithmetic validation for extracted invoice values."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


TOLERANCE = Decimal("0.05")


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except Exception:
        return None


def _close(left: Decimal | None, right: Decimal | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= TOLERANCE


def validate(document: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    items = document.get("items", [])
    for index, item in enumerate(items):
        q, price, discount = (_decimal(item.get(key)) for key in ("quantity", "unit_price", "discount"))
        taxable, rate, tax, gross = (_decimal(item.get(key)) for key in ("taxable_amount", "tax_rate_percent", "tax_amount", "item_subtotal_including_vat"))
        extended_base = q * price - (discount or Decimal("0")) if q is not None and price is not None else None
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
            if actual is not None and expected is not None and not _close(actual, expected):
                warnings.append({"field": f"items[{index}].{field}", "expected": float(expected), "actual": float(actual), "needs_review": True})
        if gross is not None and gross_candidates and not any(_close(gross, candidate) for candidate in gross_candidates if candidate is not None):
            warnings.append({"field": f"items[{index}].item_subtotal_including_vat", "expected": [float(candidate) for candidate in gross_candidates if candidate is not None], "actual": float(gross), "needs_review": True})
        if item.get("needs_review"):
            warnings.append({"field": f"items[{index}]", "reason": "low OCR confidence", "needs_review": True})
    totals = document.get("totals", {})
    for total_key, item_key in (("total_amount_including_vat", "item_subtotal_including_vat"), ("total_taxable_amount_excluding_vat", "taxable_amount"), ("total_vat", "tax_amount")):
        expected = sum((_decimal(item.get(item_key)) or Decimal("0") for item in items), Decimal("0"))
        if total_key == "total_vat":
            extended_tax = sum(((_decimal(item.get("quantity")) or Decimal("1")) * (_decimal(item.get("tax_amount")) or Decimal("0")) for item in items), Decimal("0"))
            if _close(_decimal(totals.get(total_key)), extended_tax):
                expected = extended_tax
        if total_key in {"total_taxable_amount_excluding_vat", "total_amount_including_vat"}:
            extended = sum(((_decimal(item.get("quantity")) or Decimal("1")) * (_decimal(item.get(item_key)) or Decimal("0")) for item in items), Decimal("0"))
            if total_key == "total_taxable_amount_excluding_vat" and not _close(_decimal(totals.get(total_key)), expected):
                expected = extended
            if total_key == "total_amount_including_vat":
                extended_gross = sum(
                    ((_decimal(item.get("quantity")) or Decimal("1")) * (_decimal(item.get("taxable_amount")) or Decimal("0")))
                    + (_decimal(item.get("tax_amount")) or Decimal("0")) for item in items
                )
                if not _close(_decimal(totals.get(total_key)), expected):
                    expected = extended_gross
        actual = _decimal(totals.get(total_key))
        if actual is not None and not _close(actual, expected):
            warnings.append({"field": f"totals.{total_key}", "expected": float(expected), "actual": float(actual), "needs_review": True})
    return {"passed": not warnings, "needs_review": bool(warnings), "warnings": warnings}