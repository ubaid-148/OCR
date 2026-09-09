from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


DIGIT_TABLE = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def normalize(text: str) -> str:
    return " ".join(text.translate(DIGIT_TABLE).replace(",", ".").split())


def center(word: dict[str, Any]) -> tuple[float, float]:
    return (
        float(word.get("left", 0)) + float(word.get("width", 0)) / 2,
        float(word.get("top", 0)) + float(word.get("height", 0)) / 2,
    )


def money(text: str) -> Decimal | None:
    match = re.fullmatch(r"\D*(\d{1,8}(?:\.\d{1,2})?)\D*", normalize(text))
    if not match:
        return None
    try:
        return Decimal(match.group(1)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def number_string(text: str, lengths: set[int]) -> str | None:
    candidates = re.findall(r"(?<!\d)\d+(?!\d)", normalize(text))
    return next((value for value in candidates if len(value) in lengths), None)


def contains(text: str, aliases: tuple[str, ...]) -> bool:
    # Preserve Arabic and word boundaries: "customer" must not match
    # "customers" in the returns policy at the bottom of an invoice.
    tokens = re.findall(r"[^\W_]+", normalize(text).casefold())
    for alias in aliases:
        wanted = re.findall(r"[^\W_]+", normalize(alias).casefold())
        if not wanted:
            continue
        if any(tokens[i:i + len(wanted)] == wanted for i in range(len(tokens))):
            return True
        # OCR sometimes joins a multi-word label, e.g. UnitPrice.
        if len(wanted) > 1 and "".join(wanted) in tokens:
            return True
    return False


def has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", text))


def nearest_value(
    words: list[dict[str, Any]],
    labels: tuple[str, ...],
    predicate,
    *,
    max_distance: float = 450,
) -> dict[str, Any] | None:
    label_words = [word for word in words if contains(str(word.get("text", "")), labels)]
    values = [word for word in words if predicate(str(word.get("text", "")))]
    best: tuple[float, dict[str, Any]] | None = None
    for label in label_words:
        lx, ly = center(label)
        for value in values:
            if value is label:
                continue
            vx, vy = center(value)
            vertical = abs(vy - ly)
            # Invoice forms commonly place a value beside or immediately above a label.
            score = vertical * 3 + abs(vx - lx)
            if vertical <= 100 and score <= max_distance and (best is None or score < best[0]):
                best = (score, value)
    return best[1] if best else None


def field_confidence(word: dict[str, Any] | None) -> float | None:
    return round(float(word.get("confidence", 0)), 2) if word else None


def as_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def parse_invoice(pages: list[dict[str, Any]], source_filename: str, language: str) -> dict[str, Any]:
    words = [word for page in pages for word in page.get("words", [])]
    words.sort(key=lambda word: (float(word.get("top", 0)), float(word.get("left", 0))))

    vat_words = [word for word in words if number_string(str(word.get("text", "")), {15})]
    supplier_vat_word = next(
        (word for word in vat_words if contains(str(word.get("text", "")), ("vatno",))),
        vat_words[0] if vat_words else None,
    )
    customer_vat_word = nearest_value(
        words, ("customer vat",), lambda text: number_string(text, {15}) is not None, max_distance=750
    )
    if customer_vat_word is supplier_vat_word:
        customer_vat_word = vat_words[1] if len(vat_words) > 1 else None

    invoice_word = nearest_value(
        words,
        ("inv no", "invoice no"),
        lambda text: number_string(text, set(range(4, 11))) is not None,
        max_distance=600,
    )
    date_word = nearest_value(
        words, ("date",), lambda text: bool(re.search(r"\d{1,2}/\d{1,2}/\d{4}", normalize(text)))
    )
    dates = [
        (word, re.search(r"\d{1,2}/\d{1,2}/\d{4}", normalize(str(word.get("text", "")))))
        for word in words
    ]
    dates = [(word, match.group()) for word, match in dates if match]
    gregorian = next((value for word, value in dates if word is date_word and value.split("/")[-1].startswith("20")), None)
    if gregorian is None:
        gregorian = next((value for _, value in dates if value.split("/")[-1].startswith("20")), None)
    hijri = next((value for _, value in dates if value.split("/")[-1].startswith("14")), None)
    time_value = next(
        (match.group() for word in words if (match := re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", normalize(str(word.get("text", "")))))),
        None,
    )

    supplier_name_word = next(
        (word for word in words if re.search(r"\b[A-Z][A-Z .]{5,}\b", str(word.get("text", ""))) and "EST" in str(word.get("text", ""))
         and not normalize(str(word.get("text", ""))).lower().startswith("for ")),
        None,
    )
    supplier_name_ar = None
    if supplier_name_word:
        supplier_name_y = center(supplier_name_word)[1]
        arabic_header = [
            word for word in words
            if 0 < supplier_name_y - center(word)[1] <= 100
            and has_arabic(str(word.get("text", "")))
            and not re.search(r"\d", normalize(str(word.get("text", ""))))
        ]
        if arabic_header:
            supplier_name_ar = " ".join(normalize(str(word.get("text", ""))) for word in arabic_header)

    customer_labels = [word for word in words if contains(str(word.get("text", "")), ("customer",))]
    customer_name = None
    if customer_labels:
        customer_y = min(center(word)[1] for word in customer_labels)
        excluded_arabic_labels = (
            "رقم الفاتورة", "الرقم الضريبي", "التاريخ", "العميل", "العنوان", "الموافق"
        )
        customer_candidates = [
            word for word in words
            if abs(center(word)[1] - customer_y) <= 85
            and has_arabic(str(word.get("text", "")))
            and len(normalize(str(word.get("text", "")))) >= 12
            and not any(label in normalize(str(word.get("text", ""))) for label in excluded_arabic_labels)
            and not re.search(r"\d", normalize(str(word.get("text", ""))))
        ]
        if customer_candidates:
            customer_name = normalize(str(max(customer_candidates, key=lambda word: len(str(word.get("text", "")))).get("text", "")))
    payment = next(
        (normalize(str(word.get("text", ""))).title() for word in words if normalize(str(word.get("text", ""))).lower() in {"cash", "card", "credit"}),
        None,
    )

    headers: dict[str, dict[str, Any]] = {}
    header_aliases = {
        "item_code": ("item code",), "description": ("description",), "quantity": ("qty",),
        "unit_price": ("unit price",), "amount": ("amount",),
    }
    for key, aliases in header_aliases.items():
        headers[key] = next((word for word in words if contains(str(word.get("text", "")), aliases)), None)

    items: list[dict[str, Any]] = []
    if headers.get("item_code") and headers.get("amount"):
        header_y = max(center(word)[1] for word in headers.values() if word)
        total_labels = [word for word in words if contains(str(word.get("text", "")), ("total amount", "subtotal"))]
        table_end = min((center(word)[1] for word in total_labels if center(word)[1] > header_y), default=float("inf"))
        column_x = {key: center(word)[0] for key, word in headers.items() if word}
        codes = [
            word for word in words
            if header_y < center(word)[1] < table_end
            and number_string(str(word.get("text", "")), {7})
            and abs(center(word)[0] - column_x["item_code"]) < 180
        ]
        for line_no, code_word in enumerate(codes, 1):
            _, row_y = center(code_word)
            candidates: dict[str, dict[str, Any] | None] = {}
            for key in ("quantity", "unit_price", "amount"):
                if key not in column_x:
                    candidates[key] = None
                    continue
                candidates[key] = min(
                    (word for word in words if header_y < center(word)[1] < table_end and money(str(word.get("text", ""))) is not None),
                    key=lambda word: abs(center(word)[1] - row_y) * 4 + abs(center(word)[0] - column_x[key]),
                    default=None,
                )
                if candidates[key] and (abs(center(candidates[key])[1] - row_y) > 90 or abs(center(candidates[key])[0] - column_x[key]) > 170):
                    candidates[key] = None
            unit_price = money(str(candidates["unit_price"].get("text", ""))) if candidates["unit_price"] else None
            amount = money(str(candidates["amount"].get("text", ""))) if candidates["amount"] else None
            quantity_value = money(str(candidates["quantity"].get("text", ""))) if candidates["quantity"] else None
            quantity = int(quantity_value) if quantity_value is not None and quantity_value == int(quantity_value) else None
            if quantity is None and unit_price and amount:
                inferred = amount / unit_price
                if inferred == inferred.to_integral_value() and 0 < inferred <= 10000:
                    quantity = int(inferred)
            handwritten_notes: list[str] = []
            if "description" in column_x:
                handwritten_notes = [
                    normalize(str(word.get("text", ""))) for word in words
                    if header_y < center(word)[1] < table_end
                    and abs(center(word)[0] - column_x["description"]) < 260
                    and abs(center(word)[1] - row_y) > 100
                    and not contains(str(word.get("text", "")), ("description",))
                    and not re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", normalize(str(word.get("text", ""))))
                    and number_string(str(word.get("text", "")), {7, 15}) is None
                    and len(normalize(str(word.get("text", "")))) >= 3
                ]
            items.append({
                "line_no": line_no,
                "item_code": number_string(str(code_word.get("text", "")), {7}),
                "description": None,
                "quantity": quantity,
                "unit_price": as_float(unit_price),
                "amount": as_float(amount),
                "handwritten_notes": handwritten_notes,
            })

    def total_value(aliases: tuple[str, ...]) -> tuple[Decimal | None, dict[str, Any] | None]:
        word = nearest_value(words, aliases, lambda text: money(text) is not None, max_distance=650)
        return (money(str(word.get("text", ""))) if word else None, word)

    subtotal, subtotal_word = total_value(("total amount", "subtotal"))
    discount, discount_word = total_value(("discount",))
    vat_amount, vat_word = total_value(("vat amount", "value added", "vat"))
    # A plain VAT label in the page header can confuse the generic lookup; use the
    # amount positioned between discount and net when necessary.
    net_amount, net_word = total_value(("net amount", "grand total"))
    if discount_word and net_word:
        dy1, dy2 = center(discount_word)[1], center(net_word)[1]
        between = [word for word in words if dy1 < center(word)[1] < dy2 and money(str(word.get("text", ""))) is not None]
        if between:
            vat_word = max(between, key=lambda word: center(word)[0])
            vat_amount = money(str(vat_word.get("text", "")))

    tolerance = Decimal("0.02")
    item_sum = sum((Decimal(str(item["amount"])) for item in items if item["amount"] is not None), Decimal("0"))
    items_valid = all(
        item["quantity"] is not None and item["unit_price"] is not None and item["amount"] is not None
        and abs(Decimal(item["quantity"]) * Decimal(str(item["unit_price"])) - Decimal(str(item["amount"]))) <= tolerance
        for item in items
    ) if items else False
    subtotal_valid = subtotal is not None and bool(items) and abs(item_sum - subtotal) <= tolerance
    vat_expected = (subtotal * Decimal("0.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if subtotal is not None else None
    vat_valid = vat_expected is not None and vat_amount is not None and abs(vat_expected - vat_amount) <= tolerance
    net_expected = subtotal - (discount or Decimal("0")) + vat_amount if subtotal is not None and vat_amount is not None else None
    net_valid = net_expected is not None and net_amount is not None and abs(net_expected - net_amount) <= tolerance

    tracked = {
        "supplier.vat_number": supplier_vat_word, "invoice.invoice_number": invoice_word,
        "customer.vat_number": customer_vat_word, "totals.subtotal": subtotal_word,
        "totals.vat_amount": vat_word, "totals.net_amount": net_word,
    }
    low_confidence = [
        {"field": field, "confidence": field_confidence(word), "reason": "OCR confidence is below 80%"}
        for field, word in tracked.items() if word is not None and field_confidence(word) < 80
    ]
    missing = [field for field, word in tracked.items() if word is None]
    if not items:
        missing.append("items")
    if not gregorian:
        missing.append("invoice.date")
    needs_review = bool(low_confidence or missing or not all((items_valid, subtotal_valid, vat_valid, net_valid)))

    return {
        "data": {
            "document_type": "invoice",
            "document_language": [part for part in language.split("+") if part],
            "source_filename": source_filename,
            "supplier": {
                "name_ar": supplier_name_ar,
                "name_en": normalize(str(supplier_name_word.get("text", ""))) if supplier_name_word else None,
                "vat_number": number_string(str(supplier_vat_word.get("text", "")), {15}) if supplier_vat_word else None,
            },
            "invoice": {
                "invoice_number": number_string(str(invoice_word.get("text", "")), set(range(4, 11))) if invoice_word else None,
                "date": gregorian, "hijri_date": hijri, "time": time_value, "payment_method": payment,
            },
            "customer": {
                "name": customer_name,
                "vat_number": number_string(str(customer_vat_word.get("text", "")), {15}) if customer_vat_word else None,
                "address": None,
            },
            "items": items,
            "totals": {
                "subtotal": as_float(subtotal), "discount": as_float(discount),
                "vat_rate": 15.0, "vat_amount": as_float(vat_amount),
                "net_amount": as_float(net_amount), "currency": "SAR",
            },
            "validation": {
                "items_calculation_valid": items_valid,
                "items_sum": as_float(item_sum) if items else None,
                "subtotal_valid": subtotal_valid,
                "vat_expected": as_float(vat_expected), "vat_valid": vat_valid,
                "net_expected": as_float(net_expected), "net_amount_valid": net_valid,
            },
        },
        "quality": {
            "overall_status": "verified" if not needs_review else "needs_review",
            "needs_review": needs_review,
            "missing_fields": missing,
            "low_confidence_fields": low_confidence,
        },
    }
