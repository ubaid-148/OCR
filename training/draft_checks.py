"""Review warnings for model drafts; never recalculate or overwrite source values."""
from decimal import Decimal


def draft_warnings(target):
    warnings = []
    for section in ("supplier", "invoice", "customer", "totals"):
        if not any(v not in (None, "", [], {}) for v in target.get(section, {}).values()):
            warnings.append(f"{section}: no extracted values; check the page and header prediction.")
    for index, item in enumerate(target.get("items", [])):
        # These are warnings, not rejection: discounts/per-unit columns can explain differences.
        values = [item.get(k) for k in ("quantity", "unit_price", "amount")]
        if all(type(value) in (int, float) for value in values) and not item.get("discount"):
            quantity, price, amount = map(lambda v: Decimal(str(v)), values)
            if all(v.is_finite() for v in (quantity, price, amount)) and abs(quantity*price-amount) > Decimal("0.02"):
                warnings.append(f"items[{index}].amount: differs from quantity × unit price; check column roles, discounts and per-unit amounts.")
    return warnings
