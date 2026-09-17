"""Template-tolerant bilingual label/value pairing."""
from __future__ import annotations

from typing import Any

from bbox_grouping import box_geometry, group_rows

# Proposed calibration defaults for the approved future ranking design.
# These are NOT empirical results; validate/challenge them against the frozen
# full-corpus baseline. The existing pair_labels algorithm does not use them yet.
# Scores use a 0..1 scale; OCR confidence uses percentage points (0..100).
PAIRING_AMBIGUITY_MARGIN = 0.10
PAIRING_MIN_OCR_CONFIDENCE = 50.0
PAIRING_CONFIDENCE_WEIGHT = 0.60
PAIRING_DISTANCE_WEIGHT = 0.40
# Within an association class: S = confidence_weight * min(label, value)/100
# + distance_weight/(1+d), d=(4*vertical_gap+horizontal_gap)/median_text_height.
# Native text has full recognition reliability, with no fabricated OCR score.

LABELS = {
    "tax_number": ("الرقم الضريبي", "الرقم الضريبى", "vat number", "tax number", "vat no"),
    "building_no": ("المبنى", "building", "building no"),
    "street": ("الشارع", "street"), "district": ("الحي", "district", "neighborhood"),
    "postal_code": ("الرمز البريدي", "postal code", "zip code"),
    "additional_no": ("الرقم الإضافي", "الرقم الاضافي", "additional number", "additional no"),
    "short_address": ("عنوان مختصر", "short address"), "city": ("المدينة", "city"),
    "country": ("الدولة", "country"),
    "invoice_number": ("تسلسل الفاتورة", "invoice number", "invoice no", "serial number"),
    "invoice_date": ("تاريخ الفاتورة", "invoice date"),
    "date_of_supply": ("تاريخ التوريد", "تاريخ المتوريد", "supply date", "date of supply"),
    "reference_no": ("رقم المرجع", "reference no", "reference number"),
    "payment_method": ("طريقة الدفع", "payment method"),
    "total_excluding_vat": ("الإجمالي غير شامل", "total excluding vat", "before tax"),
    "total_taxable_amount_excluding_vat": ("الإجمالي الخاضع", "total taxable amount", "taxable total"),
    "total_vat": ("مجموع ضريبة القيمة المضافة", "total vat", "tax amount"),
    "total_amount_including_vat": ("الإجمالي النهائي", "total amount including vat", "including vat"),
    "currency": ("بالريال", "currency", "sar", "ريال"),
}


def _norm(text: str) -> str:
    return " ".join(str(text).casefold().replace(".", " ").split())


def _contains(text: str, label: str) -> bool:
    actual, wanted = _norm(text), _norm(label)
    return wanted in actual or wanted.replace(" ", "") in actual.replace(" ", "")


def _distance(label: dict[str, Any], value: dict[str, Any]) -> float:
    lx, ly, lw, lh = box_geometry(label)
    vx, vy, vw, vh = box_geometry(value)
    return abs((ly + lh / 2) - (vy + vh / 2)) * 4 + abs((lx + lw / 2) - (vx + vw / 2))


def pair_labels(boxes: list[dict[str, Any]], labels: dict[str, tuple[str, ...]] | None = None,
                max_distance: float = 750) -> dict[str, dict[str, Any]]:
    """Return canonical fields with value box and confidence, never inferred text."""
    aliases = labels or LABELS
    rows = group_rows(boxes)
    row_by_id = {id(box): row for row in rows for box in row}
    result: dict[str, dict[str, Any]] = {}
    for field, names in aliases.items():
        for label in (box for box in boxes if any(_contains(str(box.get("text", "")), name) for name in names)):
            candidates = []
            for value in boxes:
                if value is label or any(_contains(str(value.get("text", "")), name) for name in names):
                    continue
                same_row = value in row_by_id.get(id(label), [])
                distance = _distance(label, value)
                if (same_row or distance <= max_distance) and distance <= max_distance:
                    candidates.append((0 if same_row else 1, distance, value))
            if candidates:
                _, distance, value = min(candidates, key=lambda item: (item[0], item[1]))
                result[field] = {"page": value.get("page", 1), "text": value.get("text"), "confidence": value.get("confidence"),
                                 "bbox": value.get("bbox", value), "label": label.get("text"),
                                 "distance": round(distance, 2), "needs_review": float(value.get("confidence") or 0) < 85}
                break
    return result
