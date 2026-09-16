"""Spatial grouping helpers for PaddleOCR text boxes."""
from __future__ import annotations

from typing import Any
import re


def box_geometry(box: dict[str, Any]) -> tuple[float, float, float, float]:
    raw = box.get("bbox", box)
    if isinstance(raw, dict):
        raw = [raw.get(key, 0) for key in ("left", "top", "width", "height")]
    left, top, width, height = (float(value or 0) for value in raw[:4])
    return left, top, width, height


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", text))


def _vertical_distance(box: dict[str, Any], row: list[dict[str, Any]]) -> float:
    _, top, _, height = box_geometry(box)
    center = top + height / 2
    centers = [box_geometry(item)[1] + box_geometry(item)[3] / 2 for item in row]
    return min(abs(center - value) for value in centers) if centers else float("inf")


def sort_row(row: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep numeric/English columns left-to-right and Arabic columns right-to-left."""
    numeric_or_ltr = [item for item in row if not is_arabic(str(item.get("text", "")))]
    arabic = [item for item in row if is_arabic(str(item.get("text", "")))]
    return sorted(numeric_or_ltr, key=lambda item: box_geometry(item)[0]) + sorted(
        arabic, key=lambda item: box_geometry(item)[0], reverse=True
    )


def group_rows(boxes: list[dict[str, Any]], tolerance: float = 18.0) -> list[list[dict[str, Any]]]:
    """Group boxes by vertical center, preserving page boundaries."""
    rows: list[list[dict[str, Any]]] = []
    ordered = sorted(boxes, key=lambda item: (item.get("page", 1), box_geometry(item)[1]))
    for box in ordered:
        page = box.get("page", 1)
        candidates = [row for row in rows if row[0].get("page", 1) == page and _vertical_distance(box, row) <= tolerance]
        if candidates:
            min(candidates, key=lambda row: _vertical_distance(box, row)).append(box)
        else:
            rows.append([box])
    return [sort_row(row) for row in rows]