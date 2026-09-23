"""Single canonical output boundary for all invoice templates."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any


SELLER_KEYS = ("name_ar", "name_en", "tax_number", "commercial_registration", "building_no",
               "street", "district", "postal_code", "additional_no", "short_address", "city", "country")
CUSTOMER_KEYS = ("name_ar", "customer_code", "tax_number", "commercial_registration", "building_no",
                 "street", "district", "postal_code", "additional_no", "short_address", "city", "country",
                 "customer_balance")
ITEM_KEYS = ("item_id", "item_name", "unit", "quantity", "unit_price", "discount", "taxable_amount",
             "tax_rate_percent", "tax_code", "tax_amount", "item_subtotal_including_vat")
TOTAL_KEYS = ("total_excluding_vat", "discount", "other_charges", "total_taxable_amount_excluding_vat",
              "total_vat", "total_vat_rate_percent", "total_amount_including_vat", "amount_in_words_ar")


def _value(source: dict[str, Any], *names: str) -> Any:
    for name in names:
        if source.get(name) is not None:
            return source[name]
    return None


def _object(source: Any, keys: tuple[str, ...], aliases: dict[str, tuple[str, ...]] | None = None) -> OrderedDict:
    source = source if isinstance(source, dict) else {}
    aliases = aliases or {}
    return OrderedDict((key, _value(source, key, *aliases.get(key, ()))) for key in keys)


def _missing_warnings(value: Any, path: str, warnings: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"validation", "page_info"}:
                continue
            _missing_warnings(child, f"{path}.{key}" if path else key, warnings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _missing_warnings(child, f"{path}[{index}]", warnings)
    elif value is None:
        warnings.append(f"needs_review: missing {path}")


def to_canonical(extracted: dict[str, Any]) -> dict[str, Any]:
    """Map any extraction shape into the fixed schema without inventing values."""
    source = extracted.get("data", extracted) if isinstance(extracted, dict) else {}
    if not isinstance(source, dict):
        source = {}
    seller_source = source.get("seller", source.get("supplier", {}))
    customer_source = source.get("customer", {})
    item_source = source.get("items", source.get("line_items", []))
    totals_source = source.get("totals", {})
    vat_source = source.get("vat_summary", {})
    validation_source = source.get("validation", {})
    warnings = list(validation_source.get("warnings", [])) if isinstance(validation_source, dict) else []
    warnings = [item if isinstance(item, str) else str(item) for item in warnings]
    # The canonical contract exposes passed/warnings, not needs_review. Carry
    # an explicit upstream review request across that boundary even when all
    # fields are populated and arithmetic passed.
    if isinstance(validation_source, dict) and validation_source.get("needs_review"):
        warnings.append("needs_review: upstream validation requires review")
    template_warning = (source.get("_evidence", {}).get("table", {}).get("warning")
                        if isinstance(source.get("_evidence"), dict) else None)
    if template_warning:
        warnings.append(template_warning)

    canonical = OrderedDict([
        ("document_type", "Tax Invoice"),
        ("invoice_number", _value(source, "invoice_number", "invoice_no")),
        ("invoice_serial", _value(source, "invoice_serial", "serial_number")),
        ("invoice_date", _value(source, "invoice_date", "date")),
        ("date_of_supply", _value(source, "date_of_supply", "supply_date")),
        ("reference_no", _value(source, "reference_no", "reference_number")),
        ("payment_method", _value(source, "payment_method", "payment")),
        ("seller", _object(seller_source, SELLER_KEYS, {"tax_number": ("vat_number",), "commercial_registration": ("cr_number", "commercial_reg"), "building_no": ("building",)})),
        ("customer", _object(customer_source, CUSTOMER_KEYS, {"tax_number": ("vat_number",), "commercial_registration": ("cr_number", "commercial_reg"), "building_no": ("building",)})),
        ("items", []),
        ("totals", _object(totals_source, TOTAL_KEYS, {"total_vat": ("vat_amount",), "total_amount_including_vat": ("net_amount",), "total_excluding_vat": ("subtotal",), "total_taxable_amount_excluding_vat": ("taxable_amount",), "total_vat_rate_percent": ("vat_rate",), "amount_in_words_ar": ("amount_in_words",)})),
        ("vat_summary", _object(vat_source, ("tax_code", "before_tax", "tax_amount", "including_tax"), {"tax_amount": ("vat_amount",)})),
        ("currency", _value(source, "currency")),
        ("page_info", OrderedDict([("page", source.get("page_info", {}).get("page", 1) if isinstance(source.get("page_info"), dict) else 1), ("total_pages", source.get("page_info", {}).get("total_pages", len(extracted.get("pages", [])) or 1) if isinstance(source.get("page_info"), dict) else len(extracted.get("pages", [])) or 1)])),
        ("validation", OrderedDict()),
    ])
    for item in item_source if isinstance(item_source, list) else []:
        canonical["items"].append(_object(item, ITEM_KEYS, {
            "item_id": ("item_code",), "item_name": ("description",), "taxable_amount": ("amount",), "tax_rate_percent": ("vat_rate", "tax_rate"),
            "tax_amount": ("vat_amount",), "item_subtotal_including_vat": ("gross_amount", "total_incl_vat"),
        }))
    _missing_warnings(canonical, "", warnings)
    if not canonical["items"]:
        warnings.append("needs_review: missing items")
    # A supplied validation failure or any missing field makes the canonical result review-only.
    passed = bool(validation_source.get("passed", True)) and not warnings
    canonical["validation"] = OrderedDict([("passed", passed), ("warnings", list(dict.fromkeys(warnings)))])
    return dict(canonical)
