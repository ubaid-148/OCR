"""Dynamic bilingual line-item table extraction from OCR boxes."""
from __future__ import annotations

from typing import Any
from decimal import Decimal, InvalidOperation
import re

from bbox_grouping import box_geometry, group_rows
import json
from pathlib import Path


COLUMNS = {
    "item_id": ("الكود", "item id", "item code", "code"), "item_name": ("اسم الصنف", "item name", "description"),
    "unit": ("الوحدة", "unit"), "quantity": ("الكمية", "quantity", "qty"), "unit_price": ("سعر الوحدة", "unit price"),
    "discount": ("الخصم", "discount"), "taxable_amount": ("المبلغ الخاضع", "taxable", "taxable amount", "item subtotal"),
    "tax_rate_percent": ("نسبة الضريبة", "tax rate"), "tax_code": ("رمز الضريبة", "tax code"),
    "tax_amount": ("مبلغ الضريبة", "tax amount"), "item_subtotal_including_vat": ("المجموع شامل", "including vat", "subtotal incl"),
}


def _template_match(header: list[dict[str, Any]]) -> str | None:
    labels = " ".join(str(box.get("text", "")).casefold() for box in header)
    registry = Path(__file__).with_name("templates")
    for path in registry.glob("*.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        expected = config.get("header_labels", [])
        score = sum(label.casefold() in labels or label.casefold().replace(" ", "") in labels.replace(" ", "") for label in expected)
        if expected and score / len(expected) >= 0.45:
            return str(config.get("template") or path.stem)
    return None


def normalize_text(text: str) -> str:
    return str(text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")).strip()


def number(text: str) -> float | None:
    value = normalize_text(text).strip(" .:;،")
    value = re.sub(r"(?<=\d),(?=\d)", ".", value)
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", value):
        return None
    try:
        return float(Decimal(value.rstrip("%")))
    except InvalidOperation:
        return None


def clean_tax_code(text: str) -> str:
    return normalize_text(text).strip(" .,:;")


def _matches(text: str, aliases: tuple[str, ...]) -> bool:
    normalized = " ".join(normalize_text(text).casefold().split())
    return any(alias.casefold() in normalized or alias.casefold().replace(" ", "") in normalized.replace(" ", "") for alias in aliases)


def _center(box: dict[str, Any]) -> tuple[float, float]:
    left, top, width, height = box_geometry(box)
    return left + width / 2, top + height / 2


def extract_table(boxes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Find the header row, bucket boxes by detected x-ranges, and preserve evidence."""
    rows = group_rows(boxes)
    header, header_score = None, 0
    for row in rows:
        found = sum(any(_matches(str(box.get("text", "")), aliases) for box in row) for aliases in COLUMNS.values())
        if found > header_score:
            header, header_score = row, found
    if not header or header_score < 3:
        return [], {"needs_review": True, "warning": "Table header row was not detected"}
    template_name = _template_match(header)
    anchors = {}
    for field, aliases in COLUMNS.items():
        anchors[field] = next((box for box in header if _matches(str(box.get("text", "")), aliases)), None)
    anchors = {field: box for field, box in anchors.items() if box}
    column_centers = {field: _center(box)[0] for field, box in anchors.items()}
    sorted_x = sorted(column_centers.items(), key=lambda pair: pair[1])
    boundaries = {}
    for index, (field, x) in enumerate(sorted_x):
        lower = (x + sorted_x[index - 1][1]) / 2 if index else float("-inf")
        upper = (x + sorted_x[index + 1][1]) / 2 if index + 1 < len(sorted_x) else float("inf")
        boundaries[field] = (lower, upper)
    header_y = max(_center(box)[1] for box in header)
    page = header[0].get("page", 1)
    items = []
    for row in group_rows([box for box in boxes if box.get("page", 1) == page and _center(box)[1] > header_y + 10]):
        cells = {}
        for box in row:
            x, _ = _center(box)
            field = next((name for name, bounds in boundaries.items() if bounds[0] <= x < bounds[1]), None)
            if field:
                cells[field] = box
        if not cells or not any(field in cells for field in ("item_id", "item_name", "quantity", "unit_price")):
            continue
        item = {field: None for field in COLUMNS}
        evidence = {}
        for field, box in cells.items():
            text = str(box.get("text", ""))
            item[field] = text if field == "item_name" else (clean_tax_code(text) if field in {"item_id", "unit", "tax_code"} else number(text))
            evidence[field] = box
        item["evidence"] = evidence
        item["needs_review"] = any(float(box.get("confidence") or 0) < 85 for box in cells.values())
        items.append(item)
    metadata = {"header": anchors, "boundaries": boundaries, "template": template_name, "needs_review": False}
    if template_name is None:
        metadata["warning"] = "unknown_template: extracted using generic fallback, please verify item table manually"
        metadata["needs_review"] = True
    return items, metadata