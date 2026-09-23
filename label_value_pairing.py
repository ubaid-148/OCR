"""Template-tolerant bilingual label/value pairing."""
from __future__ import annotations

from typing import Any

from bbox_grouping import box_geometry, group_rows
from invoice_formatter import contains

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
    return contains(_norm(text), (_norm(label),))


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
    label_ids = {id(box) for box in boxes if any(
        _contains(str(box.get("text", "")), name)
        for names in aliases.values() for name in names)}
    for field, names in aliases.items():
        for label in (box for box in boxes if any(_contains(str(box.get("text", "")), name) for name in names)):
            candidates = []
            for value in boxes:
                if value.get("page", 1) != label.get("page", 1):
                    continue
                if id(value) in label_ids:
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


def invoice_number_candidates(pages):
    """Rank same-page label-aligned identifiers by geometry, then confidence."""
    import re
    from invoice_formatter import DIGIT_TABLE
    pattern = re.compile(r'(?:\binv\.?\s*no\.?|\binvoice\s*(?:number|no\.?|#)|رقم\s*الفاتورة|تسلسل\s*الفاتورة)', re.I)
    candidates = []
    for index, page in enumerate(pages, 1):
        number = page.get('page', index)
        words = page.get('words', [])
        for label in words:
            match = pattern.search(str(label.get('text', '')))
            if not match:
                continue
            lx, ly, lw, lh = box_geometry(label)
            tail = str(label.get('text', ''))[match.end():].strip(' :#.-')
            for word in words:
                text = tail if word is label else str(word.get('text', '')).strip()
                value = text.translate(DIGIT_TABLE)
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/-]{0,31}', value) or not re.search(r'\d', value):
                    continue
                if re.fullmatch(r'\d{1,4}[-/]\d{1,2}[-/]\d{2,4}', value):
                    continue  # Date-shaped values are not invoice identifiers.
                x, y, w, h = box_geometry(word)
                if word is not label:
                    arabic = bool(re.search(r'[\u0600-\u06ff]', match[0]))
                    if (arabic and x+w > lx+lw*.2) or (not arabic and x < lx+lw*.8):
                        continue
                height = max(lh, h, 1)
                dy = abs((y+h/2)-(ly+lh/2))
                gap = max(lx-(x+w), x-(lx+lw), 0)
                if word is not label and (dy > height*.5 or gap > height*12):
                    continue
                score = 0 if word is label else (4*dy+gap)/height
                candidates.append(dict(value=value, page=number, bbox=[x,y,w,h],
                    text=word.get('text'), confidence=word.get('confidence'),
                    source=word.get('source','ocr'), label=label.get('text'),
                    label_bbox=[lx,ly,lw,lh], distance=score))
    candidates.sort(key=lambda c:(c['distance'], -float(c['confidence'] or 0), c['page'], c['bbox'][1], c['bbox'][0], c['value']))
    unique=[]
    for candidate in candidates:
        if not any(c['value']==candidate['value'] and c['page']==candidate['page'] for c in unique):
            unique.append(candidate)
    return unique


def apply_invoice_number_candidates(result, pages):
    candidates = invoice_number_candidates(pages)
    if not candidates:
        return result
    data, quality = result['data'], result['quality']
    selected = candidates[0]
    data.setdefault('invoice', {})['invoice_number'] = selected['value']
    data.setdefault('field_evidence', {})['invoice.invoice_number'] = {
        key:selected[key] for key in ('page','text','confidence','source','bbox')}
    quality['invoice_number_candidates'] = candidates
    if len(candidates)>1:
        quality.update(needs_review=True, overall_status='needs_review')
        quality.setdefault('review_reasons', []).append(
            'Invoice number selected by label alignment/distance: '+selected['value']+
            '; alternate candidate found, not selected: '+', '.join(c['value'] for c in candidates[1:])+'. Verify printed labels and handwritten references.')
    return result
